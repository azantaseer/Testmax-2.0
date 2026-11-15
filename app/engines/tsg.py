"""Test script generation (Pilot 1 stage 4), ported from Pilot 1's tsg_engine.py.

Locator building from the scan (never invented by the AI), the fixed script header and helpers, the user message, body
validation and the `node --check` syntax check are copied unchanged (the "Ported unchanged" section). Only storage and
transport differ: the test case and the App Map come from the database and the model call goes through app/llm.py.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from app.engines.tcg import strip_code_fences

PROMPT_FILENAME = "test-script-generation-v1.0.txt"
PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "system-prompts" / PROMPT_FILENAME

# ---------------------------------------------------------------------------
# Ported unchanged from Pilot 1 (app/tsg_engine.py)
# ---------------------------------------------------------------------------
MAX_LOCATORS_PER_ELEMENT = 10
FIELD_ROLES = {"button", "combobox", "textbox", "checkbox", "radio", "spinbutton", "searchbox", "switch"}
FIELD_TYPE_WORDS = ("textbox", "input", "searchbox", "combobox", "select", "dropdown", "listbox", "textarea", "spinbutton", "number")
FRAGILE_LOCATOR_PATTERN = re.compile(r"\.nth\(|hasText|:nth-|\.first\(\)|\.last\(\)")
OPTION_LOCATOR_PATTERN =re.compile(r"^page\.getByRole\(\s*['\"]option['\"]")
NEXT_CONTROL_XPATH = (
    "xpath=following::*[self::input[not(@type='hidden')] or self::select or self::textarea or self::button"
    " or @role='combobox' or @role='listbox' or @role='textbox' or @tabindex='0'][1]"
)
# page.getByRole('x', { name: 'N' }) matches any name that merely CONTAINS N
# ("State" also matches "United States"), so an exact-name twin is tried first.
ROLE_NAME_LOCATOR_PATTERN = re.compile(
    r"^page\.getByRole\(\s*('[a-z]+'|\"[a-z]+\")\s*,\s*\{\s*name:\s*('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")\s*\}\s*\)$"
)
RUN_OUTPUT_LINE_LIMIT = 2000

# A locator copied from the scan is placed into generated code, so only plain
# Playwright page.<locator>(...) expressions are accepted.
UNSAFE_CODE_PATTERN = re.compile(
    r"[;\r\n`]|\$\{|\b(require|process|eval|import|constructor|Function|globalThis|child_process|module|exports)\b"
)
FORBIDDEN_BODY_PATTERN = re.compile(
    r"\b(require|import|process|eval|child_process|globalThis|module|exports|__dirname|__filename|fetch|"
    r"XMLHttpRequest|WebSocket|Function|waitForTimeout)\b"
)
STRING_LITERAL_PATTERN = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")

PLACEHOLDER_DATA_PATTERN = re.compile(r"^<[^<>]+>$")
STOPS_FOR_MISSING_DATA_PATTERN = re.compile(r"\btest\.skip\(\s*true\b")


def missing_test_data(test_case: dict) -> list:
    """Placeholders like <authorized password> that the test case never replaced with a value."""
    found = [str(step.get("testData")).strip() for step in test_case.get("steps", []) if PLACEHOLDER_DATA_PATTERN.match(str(step.get("testData") or "").strip())]
    return list(dict.fromkeys(found))


def script_stops_for_missing_data(script_text: str) -> bool:
    return bool(STOPS_FOR_MISSING_DATA_PATTERN.search(script_text or ""))


def index_controls(app_map: dict) -> dict:
    controls = {}
    for page in app_map.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        for control in page.get("controls", []) or []:
            if isinstance(control, dict) and control.get("elementId"):
                controls[control["elementId"]] = {"page": page, "control": control}
    return controls


def _js(value: str) -> str:
    return json.dumps(value)


def is_safe_locator_expression(expression) -> bool:
    return (
        isinstance(expression, str)
        and expression.startswith("page.")
        and len(expression) <= 400
        and not UNSAFE_CODE_PATTERN.search(expression)
    )


def _unquote_js_string(literal: str) -> str:
    return literal[1:-1].replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


def _state_free_prefix(name: str) -> str | None:
    """'Cart, 1 items' / 'My cart, number of items: 0' name a control by its current
    state; the part before the first comma, colon or bracket is the stable name."""
    base = re.split(r"[,:(]", name, maxsplit=1)[0].strip()
    return base if base and base != name.strip() and len(base) >= 3 else None


def _js_regex_prefix(text: str) -> str:
    """A JavaScript regex literal matching text at the start of a name (case-insensitive)."""
    return "/^" + re.sub(r"([\\/.*+?^${}()|\[\]])", r"\\\1", text.strip()) + r"(?!\w)/i"


def build_locator_candidates(control: dict) -> list:
    """Ordered locator expressions for one scanned control: the scan's own
    candidates first (verified ones before unverified), then fallbacks derived
    from the same scan data (role+name, test id, id, label, visible text)."""
    technical = control.get("technical") or {}
    recorded = [lc for lc in (technical.get("locatorCandidates") or []) if isinstance(lc, dict)]
    recorded.sort(key=lambda lc: 0 if lc.get("verification") == "VERIFIED" else 1)

    # Only a name the browser really reports counts as an accessible name; the
    # label a person sees (humanName) is not one and would match other controls.
    accessible_name = (technical.get("accessibleName") or "").strip()
    role = (technical.get("role") or "").strip()
    control_type = (control.get("type") or "").lower()
    human_name = (control.get("humanName") or "").strip()
    is_field = any(word in control_type for word in FIELD_TYPE_WORDS)
    is_option = "option" in control_type

    verified, unverified, fragile = [], [], []
    for lc in recorded:
        expression = lc.get("locator")
        if not is_safe_locator_expression(expression):
            continue
        if is_field and not is_option and OPTION_LOCATOR_PATTERN.match(expression):
            continue  # the scan pointed a dropdown at one of its own options
        if lc.get("verification") == "VERIFIED":
            group = verified
        elif FRAGILE_LOCATOR_PATTERN.search(expression):
            group = fragile  # tied to what the control shows or to its position: tried last
        else:
            group = unverified
        role_name = ROLE_NAME_LOCATOR_PATTERN.match(expression)
        if role_name:
            group.append(f"page.getByRole({role_name.group(1)}, {{ name: {role_name.group(2)}, exact: true }})")
        group.append(expression)
        if role_name:
            stable = _state_free_prefix(_unquote_js_string(role_name.group(2)))
            if stable:
                group.append(f"page.getByRole({role_name.group(1)}, {{ name: {_js_regex_prefix(stable)} }})")

    derived = []
    if technical.get("testId"):
        derived.append(f"page.getByTestId({_js(technical['testId'])})")
    if re.fullmatch(r"[a-z]+", role) and accessible_name:
        derived.append(f"page.getByRole({_js(role)}, {{ name: {_js(accessible_name)}, exact: true }})")
        derived.append(f"page.getByRole({_js(role)}, {{ name: {_js(accessible_name)} }})")
        stable = _state_free_prefix(accessible_name)
        if stable:
            derived.append(f"page.getByRole({_js(role)}, {{ name: {_js_regex_prefix(stable)} }})")
    if technical.get("domId"):
        derived.append(f"page.locator({_js('[id=' + json.dumps(technical['domId']) + ']')})")
    if human_name and control_type in ("textbox", "input", "searchbox", "combobox", "select", "checkbox", "radio", "textarea"):
        derived.append(f"page.getByLabel({_js(human_name)}, {{ exact: true }})")
    if human_name and re.fullmatch(r"[a-z]+", role) and role in FIELD_ROLES:
        # The innermost form group whose label starts with the field's name,
        # for controls (such as a custom dropdown) that have no accessible name.
        derived.append(
            f"page.getByRole('group').filter({{ has: page.getByText({_js_regex_prefix(human_name)}) }}).last().getByRole({_js(role)})"
        )
    label_follow = None
    if human_name and is_field:
        # The first control after the field's label: independent of what the control currently shows.
        label_follow = f"page.getByText({_js_regex_prefix(human_name)}).locator({_js(NEXT_CONTROL_XPATH)})"
    if human_name and not is_field:
        derived.append(f"page.getByText({_js(human_name)}, {{ exact: true }})")

    # Order: what the scan verified, then names the browser reports (role+name, test id, id, label),
    # then the control that follows the field's label, and last the locators tied to what the control
    # currently shows or where it sits (".nth(2)", "-- Select --"). Some sites put the floating label
    # after its input, so "the control after the label" must never outrank a real name.
    candidates = verified + unverified
    for expression in derived:
        if is_safe_locator_expression(expression):
            candidates.append(expression)
    candidates += ([label_follow] if label_follow else []) + fragile

    unique = []
    seen = set()
    for expression in candidates:
        canonical = expression.replace('"', "'")  # the scan writes 'x', derived locators "x": same locator
        if canonical not in seen:
            seen.add(canonical)
            unique.append(expression)
    return unique[:MAX_LOCATORS_PER_ELEMENT]


def _element_entry(element_id: str, controls: dict) -> dict | None:
    control = controls[element_id]["control"]
    page = controls[element_id]["page"]
    candidates = build_locator_candidates(control)
    if not candidates:
        return None
    return {
        "name": control.get("humanName") or element_id,
        "type": control.get("type"),
        "page": page.get("name") or page.get("url"),
        "visibleContext": control.get("visibleContext"),
        "candidates": candidates,
    }


def build_elements_for_test_case(test_case: dict, controls: dict) -> dict:
    """The controls the test case's steps name."""
    elements = {}
    for step in test_case.get("steps", []):
        element_id = step.get("sourceElementId")
        if not element_id or element_id in elements or element_id not in controls:
            continue
        entry = _element_entry(element_id, controls)
        if entry:
            elements[element_id] = entry
    return elements


def build_all_elements(controls: dict) -> dict:
    """Every scanned control that has a usable locator, so an expected result
    (a cart badge, a heading) can use a real scanned control instead of a guess."""
    elements = {}
    for element_id in controls:
        entry = _element_entry(element_id, controls)
        if entry:
            elements[element_id] = entry
    return elements


def used_element_ids(body: str) -> list:
    return list(dict.fromkeys(match[1] for match in re.findall(r"""\bel\(\s*(['"])(.*?)\1\s*\)""", body)))


SCRIPT_HELPER = """async function findElement(page, id) {
  const entry = ELEMENTS[id];
  if (!entry) {
    throw new Error('Unknown scanned element: ' + id);
  }
  const started = Date.now();
  const deadline = started + 20000;
  let visibleMatch = null;
  let hiddenMatch = null;
  while (Date.now() < deadline) {
    for (const makeLocator of entry.candidates) {
      try {
        const locator = makeLocator(page);
        const matches = await locator.count();
        if (matches === 0) {
          continue;
        }
        const first = locator.first();
        if (!(await first.isVisible())) {
          hiddenMatch = hiddenMatch || first;
        } else if (matches === 1) {
          return first; // visible and unambiguous
        } else {
          visibleMatch = visibleMatch || first; // visible, but other elements match too
        }
      } catch (error) {
        // This locator does not fit the current page; the next one is tried.
      }
    }
    if (visibleMatch && Date.now() - started > 3000) {
      return visibleMatch;
    }
    await page.waitForTimeout(500);
  }
  if (visibleMatch || hiddenMatch) {
    return visibleMatch || hiddenMatch;
  }
  throw new Error('"' + entry.name + '" (' + id + ') was not found with any of its ' + entry.candidates.length + ' locator(s).');
}

// Text checks that ignore case, spacing, punctuation and element boundaries, so
// "Sort By" beside "Price Low To High" matches 'Sort By Price Low To High' and
// "(646) 867-5309" matches '6468675309'.
const squash = (text) => String(text).toLowerCase().replace(/[^a-z0-9]/g, '');
const asList = (phrase) => (Array.isArray(phrase) ? phrase : [phrase]).map(squash).filter((p) => p.length > 0);

// The text a user can read. withNames also adds accessible names (aria-label, alt, title),
// which is how an icon-only control such as a logo button is named.
async function visibleText(target, withNames) {
  try {
    const locator = typeof target.mainFrame === 'function' ? target.locator('body') : target; // a Page, or a Locator
    let text = await locator.innerText({ timeout: 2000 });
    if (withNames) {
      const names = await locator.evaluate((root) => Array.from(root.querySelectorAll('[aria-label], img[alt], [title]'))
        .map((node) => node.getAttribute('aria-label') || node.getAttribute('alt') || node.getAttribute('title') || ''));
      text += ' ' + names.join(' ');
    }
    return squash(text);
  } catch (error) {
    return '';
  }
}

async function expectText(target, phrase) {
  const wanted = asList(phrase);
  await expect.poll(async () => {
    const shown = await visibleText(target, true);
    return wanted.some((p) => shown.includes(p));
  }, { message: 'Expected to see: ' + [].concat(phrase).join(' / ') }).toBe(true);
}

async function expectNoText(target, phrase) {
  const unwanted = asList(phrase);
  await expect.poll(async () => {
    const shown = await visibleText(target, false);
    return unwanted.some((p) => shown.includes(p));
  }, { message: 'Expected NOT to see: ' + [].concat(phrase).join(' / ') }).toBe(false);
}

async function expectValue(locator, value) {
  const wanted = asList(value);
  const expectEmpty = wanted.length === 0; // an empty phrase means "the field is empty"
  await expect.poll(async () => {
    let current = '';
    try {
      current = await locator.inputValue({ timeout: 2000 });
      // A native <select> reports its option value ("hilo"); the label ("Price (high to low)") is what a user sees.
      current += ' ' + await locator.evaluate((node) => (node.tagName === 'SELECT'
        ? Array.from(node.selectedOptions).map((option) => option.textContent).join(' ') : ''));
    } catch (error) {
      current = await locator.innerText({ timeout: 2000 }).catch(() => '');
    }
    const shown = squash(current);
    return expectEmpty ? shown === '' : wanted.some((p) => shown.includes(p));
  }, { message: 'Expected the field to hold: ' + (expectEmpty ? '(empty)' : [].concat(value).join(' / ')) }).toBe(true);
}"""


def render_elements_block(elements: dict) -> str:
    lines = ["const ELEMENTS = {"]
    for element_id, element in elements.items():
        lines.append(f"  {_js(element_id)}: {{")
        lines.append(f"    name: {_js(element['name'])},")
        lines.append("    candidates: [")
        for expression in element["candidates"]:
            lines.append(f"      (page) => {expression},")
        lines.append("    ],")
        lines.append("  },")
    lines.append("};")
    return "\n".join(lines)


def assemble_script(test_case: dict, generation_run_id: str, elements: dict, body: str) -> str:
    title = f"{test_case['id']} {test_case.get('title', '')}".strip()
    indented_body = "\n".join(("  " + line) if line.strip() else "" for line in body.strip().splitlines())
    return "\n".join(
        [
            f"// TestMax generated test script for {test_case['id']} ({test_case.get('testCaseType', '')}).",
            f"// Source test case set: {generation_run_id}",
            "const { test, expect } = require('@playwright/test');",
            "",
            "// Scanned controls. Each lists locators in the order they are tried.",
            render_elements_block(elements),
            "",
            SCRIPT_HELPER,
            "",
            f"test({_js(title)}, async ({{ page }}) => {{",
            "  const el = (id) => findElement(page, id);",
            "  // A hover menu opened by the last click can stay open and cover the next control,",
            "  // so after every page navigation the pointer is moved to a neutral spot.",
            "  page.on('framenavigated', (frame) => {",
            "    if (frame === page.mainFrame()) {",
            "      page.mouse.move(1, 400).catch(() => {});",
            "    }",
            "  });",
            indented_body,
            "});",
            "",
        ]
    )


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_body(body: str, test_case: dict, elements: dict) -> list:
    issues = []
    if not body.strip():
        return ["The script body is empty."]
    if "`" in body:
        issues.append("Template literals (backticks) are not allowed; use plain quotes.")

    without_strings = STRING_LITERAL_PATTERN.sub('""', body)
    without_strings = re.sub(r"//[^\n]*", "", without_strings)
    for match in sorted(set(m.group(0) for m in FORBIDDEN_BODY_PATTERN.finditer(without_strings))):
        issues.append(f"'{match}' is not allowed in the script body.")
    # `(?<![\w.])` keeps a regular expression's own `/x/.test(...)` from counting.
    if re.search(r"(?<![\w.])test\s*(\(|\.describe|\.beforeEach|\.afterEach)", without_strings):
        issues.append("Do not write test(...) or hooks; only the body of the test is needed.")

    if re.search(r"\b(if|else|switch)\b", without_strings):
        issues.append("Do not branch with if/else; use one auto-waiting expect(...) with a regular expression for either-or outcomes.")
    if re.search(r"\.(isVisible|isHidden|isEnabled|count|textContent|innerText)\(|\bpage\.url\(", without_strings):
        issues.append("Do not assert on immediate values (isVisible(), count(), page.url()); use auto-waiting expect(...) assertions.")

    if re.search(r"\b(parseFloat|parseInt|Number|toBeCloseTo|toBeGreaterThan(OrEqual)?|toBeLessThan(OrEqual)?)\b|\.(evaluate|evaluateAll|\$eval|\$\$eval)\(", without_strings):
        issues.append(
            "Do not read or compare numbers from the page or run page scripts; assert the visible wording instead, "
            "and mark anything you cannot check with a '// NOT AUTOMATED: <reason>' comment."
        )
    if re.search(r"""getByRole\([^)]*\bname:\s*(['"])[^'"]*[,:][^'"]*\1""", body):
        issues.append(
            "A control name with a comma or colon ('Cart, empty', 'Items: 0') describes its current state and changes; "
            "locate it with a regular expression for the stable start of the name, for example { name: /^Cart/i }."
        )
    if re.search(r"\.(toHaveText|toContainText|toHaveValue)\(|\bexpect\(\s*(await\s+)?page\.getByText\(", without_strings):
        issues.append(
            "Do not use toHaveText/toContainText/toHaveValue or expect(page.getByText(...)); "
            "use expectText(target, phrase) and expectValue(locator, value), which tolerate spacing, punctuation and element breaks."
        )

    literal_ids = re.findall(r"""\bel\(\s*(['"])(.*?)\1\s*\)""", body)
    if len(re.findall(r"\bel\(", without_strings)) != len(literal_ids):
        issues.append("Every el(...) call must be given the element id as a plain quoted string.")
    for _, element_id in literal_ids:
        if element_id not in elements:
            issues.append(f"el('{element_id}') is not a known scanned element for this test case.")

    for step in test_case.get("steps", []):
        number = step.get("stepNo")
        if not re.search(rf"""test\.step\(\s*(['"])Step {number}\b""", body):
            issues.append(f"Step {number} has no test.step('Step {number}: ...').")
    stops_early = re.search(r"\btest\.skip\(\s*true\b", body)
    if "expect(" not in body and not re.search(r"\bexpect(Text|NoText|Value)\(", body) and not stops_early:
        issues.append("The script has no expect(...) assertion.")
    return issues


def check_syntax(script_text: str) -> str | None:
    """Returns None when Node accepts the script's syntax, else the error."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "check.spec.js"
        path.write_text(script_text, encoding="utf-8")
        try:
            result = subprocess.run(
                ["node", "--check", str(path)], capture_output=True, text=True, timeout=30, encoding="utf-8",
            )
        except FileNotFoundError:
            return "Node.js was not found on PATH, so the script could not be checked."
        except subprocess.TimeoutExpired:
            return "The syntax check timed out."
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout or "syntax error").strip().splitlines()
    return "Syntax error: " + " | ".join(line.strip() for line in detail[:6] if line.strip())

def summarize_scanned_pages(app_map: dict) -> list:
    """What the scan saw on each page, as plain visible wording only. Gives the
    model real text to assert on instead of guessing how the page words things."""
    pages = []
    for page in app_map.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        names = [str(c.get("humanName")) for c in page.get("controls", []) or [] if isinstance(c, dict) and c.get("humanName")]
        pages.append({"name": page.get("name"), "url": page.get("url"), "visibleControlNames": names[:80]})
    return pages


def summarize_observed_behaviors(app_map: dict) -> list:
    behaviors = []
    for behavior in (app_map.get("observedBehaviors") or [])[:40]:
        text = behavior if isinstance(behavior, str) else json.dumps(behavior, ensure_ascii=False)
        behaviors.append(text[:300])
    return behaviors


def build_user_message(
    test_case: dict, elements: dict, application_url: str | None, app_map: dict | None = None, other_elements: dict | None = None,
) -> str:
    visible_elements = {
        element_id: {k: v for k, v in element.items() if k != "candidates"} for element_id, element in elements.items()
    }
    other_scanned = {
        element_id: {"name": element["name"], "type": element["type"], "page": element["page"]}
        for element_id, element in (other_elements or {}).items()
        if element_id not in elements
    }
    app_map = app_map or {}
    payload = {
        "applicationUrl": application_url,
        "otherScannedElements": other_scanned,
        "scannedPages": summarize_scanned_pages(app_map),
        "observedBehaviors": summarize_observed_behaviors(app_map),
        "testCase": {
            key: test_case.get(key)
            for key in ("id", "testCaseType", "title", "overview", "preRequisites", "testObjective", "expectedResult", "steps")
        },
        "elements": visible_elements,
    }
    return "Convert this test case into the Playwright test body.\n\n" + json.dumps(payload, indent=2, ensure_ascii=False)


def find_application_url(test_case: dict, app_map: dict) -> str | None:
    for step in test_case.get("steps", []):
        data = str(step.get("testData") or "")
        if data.startswith(("http://", "https://")):
            return data
    return app_map.get("targetUrl")



# ---------------------------------------------------------------------------
# Pilot 2: the generation call (the same flow as Pilot 1's generate_test_script, minus files and OpenAI-only code)
# ---------------------------------------------------------------------------
MAX_OUTPUT_TOKENS = int(os.environ.get("TESTMAX_TSG_MAX_OUTPUT_TOKENS", "32000"))
MAX_ATTEMPTS = max(1, int(os.environ.get("TESTMAX_TSG_MAX_ATTEMPTS", "2")))


class GenerationError(Exception):
    """The message is shown to the user. `retryable` is for transport problems (the job queue retries those)."""

    def __init__(self, message: str, retryable: bool = False, issues: list | None = None, attempts: list | None = None):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.issues = issues or []
        self.attempts = attempts or []


def load_system_prompt() -> tuple[str, str]:
    """(prompt text, sha256 of exactly those bytes). Never edit the prompt file without approval."""
    raw = PROMPT_PATH.read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


# Found in live runs (not in Pilot 1): the helper ignores empty phrases, so expectText(x, '') waits 15 s and always
# fails. Such a body is sent back to the model like any other validation problem.
EMPTY_PHRASE_PATTERN = re.compile(r"\bexpect(?:No)?Text\((?:[^()]|\([^()]*\))*,\s*(?:''|\"\")\s*\)")


# Found in live runs (not in Pilot 1), three more checks that can never pass on the real page:
#  - expectNoText(page, 'error'): a bare generic word also matches page text such as the user name "error_user".
#  - expectNoText(page, <the value the script just typed>): a demo page often lists its own sample login on screen.
#  - el('x') used only to assert it is hidden: el() waits 20 s and throws for a control that is not on the page.
GENERIC_ABSENT_WORDS = {"error", "errors", "invalid", "failed", "failure", "warning", "problem", "wrong", "denied", "incorrect"}
EXPECT_NO_TEXT_PATTERN = re.compile(r"""\bexpectNoText\(\s*page\s*,\s*(?:(['"])(.*?)\1|\[([^\]]*)\])\s*\)""")
FILL_VALUE_PATTERN = re.compile(r"""\.(?:fill|pressSequentially|type)\(\s*(['"])(.+?)\1""")
EL_ID_PATTERN = re.compile(r"""\bel\(\s*(['"])(.*?)\1\s*\)""")
HIDDEN_ASSERTION_PATTERN = re.compile(r"""^\s*\)\s*\)?\s*\.(?:toBeHidden\(|not\.toBeVisible\(|toHaveCount\(\s*0\s*\))""")


def _no_text_phrases(match) -> list:
    if match.group(2) is not None:
        return [match.group(2)]
    return [m[1] for m in re.findall(r"""(['"])(.*?)\1""", match.group(3) or "")]


def extra_issues(body: str) -> list:
    """Pilot 2 additions to Pilot 1's validate_body (kept separate so the ported function stays unchanged)."""
    issues = []
    if EMPTY_PHRASE_PATTERN.search(body):
        issues.append(
            "expectText/expectNoText need a non-empty phrase: an empty one can never match. "
            "To check that nothing is shown, use expectNoText(target, 'the words that would appear') "
            "or mark the check with '// NOT AUTOMATED: <reason>'."
        )

    typed = {m.group(2).strip().lower() for m in FILL_VALUE_PATTERN.finditer(body) if m.group(2).strip()}
    for match in EXPECT_NO_TEXT_PATTERN.finditer(body):
        for phrase in _no_text_phrases(match):
            key = phrase.strip().lower()
            if key in GENERIC_ABSENT_WORDS:
                issues.append(
                    f"expectNoText(page, '{phrase}') also matches harmless page text (for example a sample user name such as 'error_user'). "
                    "Use the exact wording the scan recorded for that message, or assert the effect instead "
                    "(the URL is unchanged, the next page's label is absent), or mark the check '// NOT AUTOMATED: <reason>'."
                )
            elif key in typed:
                issues.append(
                    f"expectNoText(page, '{phrase}') checks for a value this script typed, which the page may also print elsewhere (a hint or sample login). "
                    "To prove a password is masked assert that the field has type 'password' with "
                    "toHaveAttribute('type', 'password') and do not search the page text for the value."
                )

    uses: dict = {}
    for match in EL_ID_PATTERN.finditer(body):
        after = body[match.end():match.end() + 30]
        uses.setdefault(match.group(2), []).append(bool(HIDDEN_ASSERTION_PATTERN.match(after)))
    for element_id, flags in uses.items():
        if flags and all(flags):
            issues.append(
                f"el('{element_id}') is only used to assert that it is hidden, but el() waits and then fails for a control that is not on the current page. "
                "To prove something is not shown use expectNoText(page, 'its visible label') with wording from scannedPages, "
                "or mark the check '// NOT AUTOMATED: <reason>'."
            )
    return list(dict.fromkeys(issues))


def missing_data_message(placeholders: list) -> str:
    needed = ", ".join(placeholders) if placeholders else "test data"
    return (
        f"This script cannot run yet: it stops at its first step that needs {needed}, which the test case never provided. "
        "To fix it: add the login to the requirement Description (for example 'Username: Admin, Password: ...'), "
        "click Scan App, then Generate Test Cases, then generate this script again."
    )


def blocked_message(script_text: str, test_case: dict) -> str | None:
    """The explanation shown instead of letting Execute open a browser that would stop at once."""
    if not script_stops_for_missing_data(script_text):
        return None
    return missing_data_message(missing_test_data(test_case))


def run_generation(converse, *, test_case: dict, app_map: dict, set_id: str, system_prompt: str, max_attempts: int | None = None, on_progress=None, usage: dict | None = None) -> dict:
    """`test_case` has id, title, testCaseType, overview, preRequisites, testObjective, expectedResult, steps.

    One request, plus one bounded retry when the body fails the checks (the problems are sent back, as in Pilot 1).
    """
    from app.llm_types import LLMError

    max_attempts = max_attempts or MAX_ATTEMPTS
    controls = index_controls(app_map)
    elements = build_elements_for_test_case(test_case, controls)
    all_elements = build_all_elements(controls)
    steps_with_gap = [s["stepNo"] for s in test_case.get("steps", []) if s.get("sourceElementId") and s["sourceElementId"] not in elements]
    messages = [{"role": "user", "content": build_user_message(test_case, elements, find_application_url(test_case, app_map), app_map, all_elements)}]

    usage = usage if usage is not None else {}
    usage.update(tokens_in=0, tokens_out=0)
    last_issues: list = []
    attempt_log: list = []
    for attempt in range(max_attempts):
        if on_progress:
            on_progress("Asking the AI to write the script" if attempt == 0 else "Asking the AI to correct the script")
        try:
            result = converse(messages, MAX_OUTPUT_TOKENS)
        except LLMError as err:
            raise GenerationError(err.message, retryable=err.retryable, attempts=attempt_log) from None
        raw = result.text or ""
        usage["tokens_in"] += result.tokens_in or 0
        usage["tokens_out"] += result.tokens_out or 0
        if result.truncated:
            raise GenerationError(f"The AI ran out of output space ({MAX_OUTPUT_TOKENS} tokens) before finishing the script.", attempts=attempt_log)

        body = strip_code_fences(raw)
        last_issues = validate_body(body, test_case, {**all_elements, **elements}) + extra_issues(body)
        if not last_issues:
            # Only the controls the script actually uses are written into it.
            script_elements = {
                element_id: {**all_elements, **elements}[element_id]
                for element_id in dict.fromkeys(list(elements) + used_element_ids(body))
            }
            candidate = assemble_script(test_case, set_id, script_elements, body)
            syntax_error = check_syntax(candidate)
            if syntax_error:
                last_issues = [syntax_error]
            else:
                # One entry per distinct reason (the model may repeat a note on every step).
                not_automated = list(dict.fromkeys(n.strip()[:200] for n in re.findall(r"//\s*NOT AUTOMATED:?([^\n]*)", body) if n.strip()))
                return {
                    "script": candidate,
                    "elements_used": sorted(script_elements),
                    "steps_without_locator": steps_with_gap,
                    "not_automated": not_automated,
                    "blocked_message": blocked_message(candidate, test_case),
                    "tokens_in": usage["tokens_in"],
                    "tokens_out": usage["tokens_out"],
                    "attempts": attempt + 1,
                }
        attempt_log.append({"raw_answer": raw, "issues": last_issues})
        if attempt < max_attempts - 1:
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "That script body failed these checks: " + "; ".join(last_issues) + ". Return ONLY the corrected JavaScript body, following every rule."},
            ]
    raise GenerationError("The generated script failed validation: " + "; ".join(last_issues[:5]), issues=last_issues, attempts=attempt_log)
