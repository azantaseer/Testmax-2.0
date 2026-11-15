"""Data-driven test cases: keys for the test data of each step, data variations, and scripts that read their values from a data block.

How it fits together (the AI prompts are not touched):
- Every step that carries real test data gets a stable key (`username`, `password`, `destination_url`, ...). The keys are stored in the
  steps (`dataKey`). DV-1, the default variation, is simply the test data written in the steps.
- More variations live on the test case (`data_sets`): a name and one value per key. An empty value means "use the default".
- After the AI writes a script, `parameterize` replaces each value typed into a step by `DATA["key"]` and adds a data block (`const DATA`).
  Only a string equal to that step's own test data, inside that step, is replaced. Anything else is left as written and reported.
- Each time a script is executed or downloaded, `rewrite_block` writes the chosen variation's current values into the data block.
"""
import json
import re
import uuid

EMPTY = "—"
BLOCK_BEGIN = "// TESTMAX-DATA-BEGIN: the values below are replaced with the chosen data variation each time the script is run or downloaded."
BLOCK_END = "// TESTMAX-DATA-END"
BLOCK_PATTERN = re.compile(re.escape("// TESTMAX-DATA-BEGIN") + r"[^\n]*\n.*?" + re.escape(BLOCK_END), re.DOTALL)
STRING_LITERAL = re.compile(r"'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"")
STEP_START = re.compile(r"""^  await test\.step\(\s*(['"])Step (\d+)\b""")
STEP_END = "  });"
NO_DATA = {"", "-", "—", "n/a", "none", "(empty)", "(none)"}
PLACEHOLDER = re.compile(r"^<[^<>]+>$")
MAX_VALUE = 2000
MAX_NAME = 60
MAX_SETS = 20


def has_value(value) -> bool:
    """True when the step's test data is a real value (not empty, a dash, '(empty)' or a <placeholder>)."""
    text = str(value if value is not None else "").strip()
    return text.lower() not in NO_DATA and not PLACEHOLDER.match(text)


def _slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    return out[:40]


def derive_key(step: dict, used: set) -> str:
    value = str(step.get("testData") or "").strip()
    field = str(step.get("fieldName") or "").strip()
    if value.lower().startswith(("http://", "https://")) and field in ("", EMPTY, "-"):
        base = "destination_url"
    else:
        base = _slug(field) or f"step_{step.get('stepNo')}"
        if base[0].isdigit():
            base = f"field_{base}"
    key, n = base, 2
    while key in used:
        key = f"{base}_{n}"
        n += 1
    return key


def with_keys(steps: list) -> list:
    """The steps with a `dataKey` on every step that has test data (existing keys are kept, new ones are unique). Returns new dicts."""
    used = {s.get("dataKey") for s in steps if s.get("dataKey")}
    out = []
    for step in steps:
        step = dict(step)
        if has_value(step.get("testData")):
            if not step.get("dataKey"):
                step["dataKey"] = derive_key(step, used)
                used.add(step["dataKey"])
        else:
            step.pop("dataKey", None)
        out.append(step)
    return out


def keyed_steps(steps: list) -> list:
    return [s for s in with_keys(steps) if s.get("dataKey")]


def default_values(steps: list) -> dict:
    """DV-1: key -> the test data written in the step."""
    return {s["dataKey"]: str(s["testData"]).strip() for s in keyed_steps(steps)}


def values_for(steps: list, data_set: dict | None) -> dict:
    """The values of one variation; an empty cell falls back to the default."""
    values = default_values(steps)
    if data_set:
        for key, value in (data_set.get("values") or {}).items():
            if key in values and str(value).strip() != "":
                values[key] = str(value)
    return {k: ("" if v.strip() == "(empty)" else v) for k, v in values.items()}


def clean_data_sets(raw, keys: list) -> list:
    """Validates what the screen sends: at most MAX_SETS variations, each with a name and a value for (some of) the known keys."""
    from app.errors import AppError

    if not isinstance(raw, list) or len(raw) > MAX_SETS:
        raise AppError(422, "invalid_data_sets", f"At most {MAX_SETS} data variations are allowed.")
    out, ids = [], set()
    for number, item in enumerate(raw, start=2):
        item = item if isinstance(item, dict) else {}
        set_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(item.get("id") or ""))[:24] or f"dv{number}-{uuid.uuid4().hex[:6]}"
        while set_id in ids or set_id == "default":
            set_id += "x"
        ids.add(set_id)
        name = " ".join(str(item.get("name") or "").split())[:MAX_NAME]
        values = {}
        for key, value in (item.get("values") or {}).items():
            if key not in keys:
                raise AppError(422, "invalid_data_sets", f"'{key}' is not a test data key of this test case.")
            text = "".join(ch for ch in str(value if value is not None else "") if ch == "\t" or ch >= " ")
            if len(text) > MAX_VALUE:
                raise AppError(422, "invalid_data_sets", f"A value for '{key}' is longer than {MAX_VALUE} characters.")
            values[key] = text
        out.append({"id": set_id, "name": name, "values": values})
    return out


def set_label(index: int, data_set: dict | None) -> str:
    """DV-1 (Default), DV-2 - Locked User ..."""
    if data_set is None:
        return "DV-1 (Default)"
    return f"DV-{index} — {data_set['name']}" if data_set.get("name") else f"DV-{index}"


# ---------------------------------------------------------------- the data block in a script
def render_block(values: dict) -> str:
    lines = [BLOCK_BEGIN, "const DATA = {"]
    lines += [f"  {json.dumps(key)}: {json.dumps(value)}," for key, value in values.items()]
    lines += ["};", BLOCK_END]
    return "\n".join(lines)


def is_data_driven(code: str) -> bool:
    return bool(BLOCK_PATTERN.search(code or ""))


def block_keys(code: str) -> list:
    match = BLOCK_PATTERN.search(code or "")
    return re.findall(r'^  "([^"]+)":', match.group(0), re.MULTILINE) if match else []


def rewrite_block(code: str, values: dict) -> str:
    """Writes the values of the keys the script reads into its data block (a script without a block is returned as it is)."""
    keys = block_keys(code)
    if not keys:
        return code
    block = render_block({k: values[k] for k in keys if k in values} | {k: "" for k in keys if k not in values})
    return BLOCK_PATTERN.sub(lambda _m: block, code, count=1)


def _literal_value(literal: str) -> str:
    inner = literal[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), inner)


def parameterize(code: str, steps: list) -> dict:
    """Turns the typed-in values of a generated script into `DATA["key"]`.

    Returns {"code", "keys", "gaps"}: `keys` are the keys now read from the data block; `gaps` are steps whose value could not be found
    in the script (the AI wrote something else), listed as [{"stepNo", "key"}]. A script that already has a data block is returned unchanged.
    """
    if is_data_driven(code):
        return {"code": code, "keys": [], "gaps": []}
    keyed = {int(s["stepNo"]): s for s in keyed_steps(steps)}
    if not keyed:
        return {"code": code, "keys": [], "gaps": []}
    lines = code.split("\n")
    used, replaced_steps = [], set()
    i = 0
    while i < len(lines):
        match = STEP_START.match(lines[i])
        if not match or int(match.group(2)) not in keyed:
            i += 1
            continue
        number = int(match.group(2))
        step = keyed[number]
        wanted = str(step["testData"]).strip()
        j = i + 1
        while j < len(lines) and lines[j] != STEP_END:
            before = lines[j]
            lines[j] = STRING_LITERAL.sub(lambda m: f'DATA["{step["dataKey"]}"]' if _literal_value(m.group(0)).strip() == wanted else m.group(0), before)
            if lines[j] != before:
                replaced_steps.add(number)
            j += 1
        i = j + 1
    used = [keyed[n]["dataKey"] for n in sorted(replaced_steps)]
    gaps = [{"stepNo": n, "key": keyed[n]["dataKey"]} for n in sorted(keyed) if n not in replaced_steps]
    if not used:
        return {"code": code, "keys": [], "gaps": gaps}
    new_code = "\n".join(lines)
    values = default_values(steps)
    block = render_block({k: values[k] for k in dict.fromkeys(used)})
    anchor = "const { test, expect } = require('@playwright/test');"
    if anchor not in new_code:
        return {"code": code, "keys": [], "gaps": gaps}
    new_code = new_code.replace(anchor, anchor + "\n\n" + block, 1)
    return {"code": new_code, "keys": list(dict.fromkeys(used)), "gaps": gaps}


def only_data_changed(old_steps: list, new_steps: list, script_keys: list) -> bool:
    """True when two versions of the steps differ only in the test data of steps whose key the script reads from its data block."""
    if len(old_steps) != len(new_steps):
        return False
    for old, new in zip(old_steps, new_steps):
        a, b = {k: v for k, v in old.items() if k != "testData"}, {k: v for k, v in new.items() if k != "testData"}
        if a != b:
            return False
        if old.get("testData") != new.get("testData") and old.get("dataKey") not in script_keys:
            return False
    return True


# ---------------------------------------------------------------- choosing variations for a run
SECRET_KEY = re.compile(r"pass|pwd|secret|token|pin$|cvv|cvc|api_?key", re.IGNORECASE)


def mask_values(values: dict) -> dict:
    """The values shown in the run report: keys that look like secrets are hidden."""
    return {k: ("••••••" if SECRET_KEY.search(k) and v else v) for k, v in values.items()}


def choose(data_sets: list, ids: list | None) -> list:
    """[(id, number, data_set or None)] for the requested variation ids; 'default' is DV-1. Unknown ids are an error."""
    from app.errors import AppError

    out = []
    for set_id in dict.fromkeys(ids or ["default"]):
        if set_id == "default":
            out.append(("default", 1, None))
            continue
        index = next((i for i, d in enumerate(data_sets or []) if d.get("id") == set_id), None)
        if index is None:
            raise AppError(404, "data_set_not_found", "One of the chosen data variations no longer exists. Reload the page and choose again.")
        out.append((set_id, index + 2, data_sets[index]))
    return out


def run_code(code: str, steps: list, data_set: dict | None) -> str:
    """The script text to execute or download: its data block filled with the variation's current values."""
    return rewrite_block(code, values_for(steps, data_set)) if is_data_driven(code) else code
