"""Test batches: several generated scripts joined into ONE Playwright test, so they share one browser session.

The scripts are not changed and the runner is not changed. Each script keeps its own scope (its `ELEMENTS`, helper functions and
`DATA` block live inside a function of its own), and all of them receive the same `page`. The joined test writes a few marker steps
("Step 0 TMX-START 2 TS-002", "... TMX-DONE ...", "... TMX-FAIL ...") between the scripts' own steps. The runner already reports
every step of a test in order, so the server cuts the list at the markers to get one result per script. A screenshot of the
page is attached after each script (named "TMX-<n>").
"""
import json
import re

MAX_ITEMS = 8  # the runner returns at most 8 screenshots: one per script
REQUIRE_LINE = "const { test, expect } = require('@playwright/test');"
TEST_START = re.compile(r"^test\((.*), async \(\{ page \}\) => \{$")
MARKER = re.compile(r"^Step 0 TMX-(START|DONE|FAIL) (\d+)(?: (.*))?$")
SHOT_NAME = re.compile(r"^TMX-(\d+)$")  # kept short: Playwright puts the name in a file path, and Windows stops at 260 characters
SECONDS_PER_SCRIPT = 120  # added to Playwright's test time limit for every script in the batch


def split_script(code: str) -> tuple[list, list]:
    """(the lines before the test, the lines inside the test) of a generated script. Raises ValueError with the reason otherwise."""
    lines = (code or "").replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    starts = [i for i, line in enumerate(lines) if TEST_START.match(line)]
    if len(starts) != 1 or not lines or lines[-1] != "});":
        raise ValueError("it is not in the shape TestMax generates (one test(...) at the end)")
    if REQUIRE_LINE not in lines[: starts[0]]:
        raise ValueError("it does not start with the usual Playwright import")
    preamble = [line for line in lines[: starts[0]] if line != REQUIRE_LINE]
    body = lines[starts[0] + 1 : -1]
    return preamble, body


def _js(text: str) -> str:
    return json.dumps(str(text))


def _indent(lines: list, spaces: int) -> list:
    pad = " " * spaces
    return [(pad + line) if line.strip() else "" for line in lines]


def start_title(n: int, key: str) -> str:
    return f"Step 0 TMX-START {n} {key}"


def build_script(name: str, on_failure: str, items: list, key: str = "") -> str:
    """The joined test file. `items` = [{"key": "TS-001", "code": <the script with its data filled in>}] in run order; `key` is the batch's ID (TB-001).
    Raises ValueError("TS-002: reason") when a script cannot be joined."""
    stop = on_failure != "continue"
    count = len(items)
    one_line = re.sub(r"[\r\n]+", " ", str(name))
    plural = "" if count == 1 else "s"
    mode = "stop" if stop else "continue"
    out = [
        f"// TestMax test batch: {one_line[:200]} ({count} script{plural}, on failure: {mode}).",
        "// The scripts below run one after another in the same browser page. Each keeps its own helpers and data.",
        REQUIRE_LINE,
        "",
        "async function __tmx_shot(page, n, key) {",
        "  try {",
        "    const file = test.info().outputPath('tmx-item-' + n + '.png');",
        "    await page.screenshot({ path: file });",
        "    await test.info().attach('TMX-' + n, { path: file, contentType: 'image/png' });",
        "  } catch (error) {",
        "    // the page may already be closed",
        "  }",
        "}",
        "",
    ]
    for n, item in enumerate(items, start=1):
        try:
            preamble, body = split_script(item["code"])
        except ValueError as exc:
            raise ValueError(f"{item['key']}: {exc}") from None
        out += [f"// ---- Batch item {n}: {item['key']} ----", f"function __tmx_script_{n}() {{"]
        out += _indent(preamble, 2)
        out += ["  return async function (page) {"] + _indent(body, 2) + ["  };", "}", ""]
    out += [
        f"test({_js(('Batch ' + key) if key else 'Batch: ' + one_line[:30])}, async ({{ page }}) => {{",  # short: it becomes part of a file path
        f"  test.setTimeout({180 + SECONDS_PER_SCRIPT * count} * 1000);",
        "  const __failed = [];",
    ]
    for n, item in enumerate(items, start=1):
        key = item["key"]
        out += [
            f"  await test.step({_js(start_title(n, key))}, async () => {{}});",
            "  {",
            "    const __t0 = Date.now();",
            "    try {",
            f"      await __tmx_script_{n}()(page);",
            f"      await __tmx_shot(page, {n}, {_js(key)});",
            f"      await test.step('Step 0 TMX-DONE {n} ' + (Date.now() - __t0), async () => {{}});",
            "    } catch (__error) {",
            f"      await __tmx_shot(page, {n}, {_js(key)});",
            "      try {",
            f"        await test.step('Step 0 TMX-FAIL {n} ' + (Date.now() - __t0), async () => {{ throw __error; }});",
            "      } catch (__ignored) {",
            "        // recorded as a failed step; the batch decides below whether to go on",
            "      }",
            f"      __failed.push({_js(key)});",
            *(["      throw __error;"] if stop else []),
            "    }",
            "  }",
        ]
    out += [
        "  if (__failed.length > 0) {",
        f"    throw new Error(__failed.length + ' of {count} scripts failed: ' + __failed.join(', '));",
        "  }",
        "});",
        "",
    ]
    return "\n".join(out)


def parse_marker(title: str):
    """('START'|'DONE'|'FAIL', n, rest) for a marker step title, else None."""
    match = MARKER.match(title or "")
    return (match.group(1), int(match.group(2)), match.group(3) or "") if match else None


def split_steps(steps: list, count: int) -> dict:
    """Cuts the runner's flat step list at the markers. Returns {n: {started, done, failed, duration_ms, steps, error}} for n = 1..count.

    The steps between a script's START and its DONE / FAIL belong to it. A script that started and neither finished nor failed
    (the run was stopped, timed out or crashed while it ran) counts as started without DONE."""
    out = {n: {"started": False, "done": False, "failed": False, "duration_ms": None, "steps": [], "error": ""} for n in range(1, count + 1)}
    current = None
    for step in steps or []:
        marker = parse_marker(step.get("title", ""))
        if marker is None:
            if current is not None:
                out[current]["steps"].append(step)
            continue
        kind, n, rest = marker
        if n not in out:
            continue
        item = out[n]
        if kind == "START":
            item["started"] = True
            current = n
        elif kind == "DONE":
            item["done"] = True
            item["duration_ms"] = int(rest) if rest.isdigit() else None
            current = None
        else:  # FAIL
            item["failed"] = True
            item["duration_ms"] = int(rest) if rest.isdigit() else None
            item["error"] = step.get("error", "") or ""
            current = None
    return out


OUTPUT_MARKER = re.compile(r"›\s*Step 0 TMX-(START|DONE|FAIL) (\d+)(?: ([^\s(]+))?")


def markers_from_output(output: str) -> list:
    """The marker steps found in the runner's console output. Used when Playwright wrote no report (the run was cancelled or killed):
    the list reporter prints every marker as it finishes, so it still tells which scripts started and which ended."""
    out = []
    for line in str(output or "").splitlines():
        match = OUTPUT_MARKER.search(line)
        if match:
            kind, n, rest = match.group(1), match.group(2), match.group(3) or ""
            out.append({"title": f"Step 0 TMX-{kind} {n} {rest}".rstrip(), "status": "failed" if kind == "FAIL" else "passed", "duration_ms": 0, "error": ""})
    return out
