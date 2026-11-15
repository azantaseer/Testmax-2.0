"""TestMax runner.

  python -m runner pair --server http://127.0.0.1:8000     connect this machine (device-code pairing)
  python -m runner run [--headed] [--capacity N]            wait for jobs and run them
  python -m runner setup                                     open the local setup page (pair, start, stop, settings)

Outbound HTTPS only. Holds a per-organization runner key (runner.json) and no AI key.
"""
import argparse
import asyncio
import platform
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from runner import config
from runner.api import ApiError, ServerClient
from runner.mcp_proc import McpStartError, PlaywrightMcp, cleanup_orphans
from runner.scan_loop import RetryableScanError, run_scan
from runner.test_run import RunSetupError, execute as execute_script

VERSION = "0.3.0"
OS_NAME = f"{platform.system()} {platform.release()}"


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


# ------------------------------------------------------------------ pairing
def pair_flow(server: str, name: str, capacity: int, on_code=None, cancelled=lambda: False) -> tuple[bool, str]:
    """Device-code pairing, shared by `python -m runner pair` and the setup page.

    on_code(user_code, expires_in_seconds) is called once the server has issued the code the administrator must enter.
    Returns (paired, message); on success the runner key is saved in runner.json.
    """
    server = server.strip().rstrip("/")
    api = ServerClient(server)
    name = (name or platform.node() or "Runner").strip()[:80]
    try:
        start = api.pair_start(name, capacity, OS_NAME, VERSION)
    except ApiError as err:
        return False, f"Could not start pairing: {err.message}"
    if on_code:
        on_code(start["user_code"], start["expires_in"])
    deadline = time.monotonic() + start["expires_in"]
    while time.monotonic() < deadline:
        for _ in range(int(start["interval"] * 10)):  # sleep in short steps so Cancel is quick
            if cancelled():
                return False, "Pairing was cancelled."
            time.sleep(0.1)
        try:
            polled = api.pair_poll(start["device_code"])
        except ApiError as err:
            if err.code == "network":
                continue
            return False, f"Pairing failed: {err.message}"
        if polled["status"] == "approved":
            previous = config.load() or {}
            config.save({**previous, "server": server, "runner_key": polled["runner_key"], "runner_id": polled["runner_id"], "name": name, "capacity": capacity})
            return True, "Paired."
    return False, "The code expired before it was approved."


def cmd_pair(args) -> int:
    def show(code: str, expires_in: int) -> None:
        log(f"Your pairing code is:  {code}")
        log("An administrator enters this code on the Runners page in TestMax. Waiting for approval...")

    ok, message = pair_flow(args.server, args.name, args.capacity, on_code=show)
    if ok:
        log(f"Paired. Runner saved to {config.CONFIG_PATH.name}. Start it with:  python -m runner run")
        return 0
    log(message + (" Run the command again." if "expired" in message else ""))
    return 1


# ------------------------------------------------------------------ jobs
def run_stub(api: ServerClient, job: dict, cancel: threading.Event) -> None:
    """A tiny job used to test pairing, leases and recovery without a browser."""
    seconds = int(job["payload"].get("seconds", 1))
    for i in range(seconds):
        if cancel.is_set():
            api.fail(job["id"], "Cancelled by the user.")
            return
        api.progress(job["id"], f"Stub working {i + 1}/{seconds}")
        time.sleep(1)
    if job["payload"].get("fail"):
        api.fail(job["id"], str(job["payload"]["fail"]))
        return
    api.complete(job["id"], {"echo": job["payload"].get("message", "")})


def run_app_scan(api: ServerClient, job: dict, cancel: threading.Event, headless: bool) -> None:
    jid = job["id"]
    last = {"t": 0.0}

    def report(message: str) -> None:
        # Step updates are limited to about one per second; other messages always go through.
        if message.startswith("Scanning:") and time.monotonic() - last["t"] < 0.8:
            return
        last["t"] = time.monotonic()
        try:
            api.progress(jid, message)
        except ApiError:
            pass

    api.progress(jid, "Starting the browser")
    with PlaywrightMcp(headless=headless) as mcp:
        result = asyncio.run(run_scan(api, job, mcp.url, report=report, cancelled=cancel.is_set))
    api.complete(jid, result)


def run_script_job(api: ServerClient, job: dict, cancel: threading.Event, headless: bool) -> None:
    jid = job["id"]
    last = {"t": 0.0}

    def report(message: str) -> None:
        if time.monotonic() - last["t"] < 0.5 and not message.startswith("Starting"):
            return
        last["t"] = time.monotonic()
        try:
            api.progress(jid, message)
        except ApiError:
            pass

    result = execute_script(job, headless=headless, cancelled=cancel.is_set, report=report)
    api.progress(jid, "Sending the report")
    api.complete(jid, result)


def handle_job(api: ServerClient, job: dict, headless: bool) -> None:
    jid = job["id"]
    cancel, lost, done = threading.Event(), threading.Event(), threading.Event()

    def beat() -> None:
        while not done.wait(job.get("heartbeat_seconds", 10)):
            try:
                state = api.job_heartbeat(jid)
            except ApiError as err:
                if err.code != "network":
                    lost.set()
                    cancel.set()
                continue
            if state.get("cancel_requested"):
                cancel.set()
            if not state.get("ok"):  # the server gave the job to someone else: stop quietly
                lost.set()
                cancel.set()

    threading.Thread(target=beat, daemon=True).start()
    log(f"job {jid[:8]} ({job['type']}) started")
    try:
        if job["type"] == "runner_stub":
            run_stub(api, job, cancel)
        elif job["type"] == "app_scan":
            run_app_scan(api, job, cancel, headless)
        elif job["type"] == "run_script":
            run_script_job(api, job, cancel, headless)
        else:
            api.fail(jid, f"This runner does not know the job type '{job['type']}'.")
        log(f"job {jid[:8]} finished")
    except RetryableScanError as err:
        _safe_fail(api, jid, str(err), True)
    except RunSetupError as err:
        _safe_fail(api, jid, str(err), False)
    except McpStartError as err:
        _safe_fail(api, jid, f"The runner could not start the browser tooling: {err}", False)
    except ApiError as err:
        log(f"job {jid[:8]} server error: {err.message}")  # e.g. 409: the job is no longer ours
    except Exception as exc:  # noqa: BLE001
        log(f"job {jid[:8]} crashed: {type(exc).__name__}")
        _safe_fail(api, jid, f"The runner hit an unexpected error ({type(exc).__name__}).", False)
    finally:
        done.set()


def _safe_fail(api: ServerClient, jid: str, message: str, retryable: bool) -> None:
    try:
        api.fail(jid, message, retryable)
    except ApiError:
        pass


# ------------------------------------------------------------------ main loop
def cmd_run(args) -> int:
    cfg = config.load()
    if not cfg:
        log("This runner is not paired yet. Run:  python -m runner pair --server <url>")
        return 1
    stopped = cleanup_orphans()
    if stopped:
        log(f"stopped {stopped} leftover browser process(es) from a previous run")
    capacity = args.capacity or cfg.get("capacity", 2)
    api = ServerClient(cfg["server"], cfg["runner_key"])
    stop = threading.Event()
    running: set[str] = set()
    lock = threading.Lock()

    def heartbeat_loop() -> None:
        while not stop.is_set():
            try:
                api.heartbeat(capacity, OS_NAME, VERSION)
            except ApiError as err:
                if err.code == "runner_revoked":
                    log("This runner was revoked by an administrator. Stopping.")
                    stop.set()
                    return
            stop.wait(10)

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    pool = ThreadPoolExecutor(max_workers=capacity)

    def work(job: dict) -> None:
        try:
            handle_job(api, job, headless=not args.headed)
        finally:
            with lock:
                running.discard(job["id"])

    log(f"runner '{cfg['name']}' online ({cfg['server']}), {capacity} slot(s), {'headed' if args.headed else 'headless'} browser")
    try:
        while not stop.is_set():
            with lock:
                free = capacity - len(running)
            job = None
            if free > 0:
                try:
                    job = api.claim(free)
                except ApiError as err:
                    if err.code == "runner_revoked":
                        log("This runner was revoked by an administrator. Stopping.")
                        break
            if job:
                with lock:
                    running.add(job["id"])
                pool.submit(work, job)
                continue
            stop.wait(2)
    except KeyboardInterrupt:
        log("stopping (running jobs will be recovered by the server)")
    stop.set()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m runner")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pair", help="connect this machine to TestMax")
    p.add_argument("--server", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--capacity", type=int, default=2)
    r = sub.add_parser("run", help="wait for jobs and run them")
    r.add_argument("--headed", action="store_true", help="show the browser window")
    r.add_argument("--capacity", type=int, default=0)
    st = sub.add_parser("setup", help="open the local setup page in your browser")
    st.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    st.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "setup":
        from runner.setup_ui import run_setup

        return run_setup(port=args.port, open_browser=not args.no_browser)
    return cmd_pair(args) if args.command == "pair" else cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
