"""Test case generation (Pilot 1 stage 3), ported from Pilot 1's tcg_engine.py.

The constants, the documented-test-data extraction, the user message, the JSON validation and normalisation and the
coverage summary are copied unchanged (the "Ported unchanged" section). Only storage and transport differ: the scan
comes from the database and the model call goes through app/llm.py, so any provider works.
"""
import hashlib
import json
import os
import re
from pathlib import Path

from app.engines.scan_core import scrub_secrets

PROMPT_FILENAME = "test-case-generation-v2.0.txt"
PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "system-prompts" / PROMPT_FILENAME

# ---------------------------------------------------------------------------
# Ported unchanged from Pilot 1 (app/tcg_engine.py)
# ---------------------------------------------------------------------------
TESTCASE_TYPES = {"POSITIVE", "NEGATIVE", "VERIFICATION", "BOUNDARY", "EDGE"}
APPROVED_ACTIONS = (
    "navigate", "click", "select", "enter", "hover", "check", "uncheck",
    "clear", "press", "upload", "scroll", "capture",
)
# Actions that actually trigger or observe the behavior under test. Navigate,
# Enter, Hover, Clear, and Scroll alone only move around or fill in a form.
COMPLETING_ACTIONS = {"click", "select", "check", "uncheck", "press", "upload", "capture"}

# Canonical credential field labels a Requirement ticket commonly documents
# literal required test data for (e.g. "Username: standard_user"). Generic
# and requirement-agnostic.
DOCUMENTED_FIELD_LABELS = ("username", "user name", "password", "passwd", "pwd")
DOCUMENTED_FIELD_VALUE_PATTERN = re.compile(
    r"(" + "|".join(re.escape(label) for label in DOCUMENTED_FIELD_LABELS) + r")\s*[:=]\s*([^\s,;\"'<>]{2,})",
    re.IGNORECASE,
)
# TestMax's generic-placeholder format (e.g. "<authorized valid password>") —
# a bracketed token, never a concrete value a real test would use.
PLACEHOLDER_VALUE_PATTERN = re.compile(r"^<.*>$")

STEP_ALLOWED_KEYS = {"stepNo", "testStep", "fieldName", "testData", "expectedResult", "sourceElementId"}
APPROVED_ACTION_WORD_PATTERN = {verb: re.compile(rf"\b{re.escape(verb)}\b", re.IGNORECASE) for verb in APPROVED_ACTIONS}

KEY_NAME_PATTERN = re.compile(r"\b(enter|tab|escape|space|delete|backspace)\s+key\b", re.IGNORECASE)

TECHNICAL_LEAK_PATTERN = re.compile(r"(#[\w-]{2,}|\[data-[\w-]+|//\w+\[|xpath|css=|nth-child)", re.IGNORECASE)


def extract_documented_test_data(*texts: str) -> dict:
    """Pulls literal "<field label>: <value>" test-data values (username/
    password-shaped labels) directly out of the Requirement's own free text,
    keyed by a normalized label ("username", "password"). These are values
    the Requirement itself hands testers to execute the flow, so generated
    steps must use them verbatim rather than a generic placeholder."""
    documented: dict = {}
    for text in texts:
        if not text:
            continue
        for match in DOCUMENTED_FIELD_VALUE_PATTERN.finditer(text):
            label = match.group(1).strip().lower()
            if label in ("user name",):
                label = "username"
            elif label in ("passwd", "pwd"):
                label = "password"
            value = match.group(2).strip().rstrip(".,;:")
            documented.setdefault(label, value)
    return documented


def collect_known_element_ids(app_map: dict) -> set:
    ids = set()
    for page in app_map.get("pages", []) or []:
        for control in (page.get("controls") or []):
            element_id = control.get("elementId")
            if element_id:
                ids.add(element_id)
    return ids


def collect_known_technical_tokens(app_map: dict) -> set:
    tokens = set()
    for page in app_map.get("pages", []) or []:
        for control in (page.get("controls") or []):
            technical = control.get("technical") or {}
            for key in ("domId", "domName", "testId"):
                value = technical.get(key)
                if value:
                    tokens.add(value)
            element_id = control.get("elementId")
            if element_id:
                tokens.add(element_id)
    return tokens


def build_user_message(requirement_id: str, requirement: dict, scan_run_id: str, app_map: dict, tcg_contract: dict) -> str:
    normalized_scan = {
        "scanRunId": scan_run_id,
        "status": tcg_contract.get("scan_status"),
        "mode": tcg_contract.get("scan_mode"),
        "applicationUrl": tcg_contract.get("application_url"),
        "orderedMeaningfulActions": tcg_contract.get("ordered_meaningful_actions", []),
        "pages": app_map.get("pages", []),
        "observedBehaviors": tcg_contract.get("observed_behaviors", []),
        "gaps": tcg_contract.get("gaps", []),
        "conflicts": tcg_contract.get("conflicts", []),
        "goalReached": tcg_contract.get("goal_reached", False),
        "evidenceRefs": app_map.get("evidenceRefs", []),
    }
    return (
        "Requirement Ticket\n"
        f"Requirement ID: {requirement_id}\n"
        f"Summary: {requirement['summary']}\n\n"
        "Description:\n"
        f"{requirement['description']}\n\n"
        "Acceptance Criteria:\n"
        f"{requirement['acceptance_criteria']}\n\n"
        f"Scan Run ID to echo back as scanRunId: {scan_run_id}\n\n"
        "Normalized App Scan (JSON):\n"
        f"{json.dumps(normalized_scan, indent=2)}"
    )

def strip_code_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _is_technical_looking_token(token: str) -> bool:
    """True when a token's own spelling marks it as a technical identifier
    rather than an ordinary human-visible word — hyphenated/underscored
    ("login-username"), containing a digit, or camelCase ("txtUsername").
    A plain single word like "username" or "password" is excluded: it is
    indistinguishable from (and often identical to) the legitimate
    human-visible label."""
    if not token:
        return False
    if re.search(r"[-_]", token):
        return True
    if re.search(r"\d", token):
        return True
    if re.search(r"[a-z][A-Z]", token):
        return True
    return False


def _contains_technical_leak(text: str, technical_tokens: set) -> bool:
    if not text:
        return False
    if TECHNICAL_LEAK_PATTERN.search(text):
        return True
    return any(
        token and _is_technical_looking_token(token) and token in text
        for token in technical_tokens
    )


def _has_multiple_actions(test_step: str) -> bool:
    """True when a testStep appears to chain more than one approved action
    verb together (e.g. "Enter the username and click Login")."""
    words = test_step.strip().split()
    if len(words) < 2:
        return False
    # "Press the Enter key" names a keyboard key, not a second Enter action.
    rest = KEY_NAME_PATTERN.sub("", " ".join(words[1:]))
    return any(pattern.search(rest) for pattern in APPROVED_ACTION_WORD_PATTERN.values())


def validate_and_normalize(
    parsed,
    expected_requirement_id: str,
    expected_scan_run_id: str,
    known_element_ids: set,
    technical_tokens: set,
    documented_test_data: dict | None = None,
):
    """Validates the model's JSON structure against the system prompt's
    contract. Only structure is policed — never the model's QA judgment
    about what to test or what should happen.

    Returns {ok, critical_issues, warnings, mapping_gaps, normalized};
    `normalized` is None when `ok` is False. Safe deterministic fixes (step
    renumbering, blank field/data -> "—", echoing the supplied requirement
    and scan IDs) are applied; everything else wrong is reported.

    `documented_test_data` (output of extract_documented_test_data()) is
    optional; when given, an Enter step for a matching field must not use a
    bracketed placeholder in place of the Requirement's own documented value.
    """
    critical = []
    warnings = []
    mapping_gaps = []

    if not isinstance(parsed, dict):
        return {"ok": False, "critical_issues": ["Response is not a JSON object."], "warnings": [], "mapping_gaps": [], "normalized": None}

    if parsed.get("requirementId") != expected_requirement_id:
        warnings.append(f"requirementId '{parsed.get('requirementId')}' replaced with the selected requirement '{expected_requirement_id}'.")
    if parsed.get("scanRunId") != expected_scan_run_id:
        warnings.append(f"scanRunId '{parsed.get('scanRunId')}' replaced with the reused scan run '{expected_scan_run_id}'.")

    test_cases = parsed.get("testCases")
    if not isinstance(test_cases, list):
        critical.append("'testCases' must be a list.")
        test_cases = []
    elif not test_cases:
        critical.append("No test cases were generated.")

    normalized_not_generated = []
    raw_not_generated = parsed.get("notGenerated")
    if raw_not_generated is not None:
        if not isinstance(raw_not_generated, list):
            critical.append("'notGenerated' must be a list when present.")
        else:
            for index, entry in enumerate(raw_not_generated):
                if not isinstance(entry, dict):
                    critical.append(f"notGenerated[{index}] is not an object.")
                    continue
                normalized_not_generated.append(
                    {
                        "title": entry.get("title") or "",
                        "missingElement": entry.get("missingElement") or "",
                        "reason": entry.get("reason") or "",
                    }
                )

    normalized_cases = []
    for tc_index, tc in enumerate(test_cases):
        label = f"testCases[{tc_index}]"
        if not isinstance(tc, dict):
            critical.append(f"{label} is not an object.")
            continue

        tc_type = tc.get("testCaseType")
        title = tc.get("title") or ""
        expected_result = tc.get("expectedResult") or ""

        if tc_type not in TESTCASE_TYPES:
            critical.append(f"{label}: invalid testCaseType '{tc_type}'.")
        if not title.strip():
            critical.append(f"{label}: 'title' is required.")
        elif not title.strip().startswith("Verify"):
            warnings.append(f"{label}: title does not begin with 'Verify' ('{title}').")
        if not expected_result.strip():
            critical.append(f"{label}: 'expectedResult' is required and must be non-empty.")
        if _contains_technical_leak(title, technical_tokens) or _contains_technical_leak(expected_result, technical_tokens):
            warnings.append(f"{label}: possible technical identifier leaked into customer-facing text.")

        raw_steps = tc.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            critical.append(f"{label}: 'steps' must be a non-empty list.")
            raw_steps = []
        elif len(raw_steps) < 2:
            critical.append(f"{label}: a test case needs at least two steps.")

        normalized_steps = []
        has_completing_action = False
        for step_index, step in enumerate(raw_steps):
            step_label = f"{label}.steps[{step_index}]"
            if not isinstance(step, dict):
                critical.append(f"{step_label} is not an object.")
                continue

            extra_keys = set(step.keys()) - STEP_ALLOWED_KEYS
            if extra_keys:
                warnings.append(f"{step_label}: ignored unexpected field(s) {sorted(extra_keys)} — exactly five visible columns are approved.")

            test_step = (step.get("testStep") or "").strip()
            first_word = None
            if not test_step:
                critical.append(f"{step_label}: 'testStep' is required.")
            else:
                first_word = test_step.split(" ", 1)[0].strip(".,:;").lower()
                if first_word not in APPROVED_ACTIONS:
                    critical.append(f"{step_label}: testStep does not begin with an approved action verb ('{test_step[:40]}').")
                elif _has_multiple_actions(test_step):
                    warnings.append(f"{step_label}: testStep may combine more than one action ('{test_step[:60]}').")
                if first_word in COMPLETING_ACTIONS:
                    has_completing_action = True

            field_name = step.get("fieldName")
            field_name = field_name.strip() if isinstance(field_name, str) else field_name
            if field_name is None:
                critical.append(f"{step_label}: 'fieldName' is required.")
                field_name = "—"
            elif field_name == "":
                field_name = "—"
            elif _contains_technical_leak(str(field_name), technical_tokens):
                critical.append(f"{step_label}: 'fieldName' must be a human-readable label, not a technical identifier ('{field_name}').")

            test_data = step.get("testData")
            test_data = test_data.strip() if isinstance(test_data, str) else test_data
            if test_data is None:
                critical.append(f"{step_label}: 'testData' is required.")
                test_data = "—"
            elif test_data == "":
                test_data = "—"
            if first_word == "enter" and test_data == "—":
                critical.append(f"{step_label}: an Enter step must specify the value entered, not the empty placeholder '—'.")
            if documented_test_data and first_word == "enter" and isinstance(field_name, str):
                documented_value = documented_test_data.get(field_name.strip().lower())
                # Only a bracketed placeholder is rejected — a deliberately
                # different concrete value (a wrong password for a negative
                # case) is exactly the intended use of that field.
                if documented_value and isinstance(test_data, str) and PLACEHOLDER_VALUE_PATTERN.match(test_data):
                    critical.append(
                        f"{step_label}: fieldName '{field_name}' has a Requirement-documented test data value "
                        f"('{documented_value}') but this step's testData is the placeholder '{test_data}'."
                    )

            step_expected = (step.get("expectedResult") or "").strip()
            if not step_expected:
                critical.append(f"{step_label}: 'expectedResult' is required and must be non-empty.")

            if _contains_technical_leak(test_step, technical_tokens):
                warnings.append(f"{step_label}: possible technical identifier leaked into customer-facing text.")

            source_element_id = step.get("sourceElementId")
            if source_element_id is not None and source_element_id not in known_element_ids:
                mapping_gaps.append(f"{step_label}: sourceElementId '{source_element_id}' has no matching App Map control (not invented; left as reported).")

            normalized_steps.append(
                {
                    "stepNo": step_index + 1,
                    "testStep": test_step,
                    "fieldName": field_name,
                    "testData": test_data,
                    "expectedResult": step_expected,
                    "sourceElementId": source_element_id,
                }
            )

        if raw_steps and not has_completing_action:
            critical.append(
                f"{label}: no step performs a completing interaction (Click, Select, Check, Uncheck, Press, "
                "Upload, or Capture) — navigating or merely entering data does not exercise the behavior under test."
            )

        normalized_cases.append(
            {
                "testCaseType": tc_type,
                "title": title,
                "overview": tc.get("overview") or "",
                "preRequisites": tc.get("preRequisites") if isinstance(tc.get("preRequisites"), list) else [],
                "testObjective": tc.get("testObjective") or "",
                "expectedResult": expected_result,
                "steps": normalized_steps,
            }
        )

    if critical:
        return {"ok": False, "critical_issues": critical, "warnings": warnings, "mapping_gaps": mapping_gaps, "normalized": None}

    # A case whose steps are exactly identical to an earlier case's adds
    # nothing; drop it (safe, deterministic) and report it.
    seen_signatures: dict = {}
    unique_cases = []
    for tc in normalized_cases:
        signature = tuple(
            (step["testStep"].strip().lower(), str(step["fieldName"]).strip().lower(), str(step["testData"]).strip().lower())
            for step in tc["steps"]
        )
        if signature in seen_signatures:
            warnings.append(f"Dropped duplicate test case '{tc['title']}': its steps are identical to '{seen_signatures[signature]}'.")
            continue
        seen_signatures[signature] = tc["title"]
        unique_cases.append(tc)
    normalized_cases = unique_cases

    for index, tc in enumerate(normalized_cases, start=1):
        tc["id"] = f"TC-{index:03d}"

    normalized = {
        "requirementId": expected_requirement_id,
        "scanRunId": expected_scan_run_id,
        "testCases": normalized_cases,
        "notGenerated": normalized_not_generated,
    }
    return {"ok": True, "critical_issues": [], "warnings": warnings, "mapping_gaps": mapping_gaps, "normalized": normalized}


def scrub_technical_leakage_from_customer_text(normalized: dict, technical_tokens: set) -> None:
    """Replaces any known technical identifier found inside customer-facing
    text fields, in place. sourceElementId is intentionally untouched — it
    is private metadata, not customer-facing text."""
    if not technical_tokens:
        return
    for tc in normalized.get("testCases", []):
        for field in ("title", "overview", "testObjective", "expectedResult"):
            if tc.get(field):
                tc[field] = scrub_secrets(tc[field], technical_tokens)
        for step in tc.get("steps", []):
            for field in ("testStep", "fieldName", "testData", "expectedResult"):
                if step.get(field):
                    step[field] = scrub_secrets(step[field], technical_tokens)


def summarize_coverage(normalized: dict) -> dict:
    """Deterministic count of generated cases per category — computed from
    the cases themselves, never self-reported by the model."""
    type_counts = {category: 0 for category in ("POSITIVE", "NEGATIVE", "VERIFICATION", "BOUNDARY", "EDGE")}
    for tc in normalized["testCases"]:
        type_counts[tc["testCaseType"]] += 1
    return {
        "test_case_count": len(normalized["testCases"]),
        "type_counts": type_counts,
        "not_generated_count": len(normalized.get("notGenerated") or []),
    }


# ---------------------------------------------------------------------------
# Pilot 2: the generation call (the same flow as Pilot 1's generate_test_cases, minus files and OpenAI-only code)
# ---------------------------------------------------------------------------
# The model accepts up to 128,000 completion tokens; a rich requirement needs well over 32,000 (about 1,400 tokens
# per case). The provider adapter caps this to what the provider allows.
MAX_OUTPUT_TOKENS = int(os.environ.get("TESTMAX_TCG_MAX_OUTPUT_TOKENS", "96000"))
MAX_ATTEMPTS = max(1, int(os.environ.get("TESTMAX_TCG_MAX_ATTEMPTS", "2")))


class GenerationError(Exception):
    """The message is shown to the user. `retryable` is for transport problems (the job queue retries those)."""

    def __init__(self, message: str, retryable: bool = False, issues: list | None = None):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.issues = issues or []


def load_system_prompt() -> tuple[str, str]:
    """(prompt text, sha256 of exactly those bytes). Never edit the prompt file without approval."""
    raw = PROMPT_PATH.read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def run_generation(converse, *, requirement_key: str, scan_id: str, tcg_contract: dict, app_map: dict, system_prompt: str, max_attempts: int | None = None, on_progress=None, usage: dict | None = None) -> dict:
    """`converse(messages, max_output_tokens)` returns an app.llm_types.LLMResult (may raise LLMError).

    One request, plus one bounded retry when the JSON is invalid or fails validation (the problems are sent back to the
    model, as in Pilot 1). A cut-off answer is not retried: it would hit the same ceiling and be paid for twice.
    """
    from app.llm_types import LLMError

    max_attempts = max_attempts or MAX_ATTEMPTS
    requirement = tcg_contract["requirement"]
    documented = extract_documented_test_data(requirement["summary"], requirement["description"], requirement["acceptance_criteria"])
    known_element_ids = collect_known_element_ids(app_map)
    technical_tokens = collect_known_technical_tokens(app_map)
    messages = [{"role": "user", "content": build_user_message(requirement_key, requirement, scan_id, app_map, tcg_contract)}]

    usage = usage if usage is not None else {}  # filled as we go, so a failed run still reports what it used
    usage.update(tokens_in=0, tokens_out=0)
    raw = ""
    validation = None
    for attempt in range(max_attempts):
        if on_progress:
            on_progress("Asking the AI to write the test cases" if attempt == 0 else "Asking the AI to correct its answer")
        try:
            result = converse(messages, MAX_OUTPUT_TOKENS)
        except LLMError as err:
            raise GenerationError(err.message, retryable=err.retryable) from None
        raw = result.text or ""
        usage["tokens_in"] += result.tokens_in or 0
        usage["tokens_out"] += result.tokens_out or 0
        if result.truncated:
            raise GenerationError(
                f"The AI ran out of output space ({MAX_OUTPUT_TOKENS} tokens) before finishing the test cases. "
                "Raise TESTMAX_TCG_MAX_OUTPUT_TOKENS (OpenAI models allow up to 128000; Anthropic is limited by "
                "ANTHROPIC_MAX_OUTPUT_TOKENS) or split the requirement."
            )
        try:
            parsed = json.loads(strip_code_fences(raw))
        except json.JSONDecodeError as exc:
            last_message = f"The AI did not return valid JSON ({exc})."
            if attempt < max_attempts - 1:
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": "That response was not valid JSON. Return ONLY the corrected valid JSON object, matching the required schema exactly."},
                ]
                continue
            raise GenerationError(last_message) from None

        validation = validate_and_normalize(parsed, requirement_key, scan_id, known_element_ids, technical_tokens, documented_test_data=documented)
        if validation["ok"]:
            break
        last_message = "Generated JSON failed schema validation: " + "; ".join(validation["critical_issues"][:5])
        if attempt < max_attempts - 1:
            messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "That response failed schema validation with these issues: "
                    + "; ".join(validation["critical_issues"])
                    + ". Return ONLY the corrected valid JSON object, matching the required schema exactly.",
                },
            ]
            continue
        raise GenerationError(last_message, issues=validation["critical_issues"])

    normalized = validation["normalized"]
    # Deliberately NOT scrubbed against the Requirement's own documented credentials (the prompt requires using them
    # verbatim); only distinctive technical element identifiers are scrubbed from customer-facing text.
    scrub_technical_leakage_from_customer_text(normalized, known_element_ids)
    return {
        "normalized": normalized,
        "warnings": validation["warnings"],
        "mapping_gaps": validation["mapping_gaps"],
        "coverage": summarize_coverage(normalized),
        "raw": raw,
        "tokens_in": usage["tokens_in"],
        "tokens_out": usage["tokens_out"],
        "attempts": attempt + 1,
    }
