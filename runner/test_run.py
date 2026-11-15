"""Runs one generated test script with Playwright Test on this machine and reports what happened.

This is the runner side of the `run_script` job. It ports Pilot 1's Run (tsg_engine.start_test_run): the same
`node @playwright/test/cli.js test ... --workers 1` command, the same Playwright settings (installed Chrome, 180 s test
timeout, 15 s expect timeout), the same "a skipped test is not a pass" rule, and a child process that never sees API
keys. New in Pilot 2: the script comes from the server, the result (per-step results, screenshots, the log) goes back
to the server, and a user's Cancel stops the browser.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
TEST_RUNTIME = RUNNER_DIR / "test-runtime"
PLAYWRIGHT_CLI = TEST_RUNTIME / "node_modules" / "@playwright" / "test" / "cli.js"
RUNS_DIR = TEST_RUNTIME / "runs"

MAX_RUN_SECONDS = int(os.environ.get("TESTMAX_RUN_MAX_SECONDS", "600"))
MAX_LOG_CHARS = 200_000
MAX_SCREENSHOTS = 8
MAX_SCREENSHOT_BYTES = 1_500_000
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SAFE_FILE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}\.spec\.js$")
STEP_LINE = re.compile(r"›\s*(Step \d+[^()]*?)\s*(?:\([\d.]+m?s\))?\s*$")

# Pilot 1's playwright.config.js, with the paths of one run folder.
CONFIG = """const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: '.',
  outputDir: 'artifacts',
  timeout: 180000,
  expect: { timeout: 15000 },
  workers: 1,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: 'report.json' }]],
  use: {
    channel: 'chrome',
    headless: process.env.TESTMAX_HEADLESS === '1',
    actionTimeout: 20000,
    navigationTimeout: 45000,
    screenshot: 'on',
    launchOptions: { slowMo: Number(process.env.TESTMAX_SLOWMO || 0) },
  },
});
"""


class RunSetupError(Exception):
    """The run could not start (Node or Playwright Test missing). The message is shown to the user."""


def clean_environment(headless: bool) -> dict:
    """The test process never receives AI keys or other secrets."""
    env = {k: v for k, v in os.environ.items() if not re.search(r"API_KEY|OPENAI|ANTHROPIC|SECRET|TOKEN", k, re.IGNORECASE)}
    env["FORCE_COLOR"] = "0"
    env.pop("NO_COLOR", None)  # Node warns when both are set
    env["PLAYWRIGHT_LIST_PRINT_STEPS"] = "1"  # the list reporter prints each test.step as it finishes (live progress)
    env["TESTMAX_HEADLESS"] = "1" if headless else "0"
    env["TESTMAX_SLOWMO"] = "0" if headless else env.get("TESTMAX_SLOWMO", "300")  # slow a visible run down so it can be watched
    return env


def classify_run(exit_code: int, output: str) -> str:
    """Playwright exits 0 for a test that skipped itself, which is not a pass."""
    if exit_code != 0:
        return "failed"
    if re.search(r"\b\d+ skipped\b", output) and not re.search(r"\b\d+ passed\b", output):
        return "skipped"
    return "passed"


def kill_tree(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=20)
        else:
            os.killpg(os.getpgid(process.pid), 9)
    except Exception:  # noqa: BLE001 - best effort; the process may already be gone
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass


def _flatten_steps(steps: list, out: list) -> None:
    for step in steps or []:
        title = step.get("title", "")
        if re.match(r"Step \d+", title):
            error = step.get("error") or {}
            message = ANSI.sub("", error.get("message", "")) if error else ""
            out.append({"title": title, "status": "failed" if error else "passed", "duration_ms": int(step.get("duration", 0)), "error": message[:4000]})
        else:
            _flatten_steps(step.get("steps") or [], out)


def read_report(report_path: Path) -> dict:
    """Per-step results, the first error and the screenshot files of the one test, from Playwright's JSON report."""
    out = {"steps": [], "error": "", "attachments": [], "duration_ms": None, "status": None}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    suites = report.get("suites") or []
    results = []

    def walk(items):
        for suite in items:
            for spec in suite.get("specs", []):
                for test in spec.get("tests", []):
                    results.extend(test.get("results", []))
            walk(suite.get("suites", []))

    walk(suites)
    if not results:
        errors = report.get("errors") or []
        if errors:
            out["error"] = ANSI.sub("", errors[0].get("message", ""))[:4000]
        return out
    result = results[-1]
    _flatten_steps(result.get("steps"), out["steps"])
    err = result.get("error") or (result.get("errors") or [{}])[0] or {}
    out["error"] = ANSI.sub("", err.get("message", ""))[:4000]
    out["duration_ms"] = result.get("duration")
    out["status"] = result.get("status")
    out["attachments"] = [a for a in result.get("attachments", []) if a.get("path") and str(a.get("contentType", "")).startswith("image/")]
    return out


def collect_screenshots(attachments: list, work_dir: Path) -> list:
    shots = []
    for attachment in attachments[:MAX_SCREENSHOTS]:
        path = Path(attachment["path"])
        try:
            path.resolve().relative_to(work_dir.resolve())  # only files inside this run's folder
            data = path.read_bytes()
        except (OSError, ValueError):
            continue
        if len(data) > MAX_SCREENSHOT_BYTES:
            continue
        shots.append({"name": attachment.get("name") or "screenshot", "content_type": attachment.get("contentType", "image/png"), "data": base64.b64encode(data).decode("ascii")})
    return shots


def execute(job: dict, *, headless: bool, cancelled, report=lambda message: None) -> dict:
    """Run the script of `job` and return the result dict for the server. Raises RunSetupError if it cannot start."""
    payload = job["payload"]
    code = payload.get("code") or ""
    file_name = payload.get("file_name") or "script.spec.js"
    if not code.strip():
        raise RunSetupError("The job has no script to run.")
    if not SAFE_FILE_NAME.match(file_name):
        file_name = "script.spec.js"
    if shutil.which("node") is None:
        raise RunSetupError("Node.js was not found on this machine. Install Node.js 20 or newer, then try again.")
    if not PLAYWRIGHT_CLI.exists():
        raise RunSetupError("Playwright Test is not installed on this runner. In the runner folder run: cd runner/test-runtime && npm install")

    work_dir = RUNS_DIR / f"{uuid.uuid4().hex[:10]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / file_name).write_text(code, encoding="utf-8")
    (work_dir / "playwright.config.js").write_text(CONFIG, encoding="utf-8")
    command = ["node", str(PLAYWRIGHT_CLI), "test", file_name, "--config", "playwright.config.js", "--workers", "1"]
    if not headless:
        command.append("--headed")

    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    lines: list[str] = []
    report("Starting Chrome")
    try:
        process = subprocess.Popen(
            command, cwd=work_dir, env=clean_environment(headless), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=(os.name != "nt"),
        )
    except OSError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RunSetupError(f"Could not start Playwright Test: {exc}") from None

    def read_output() -> None:
        for raw in process.stdout:
            line = ANSI.sub("", raw.rstrip("\r\n"))
            lines.append(line)
            match = STEP_LINE.search(line)
            if match:
                report(f"Finished: {match.group(1)}")

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    aborted = timed_out = False
    while process.poll() is None:
        if cancelled():
            aborted = True
            kill_tree(process)
            break
        if time.monotonic() - t0 > MAX_RUN_SECONDS:
            timed_out = True
            kill_tree(process)
            break
        time.sleep(0.4)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        kill_tree(process)
    reader.join(timeout=10)
    exit_code = process.returncode if process.returncode is not None else -1

    parsed = read_report(work_dir / "report.json")
    output = "\n".join(lines)
    if len(output) > MAX_LOG_CHARS:
        output = "[earlier output cut]\n" + output[-MAX_LOG_CHARS:]
    if aborted:
        status = "aborted"
        output += "\n[Stopped: cancelled by the user]"
    elif timed_out:
        status = "failed"
        output += f"\n[Stopped: the run took longer than {MAX_RUN_SECONDS} seconds]"
    else:
        status = classify_run(exit_code, output)
    shots = collect_screenshots(parsed["attachments"], work_dir)
    if os.environ.get("TESTMAX_KEEP_RUNS") != "1":
        shutil.rmtree(work_dir, ignore_errors=True)
    finished = datetime.now(timezone.utc)
    return {
        "status": status,
        "exit_code": exit_code,
        "headed": not headless,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round(time.monotonic() - t0, 2),
        "test_duration_ms": parsed["duration_ms"],
        "output": output,
        "steps": parsed["steps"],
        "error": parsed["error"],
        "screenshots": shots,
    }
