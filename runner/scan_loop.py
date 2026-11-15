"""The autonomous scan loop, ported from Pilot 1 (scan_engine.run_scan).

What is the same: the tool policy, the origin lock, the budgets, the repeated-action guard, secret redaction, the
final-map wrap-up and the "always close the browser" safety net.
What is different: the model is reached through the server's AI proxy (this machine holds no AI key), messages use a
provider-neutral format, and the result is returned to the caller instead of written to files.
"""
import asyncio
import json
import time
from datetime import datetime, timezone

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.engines import scan_core as sc
from runner.api import ApiError


def _leaf_reason(exc: BaseException) -> str:
    """Readable cause for errors wrapped in exception groups (the MCP client raises those)."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail[:200]}" if detail else type(exc).__name__


class RetryableScanError(Exception):
    """The scan could not even start talking to the model for a temporary reason; the job can be retried."""


def _tool_schema(tool) -> dict:
    return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}


def fill_unanswered_tool_calls(messages: list) -> None:
    """Neutral-format twin of Pilot 1's helper: answer tool calls left over when the scan stopped mid-turn."""
    last = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "assistant" and messages[i].get("tool_calls")), None)
    if last is None:
        return
    answered = {m.get("tool_call_id") for m in messages[last + 1 :] if m.get("role") == "tool"}
    for call in messages[last]["tool_calls"]:
        if call["id"] not in answered:
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": "Not executed: the scan budget was exhausted."})


def _short(name: str, arguments: dict) -> str:
    target = arguments.get("element") or arguments.get("url") or arguments.get("target") or ""
    return f"{name} {str(target)[:60]}".strip()


async def run_scan(api, job: dict, mcp_url: str, *, report=lambda message: None, cancelled=lambda: False) -> dict:
    payload = job["payload"]
    job_id = job["id"]
    policy = sc.ScanPolicy(payload.get("scan_policy", "INTERACTIVE"))
    application_url = payload.get("application_url", "")
    summary, description, acceptance = payload.get("summary", ""), payload.get("description", ""), payload.get("acceptance_criteria", "")
    known_secrets = sc.extract_known_secrets(summary, description, acceptance)

    started = time.monotonic()
    history: list = []
    status, stop_reason = "FAILED", "Scan did not complete."
    final_output = final_raw = None
    issues: list = []

    def result():
        out_map = sc.scrub_secrets(final_output, known_secrets) if final_output else final_output
        out_raw = sc.scrub_secrets(final_raw, known_secrets) if final_raw else final_raw
        return {
            "status": status,
            "stop_reason": stop_reason,
            "app_map": out_map,
            "raw": out_raw,
            "schema_issues": issues,
            "history": history,
            "tool_call_count": len(history),
            "duration_seconds": int(time.monotonic() - started),
        }

    # Same up-front checks as Pilot 1, before any browser is touched.
    if not sc.is_valid_http_url(application_url):
        stop_reason = "Application URL must be a valid http(s) URL."
        return result()
    if policy == sc.ScanPolicy.INTERACTIVE and not payload.get("test_environment_authorized"):
        stop_reason = (
            "Autonomous execution (INTERACTIVE) requires explicit confirmation that this URL is an authorized test "
            "environment. Confirm the test-environment checkbox, or use READ_ONLY to inspect without that authorization."
        )
        return result()
    authorized_origin = sc.get_authorized_origin(application_url)
    user_message = sc.build_user_message(summary, description, acceptance, application_url, policy)
    allowed = sc.allowed_tool_names(policy)

    async def model_call(messages, tools, tool_choice=None):
        return await asyncio.wait_for(asyncio.to_thread(api.llm, job_id, messages, tools, tool_choice), timeout=sc.MODEL_CALL_TIMEOUT_SECONDS)

    try:
        async with streamable_http_client(mcp_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                available = {t.name: t for t in tools_result.tools if t.name in allowed}
                tools = [{"name": t.name, "description": t.description or "", "input_schema": _tool_schema(t)} for t in available.values()]

                messages = [{"role": "user", "content": user_message}]
                recent: list = []
                stop_loop = wrap_up = False
                for _turn in range(sc.MAX_MODEL_TURNS):
                    if time.monotonic() - started > sc.SCAN_TIMEOUT_SECONDS:
                        stop_reason = f"Scan timed out after {sc.SCAN_TIMEOUT_SECONDS} seconds."
                        status = "PARTIAL" if history else "FAILED"
                        wrap_up = True
                        break
                    if cancelled():
                        stop_reason = "Scan cancelled by user."
                        status = "PARTIAL" if history else "FAILED"
                        break
                    if len(history) >= sc.MAX_TOOL_CALLS:
                        stop_reason = f"Operation limit reached ({sc.MAX_TOOL_CALLS} tool calls)."
                        status = "PARTIAL"
                        wrap_up = True
                        break

                    try:
                        reply = await model_call(messages, tools)
                    except ApiError as err:
                        if err.code == "llm_retryable" and not history:
                            raise RetryableScanError(err.message) from None
                        stop_reason = err.message
                        status = "PARTIAL" if history else "FAILED"
                        break
                    except asyncio.TimeoutError:
                        stop_reason = "Timed out waiting for the model response."
                        status = "PARTIAL" if history else "FAILED"
                        break

                    calls = reply["tool_calls"]
                    if not calls:
                        final_raw = reply["text"] or ""
                        try:
                            parsed = json.loads(sc.strip_code_fences(final_raw))
                            issues = sc.validate_app_map_schema(parsed)
                            status, note = sc.derive_validated_status(parsed, str(parsed.get("status", "PARTIAL")).upper())
                            parsed["status"] = status
                            final_output = parsed
                            stop_reason = note or parsed.get("stopReason") or "Scan finished."
                        except (json.JSONDecodeError, AttributeError):
                            status = "PARTIAL" if history else "FAILED"
                            stop_reason = "The AI did not return a valid JSON App Map."
                        break

                    messages.append({"role": "assistant", "content": reply["text"] or None, "tool_calls": calls})
                    for call in calls:
                        name, arguments = call["name"], call.get("arguments") or {}
                        if name not in available:
                            permitted, reason = False, f"Tool '{name}' is not permitted under the current scan policy."
                        else:
                            permitted, reason = sc.is_tool_call_permitted(name, arguments, policy, authorized_origin)
                        entry = {
                            "sequence": len(history) + 1,
                            "tool": name,
                            "arguments": sc.scrub_secrets(sc.redact_credentials_for_log(name, arguments), known_secrets),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "allowed": permitted,
                        }
                        origin_violation = False
                        reached_url = None
                        if not permitted:
                            text = reason
                            entry["is_error"] = True
                        else:
                            try:
                                tool_result = await asyncio.wait_for(session.call_tool(name, arguments), timeout=sc.TOOL_CALL_TIMEOUT_SECONDS)
                                text = sc.tool_result_to_text(tool_result)
                                is_error = bool(getattr(tool_result, "is_error", False) or getattr(tool_result, "isError", False))
                                if is_error:
                                    text = sc.enrich_error_with_recovery_hint(text)
                                entry["is_error"] = is_error
                                reached_url = sc.extract_page_url_from_result(text)
                                if reached_url and not sc.is_same_origin(reached_url, authorized_origin):
                                    origin_violation = True
                                    entry["origin_violation"] = True
                            except asyncio.TimeoutError:
                                text = sc.enrich_error_with_recovery_hint(f"Tool '{name}' timed out after {sc.TOOL_CALL_TIMEOUT_SECONDS}s.")
                                entry["is_error"] = True
                            except Exception as exc:  # noqa: BLE001
                                text = sc.enrich_error_with_recovery_hint(f"Tool '{name}' failed: {exc}")
                                entry["is_error"] = True
                        text = sc.scrub_secrets(text, known_secrets)
                        entry["result"] = text
                        history.append(entry)
                        report(f"Scanning: step {len(history)} — {_short(name, arguments)}")
                        if name == sc.BROWSER_CLOSE_TOOL and permitted:
                            # The browser window disappears here and the AI then writes a long answer. Say so, so a
                            # quiet minute does not look like a hang.
                            report("Browser closed. The AI is now writing the App Map from what it saw; this can take 1 to 2 minutes.")

                        signature = (name, json.dumps(arguments, sort_keys=True, default=str))
                        recent = (recent + [signature])[-sc.LOOP_GUARD_WINDOW :]
                        if len(recent) == sc.LOOP_GUARD_WINDOW and len(set(recent)) == 1:
                            stop_reason = f"Stopped: the same action ('{name}') was repeated {sc.LOOP_GUARD_WINDOW} times in a row without progress."
                            status = "PARTIAL" if history else "FAILED"
                            stop_loop = wrap_up = True
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
                        if origin_violation:
                            stop_reason = f"Navigated outside the authorized origin ({authorized_origin}) to '{reached_url}'. Stopping for safety."
                            status = "PARTIAL" if history else "FAILED"
                            stop_loop = True
                        if len(history) >= sc.MAX_TOOL_CALLS or stop_loop:
                            break
                    if stop_loop:
                        break
                else:
                    stop_reason = f"Reached the maximum number of model turns ({sc.MAX_MODEL_TURNS})."
                    status = "PARTIAL" if history else "FAILED"
                    wrap_up = True

                # However the scan ended, never leave the browser open.
                already_closed = bool(history) and history[-1]["tool"] == sc.BROWSER_CLOSE_TOOL
                if not already_closed and any(t.name == sc.BROWSER_CLOSE_TOOL for t in tools_result.tools):
                    try:
                        await asyncio.wait_for(session.call_tool(sc.BROWSER_CLOSE_TOOL, {}), timeout=sc.BROWSER_CLOSE_TIMEOUT_SECONDS)
                    except Exception:  # noqa: BLE001
                        pass

                # A budget stop must not throw away what was observed: ask for the App Map from the evidence so far.
                if wrap_up and final_output is None and history:
                    budget_reason = stop_reason
                    fill_unanswered_tool_calls(messages)
                    messages.append({"role": "user", "content": sc.WRAP_UP_MESSAGE})
                    report("Writing the App Map from what was observed")
                    try:
                        wrapped_reply = await model_call(messages, tools, "none" if tools else None)
                        final_raw = wrapped_reply["text"] or ""
                        wrapped = json.loads(sc.strip_code_fences(final_raw))
                        issues = sc.validate_app_map_schema(wrapped)
                        status, _note = sc.derive_validated_status(wrapped, str(wrapped.get("status", "PARTIAL")).upper())
                        wrapped["status"] = status
                        final_output = wrapped
                        stop_reason = f"{budget_reason} The observations made so far were saved as a partial App Map."
                    except Exception:  # noqa: BLE001
                        final_raw = None
                        stop_reason = f"{budget_reason} No App Map could be recovered from the evidence."
    except RetryableScanError:
        raise
    except Exception as exc:  # noqa: BLE001
        stop_reason = f"Scan orchestration error: {sc.scrub_secrets(_leaf_reason(exc), known_secrets)}"
        status = "FAILED"
    return result()
