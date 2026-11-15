"""Scan logic shared by the server and the runner.

Ported from Pilot 1 (app/scan_engine.py). The functions below are Pilot 1's code, unchanged except where marked
"Pilot 2": the OpenAI/MCP imports, the single-slot scan_state and run_scan() are not here (the loop now lives in
runner/scan_loop.py and the model is reached through the server), and file paths became database rows.
"""
import json
import os
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

# Pilot 2: the prompt is held by the server and sent to the model by the server's proxy; it is never edited here.
SCAN_PROMPT_FILENAME = "app-scan-v1.5.txt"
SCAN_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "system-prompts" / SCAN_PROMPT_FILENAME


def load_system_prompt():
    """(text, sha256) of the unchanged Pilot 1 scan prompt."""
    import hashlib

    raw = SCAN_PROMPT_PATH.read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()

class ScanPolicy(str, Enum):
    READ_ONLY = "READ_ONLY"
    INTERACTIVE = "INTERACTIVE"


# The product's intended behavior is autonomous execution of the selected
# requirement's workflow, not manual/read-only inspection. READ_ONLY remains
# available as an explicit opt-in for pure UI mapping.
DEFAULT_SCAN_POLICY = ScanPolicy.INTERACTIVE

# Pure inspection/navigation tools. These cannot type data, submit forms, or
# otherwise change application state, so they are always permitted.
READ_ONLY_TOOLS = {
    "browser_navigate",
    "browser_navigate_back",
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_find",
    "browser_hover",
    "browser_wait_for",
    "browser_tabs",
    "browser_console_messages",
    "browser_network_requests",
    "browser_network_request",
    "browser_resize",
    # Closing the browser cannot change application data; the scan prompt asks
    # the model to call it right after its last observation so the browser does
    # not sit open while the (long) final App Map is being written.
    "browser_close",
}
BROWSER_CLOSE_TOOL = "browser_close"
BROWSER_CLOSE_TIMEOUT_SECONDS = 10

# Sent when the scan's own budget (tool calls, time, turns, loop guard) stops
# it before the model wrote its final App Map. Everything observed so far is
# too valuable to throw away: test case generation only needs the controls and
# pages that were actually seen.
WRAP_UP_MESSAGE = (
    "The scan budget is exhausted, so no more browser tools are available. Do not call any tool. "
    "Return the final JSON App Map now, exactly in the required structure, describing everything you "
    "actually observed so far: every page and every visible actionable control you saw, the actions you "
    "performed and the outcomes you observed. Set status to PARTIAL and goalReached to false unless the "
    "goal was fully demonstrated, and list in gaps what remained unexplored. Do not invent anything you "
    "did not observe."
)

# browser_click is offered under both policies: it is as much a navigation
# action (following a link, opening a product) as a workflow action (Login,
# Add to Cart, Checkout). Under READ_ONLY it is still heuristically gated
# against obviously data-changing targets; under INTERACTIVE it is trusted
# to the model within the requirement-scoped authorization conveyed in the
# prompt and enforced structurally via the origin lock below.
CLICK_TOOL = "browser_click"

# Tools that type, select, or submit data. Only available when the policy is
# explicitly ScanPolicy.INTERACTIVE — this is what "autonomous execution" of
# a requirement (login, add to cart, checkout, confirmation, ...) requires.
# File upload, native-dialog handling, drag-and-drop, and raw code execution
# remain excluded under every policy: a deliberate, app-agnostic capability
# boundary (not content/site-specific) for functionality this pilot does not
# need to complete a standard web requirement workflow.
EXECUTION_TOOLS = {
    "browser_type",
    "browser_press_key",
    "browser_select_option",
    "browser_fill_form",
}

# Substrings (case-insensitive) in a click target's description that mark it
# as a likely data-changing action rather than plain navigation. Used ONLY
# under the opt-in READ_ONLY policy, where no data-changing action should
# ever be permitted. INTERACTIVE (the default, autonomous-execution policy)
# does NOT use this list — legitimate requirement actions such as "Add to
# Cart", "Checkout", or "Place Order" must not be blocked by keyword
# matching; they are authorized by the requirement-scoped prompt context and
# constrained instead by the origin lock and tool/capability boundaries.
MUTATING_ACTION_KEYWORDS = (
    "submit", "buy", "purchase", "pay", "checkout", "place order", "confirm order",
    "add to cart", "add to bag", "remove", "delete", "save", "update", "subscribe",
    "unsubscribe", "sign up", "register", "log out", "logout", "sign out", "upload",
    "send", "transfer", "withdraw", "deposit", "approve", "reject", "accept",
    "decline", "create account", "change password", "reset password",
    "cancel subscription", "apply", "book", "reserve", "donate", "vote",
)

# Substrings (case-insensitive) in a field's element/name/target description
# that mark it as holding a credential or other secret. Values typed/filled
# into such fields are redacted before being written to the persisted scan
# logs, regardless of which tool is used.
CREDENTIAL_FIELD_KEYWORDS = (
    "password", "passwd", "pwd", "credential", "secret", "api key", "apikey",
    "token", "pin", "cvv", "cvc", "ssn", "social security", "security code",
)

# Generic patterns for "label: value"-style secrets that may appear directly
# in requirement text (e.g. supplied test credentials). Captured values are
# scrubbed from every persisted field — results, arguments, and the final
# App Map — wherever they appear, not just in the field they were typed into.
SECRET_LABEL_PATTERN = re.compile(
    r"(?:password|passwd|pwd|secret|api[ _-]?key|apikey|token|pin\b|cvv|cvc)\s*[:=]\s*"
    r"([^\s,;\"'<>]{4,})",
    re.IGNORECASE,
)

REQUIRED_APP_MAP_FIELDS = (
    "requirementId", "targetUrl", "mode", "status", "stopReason", "goalReached",
    "pages", "meaningfulActions", "observedBehaviors", "conflicts", "gaps", "evidenceRefs",
)
VALID_STATUS_VALUES = {"COMPLETE", "PARTIAL", "FAILED"}
VALID_VERIFICATION_VALUES = {"VERIFIED", "UNVERIFIED"}
VALID_EVIDENCE_STATUS_VALUES = {"DISCOVERED", "INTERACTED", "OUTCOME_OBSERVED"}

STALE_ERROR_HINTS = ("not found", "no longer", "stale", "detached", "not visible", "not attached", "timed out", "timeout")

PAGE_URL_RESULT_PATTERN = re.compile(r"Page URL:\s*(\S+)")

LOOP_GUARD_WINDOW = 4

# Configurable via environment variables so operators can tune for larger
# multi-screen workflows without a code change; defaults match the
# multi-screen-workflow requirement (60 calls / 480 seconds).
MAX_TOOL_CALLS = int(os.environ.get("APP_SCAN_MAX_TOOL_CALLS", "100"))
SCAN_TIMEOUT_SECONDS = int(os.environ.get("APP_SCAN_TIMEOUT_SECONDS", "600"))
# One tool call per turn is typical, so turns must not run out before the tool-call budget.
MAX_MODEL_TURNS = int(os.environ.get("APP_SCAN_MAX_MODEL_TURNS", "120"))
TOOL_CALL_TIMEOUT_SECONDS = int(os.environ.get("APP_SCAN_TOOL_CALL_TIMEOUT_SECONDS", "30"))
# A model reply (especially the final App Map listing every control on every
# page) can legitimately take far longer than a single browser tool call.
MODEL_CALL_TIMEOUT_SECONDS = int(os.environ.get("APP_SCAN_MODEL_CALL_TIMEOUT_SECONDS", "240"))


class ScanValidationError(Exception):
    """Raised for a bad request (invalid URL, missing explicit test-environment
    authorization) before any MCP/OpenAI connection is opened."""


# --------------------------------------------------------------------------
# Permissions: policy + origin lock (no keyword hardcoding of app content)
# --------------------------------------------------------------------------

def allowed_tool_names(policy: ScanPolicy) -> set:
    tools = set(READ_ONLY_TOOLS) | {CLICK_TOOL}
    if policy == ScanPolicy.INTERACTIVE:
        tools |= EXECUTION_TOOLS
    return tools


def is_click_mutating(arguments: dict) -> bool:
    text = f"{arguments.get('element', '')} {arguments.get('target', '')}".lower()
    return any(keyword in text for keyword in MUTATING_ACTION_KEYWORDS)


def is_valid_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and " " not in value


def get_authorized_origin(application_url: str) -> str:
    parsed = urlparse(application_url)
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def is_same_origin(url: str, authorized_origin: str) -> bool:
    if not url:
        return True
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if not parsed.scheme or not parsed.netloc:
        # Relative URL / fragment / no scheme — resolves against the current
        # (already-authorized) page, so it cannot itself be an origin change.
        return True
    return f"{parsed.scheme}://{parsed.netloc}".lower() == authorized_origin


def extract_page_url_from_result(result_text: str) -> str | None:
    match = PAGE_URL_RESULT_PATTERN.search(result_text or "")
    return match.group(1) if match else None


def is_tool_call_permitted(name: str, arguments: dict, policy: ScanPolicy, authorized_origin: str):
    """Returns (permitted: bool, reason_if_blocked: str | None).

    No requirement-, product-, or site-specific content is hardcoded here:
    enforcement is purely structural — which capability class a tool belongs
    to, the declared scan policy, and whether a navigation target stays
    within the one application origin the caller explicitly authorized.
    """
    if name == CLICK_TOOL:
        if policy == ScanPolicy.READ_ONLY and is_click_mutating(arguments):
            element = arguments.get("element", "the target element")
            return False, (
                f"Click on '{element}' looks like a data-changing action and is "
                "blocked under the READ_ONLY scan policy."
            )
        return True, None

    if name == "browser_navigate":
        target = arguments.get("url", "")
        if not is_same_origin(target, authorized_origin):
            return False, (
                f"Navigation to '{target}' is outside the authorized application origin "
                f"({authorized_origin}) and is blocked."
            )
        return True, None

    if name in READ_ONLY_TOOLS:
        return True, None

    if name in EXECUTION_TOOLS:
        if policy == ScanPolicy.INTERACTIVE:
            return True, None
        return False, f"Tool '{name}' requires the INTERACTIVE scan policy and is blocked under READ_ONLY."

    return False, f"Tool '{name}' is not permitted under the current scan policy."


# --------------------------------------------------------------------------
# Sensitive-data redaction (credential fields + any known secret value)
# --------------------------------------------------------------------------

def _is_credential_field(*descriptions: str) -> bool:
    text = " ".join(d or "" for d in descriptions).lower()
    return any(keyword in text for keyword in CREDENTIAL_FIELD_KEYWORDS)


def redact_credentials_for_log(name: str, arguments: dict) -> dict:
    """Returns a deep copy of arguments safe to persist to disk, with values
    typed/selected into credential-looking fields replaced. The real
    arguments are still used for execution and the live model conversation —
    only the on-disk audit log is redacted."""
    redacted = json.loads(json.dumps(arguments)) if arguments else {}

    if _is_credential_field(redacted.get("element"), redacted.get("target"), redacted.get("name")):
        for key in ("text", "value", "values"):
            if key in redacted:
                redacted[key] = "[REDACTED]"

    if name == "browser_fill_form":
        for field in redacted.get("fields", []) or []:
            if isinstance(field, dict) and _is_credential_field(field.get("element"), field.get("name"), field.get("target")):
                if "value" in field:
                    field["value"] = "[REDACTED]"

    return redacted


def extract_known_secrets(*texts: str) -> set:
    """Pulls likely secret VALUES out of free-text requirement content (e.g.
    'Password: secret_sauce' in a supplied Acceptance Criteria). These exact
    values are then scrubbed from every persisted field, not just the field
    they were typed into, so a credential can never leak via a result string
    or the final App Map JSON. Generic/regex-based — no app-specific values."""
    secrets = set()
    for text in texts:
        if not text:
            continue
        for match in SECRET_LABEL_PATTERN.finditer(text):
            value = match.group(1).strip().rstrip(".,;:")
            if len(value) >= 4:
                secrets.add(value)
    return secrets


def scrub_secrets(value, secrets: set):
    if not secrets:
        return value
    if isinstance(value, str):
        for secret in secrets:
            if secret and secret in value:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {k: scrub_secrets(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_secrets(v, secrets) for v in value]
    return value


def enrich_error_with_recovery_hint(message: str) -> str:
    lower = (message or "").lower()
    if any(hint in lower for hint in STALE_ERROR_HINTS):
        return message + " Take a fresh browser_snapshot before retrying; the page may have changed."
    return message


# --------------------------------------------------------------------------
# App Map schema validation (deterministic; never trusts the model blindly)
# --------------------------------------------------------------------------

def validate_app_map_schema(parsed) -> list:
    """Validates and, where the contract itself implies a correction (e.g. a
    locator claimed VERIFIED without uniquenessChecked), mutates `parsed` in
    place to enforce it. Returns a list of issue strings (empty = clean)."""
    issues = []
    if not isinstance(parsed, dict):
        return ["App Map is not a JSON object."]

    for field in REQUIRED_APP_MAP_FIELDS:
        if field not in parsed:
            issues.append(f"Missing required field '{field}'.")

    if parsed.get("status") not in VALID_STATUS_VALUES:
        issues.append(f"Invalid status '{parsed.get('status')}'.")

    pages = parsed.get("pages")
    if not isinstance(pages, list):
        issues.append("'pages' must be a list.")
        pages = []

    for page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            issues.append(f"pages[{page_index}] is not an object.")
            continue
        controls = page.get("controls")
        if not isinstance(controls, list):
            issues.append(f"pages[{page_index}].controls must be a list.")
            controls = []
        for control_index, control in enumerate(controls):
            if not isinstance(control, dict):
                issues.append(f"pages[{page_index}].controls[{control_index}] is not an object.")
                continue
            technical = control.get("technical") or {}
            human_name = control.get("humanName")
            for tech_key in ("domId", "domName", "testId"):
                tech_value = technical.get(tech_key)
                if human_name and tech_value and human_name == tech_value:
                    issues.append(
                        f"pages[{page_index}].controls[{control_index}]: humanName equals technical "
                        f"'{tech_key}' — a technical identifier may have been used as the human-readable name."
                    )
            locator_candidates = technical.get("locatorCandidates")
            if isinstance(locator_candidates, list):
                for lc_index, lc in enumerate(locator_candidates):
                    if not isinstance(lc, dict):
                        continue
                    if lc.get("verification") == "VERIFIED" and not lc.get("uniquenessChecked"):
                        lc["verification"] = "UNVERIFIED"
                        issues.append(
                            f"pages[{page_index}].controls[{control_index}].locatorCandidates[{lc_index}]: "
                            "downgraded VERIFIED to UNVERIFIED because uniquenessChecked was not true."
                        )
                    if lc.get("verification") not in VALID_VERIFICATION_VALUES:
                        issues.append(
                            f"pages[{page_index}].controls[{control_index}].locatorCandidates[{lc_index}]: "
                            f"invalid verification value '{lc.get('verification')}'."
                        )

    actions = parsed.get("meaningfulActions")
    if not isinstance(actions, list):
        issues.append("'meaningfulActions' must be a list.")
        actions = []

    previous_sequence = 0
    for action_index, action in enumerate(actions):
        if not isinstance(action, dict):
            issues.append(f"meaningfulActions[{action_index}] is not an object.")
            continue
        sequence = action.get("sequence")
        if not isinstance(sequence, int):
            issues.append(f"meaningfulActions[{action_index}].sequence must be an integer.")
        elif sequence <= previous_sequence:
            issues.append(f"meaningfulActions[{action_index}].sequence ({sequence}) is not strictly increasing.")
        else:
            previous_sequence = sequence
        if action.get("evidenceStatus") not in VALID_EVIDENCE_STATUS_VALUES:
            issues.append(f"meaningfulActions[{action_index}]: invalid evidenceStatus '{action.get('evidenceStatus')}'.")

    return issues


def derive_validated_status(parsed: dict, raw_status: str):
    """Never let a scan be reported COMPLETE unless the App Map itself claims
    the goal was reached AND at least one action has OUTCOME_OBSERVED
    evidence. Returns (status, downgrade_note_or_None)."""
    status = raw_status if raw_status in VALID_STATUS_VALUES else "PARTIAL"
    if status != "COMPLETE":
        return status, None

    goal_reached = parsed.get("goalReached") is True
    actions = parsed.get("meaningfulActions") or []
    has_outcome_evidence = any(
        isinstance(a, dict) and a.get("evidenceStatus") == "OUTCOME_OBSERVED" for a in actions
    )
    if not goal_reached or not has_outcome_evidence:
        return "PARTIAL", (
            "Downgraded from COMPLETE to PARTIAL: goalReached was not true, or no action had "
            "evidenceStatus 'OUTCOME_OBSERVED', so the required outcome was not confirmed as observed."
        )
    return "COMPLETE", None


# --------------------------------------------------------------------------
# TCG / TSG input contracts (prepared now; generation is a later task)
# --------------------------------------------------------------------------

def build_requirement_snapshot(requirement_id, summary, description, acceptance_criteria, run_id) -> dict:
    return {
        "requirement_id": requirement_id,
        "summary": summary,
        "description": description,
        "acceptance_criteria": acceptance_criteria,
        "captured_for_run_id": run_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def build_tcg_input_contract(status_record: dict, requirement_snapshot: dict, app_map: dict) -> dict:
    app_map = app_map or {}
    ordered_actions = sorted(
        (a for a in app_map.get("meaningfulActions", []) if isinstance(a, dict)),
        key=lambda a: a.get("sequence", 0),
    )
    return {
        "contract_version": "1.0",
        "run_id": status_record["run_id"],
        "requirement_id": status_record["requirement_id"],
        "requirement": {
            "summary": requirement_snapshot["summary"],
            "description": requirement_snapshot["description"],
            "acceptance_criteria": requirement_snapshot["acceptance_criteria"],
        },
        "scan_mode": status_record["scan_policy"],
        "scan_status": status_record["status"],
        "application_url": status_record["application_url"],
        "model": status_record["model"],
        "ordered_meaningful_actions": ordered_actions,
        "pages": app_map.get("pages", []),
        "observed_behaviors": app_map.get("observedBehaviors", []),
        "gaps": app_map.get("gaps", []),
        "conflicts": app_map.get("conflicts", []),
        "goal_reached": app_map.get("goalReached", False),
    }


def build_tsg_input_contract(status_record: dict, app_map: dict) -> dict:
    app_map = app_map or {}
    verified_locators = []
    page_element_context = []

    for page in app_map.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        for control in page.get("controls", []) or []:
            if not isinstance(control, dict):
                continue
            technical = control.get("technical") or {}
            page_element_context.append(
                {
                    "page_url": page.get("url"),
                    "page_name": page.get("name"),
                    "human_name": control.get("humanName"),
                    "element_type": control.get("type"),
                    "visible_context": control.get("visibleContext"),
                }
            )
            for lc in technical.get("locatorCandidates", []) or []:
                if isinstance(lc, dict) and lc.get("verification") == "VERIFIED":
                    verified_locators.append(
                        {
                            "page_url": page.get("url"),
                            "human_name": control.get("humanName"),
                            "element_id": control.get("elementId"),
                            "locator": lc.get("locator"),
                            "uniqueness_checked": lc.get("uniquenessChecked", False),
                        }
                    )

    return {
        "contract_version": "1.0",
        "run_id": status_record["run_id"],
        "requirement_id": status_record["requirement_id"],
        "app_map_reference": f"scans/{status_record['run_id']}",  # Pilot 2: a scan row, not a file
        # Populated by a future Test Case Generation step; intentionally
        # empty here — this task only guarantees the contract's shape exists.
        "test_cases": [],
        "verified_locators": verified_locators,
        "page_element_context": page_element_context,
        "automation_gaps": app_map.get("gaps", []),
        "conflicts": app_map.get("conflicts", []),
    }


def validate_contracts(tcg_contract: dict, tsg_contract: dict, app_map: dict) -> list:
    """Deterministic cross-checks: every reference in either contract must
    trace back to something actually present in the App Map. No fabricated
    locators, actions, or outcomes."""
    issues = []
    app_map = app_map or {}

    action_sequences_in_map = {
        a.get("sequence") for a in app_map.get("meaningfulActions", []) if isinstance(a, dict)
    }
    for action in tcg_contract["ordered_meaningful_actions"]:
        if action.get("sequence") not in action_sequences_in_map:
            issues.append(f"TCG contract references action sequence {action.get('sequence')} not found in the App Map.")

    verified_locators_in_map = set()
    for page in app_map.get("pages", []) or []:
        for control in (page.get("controls") or []):
            technical = control.get("technical") or {}
            for lc in (technical.get("locatorCandidates") or []):
                if isinstance(lc, dict) and lc.get("verification") == "VERIFIED":
                    verified_locators_in_map.add(lc.get("locator"))
    for locator_entry in tsg_contract["verified_locators"]:
        if locator_entry["locator"] not in verified_locators_in_map:
            issues.append(f"TSG contract lists locator '{locator_entry['locator']}' not marked VERIFIED in the App Map.")

    return issues


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def tool_result_to_text(result) -> str:
    parts = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(f"[{getattr(block, 'type', 'non-text')} content omitted]")
    text = "\n".join(parts)
    return text[:8000]


def build_user_message(summary: str, description: str, acceptance_criteria: str, application_url: str, policy: ScanPolicy) -> str:
    allowed_list = ", ".join(sorted(allowed_tool_names(policy)))
    if policy == ScanPolicy.INTERACTIVE:
        permissions_text = (
            "Scan Permissions: autonomous execution is authorized for this scan. You may "
            "navigate, click, type, select options, and submit forms as needed to fully "
            "complete the requirement's workflow below — including login, adding items, "
            "navigation between screens, form submission, checkout, and final confirmation. "
            "You may use any credentials supplied in the Requirement Ticket. Stay strictly "
            "within the supplied Target URL's origin; do not follow links to a different "
            "site, and do not perform any action unrelated to this specific requirement "
            "(e.g. unrelated purchases, deletions, or account changes)."
        )
    else:
        permissions_text = (
            "Scan Permissions: READ_ONLY mode. Do not type into fields, submit forms, "
            "or create, update, delete, purchase, pay, or otherwise change application "
            "data. Clicks on controls that appear to change data (e.g. 'Add to Cart', "
            "'Delete', 'Submit', 'Place Order') will be blocked automatically."
        )
    budget_text = (
        f"Tool-call budget: at most {MAX_TOOL_CALLS} tool calls in total. Work efficiently: take one snapshot per "
        "page or state instead of after every field, and fill several fields in one browser_fill_form call where "
        f"possible. When about 12 calls remain, stop exploring, call browser_close, and return the final JSON App "
        "Map describing everything you observed, even if the flow is incomplete (status PARTIAL)."
    )
    return (
        "Requirement Ticket\n"
        f"Summary: {summary}\n\n"
        "Description:\n"
        f"{description}\n\n"
        "Acceptance Criteria:\n"
        f"{acceptance_criteria}\n\n"
        f"Target URL: {application_url}\n\n"
        f"{permissions_text} Only the following MCP tools are available to you: "
        f"{allowed_list}.\n\n"
        f"{budget_text}"
    )
