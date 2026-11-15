"""Starts one Playwright MCP server (own browser, own port) for one scan, and always stops it afterwards."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

RUNNER_DIR = Path(__file__).resolve().parent
PIDS_FILE = RUNNER_DIR / "children.json"  # browser tooling started by this runner; cleaned up if the runner is killed
CLI = RUNNER_DIR / "node_modules" / "@playwright" / "mcp" / "cli.js"


class McpStartError(Exception):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        proc.kill()


def _read_pids() -> list[int]:
    try:
        return [int(x) for x in json.loads(PIDS_FILE.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []


def _write_pids(pids: list[int]) -> None:
    try:
        PIDS_FILE.write_text(json.dumps(pids), encoding="utf-8")
    except OSError:
        pass


def _command_line(pid: int) -> str:
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, timeout=20,
            )
            return out.stdout
        return Path(f"/proc/{pid}/cmdline").read_text(errors="ignore").replace("\x00", " ")
    except Exception:  # noqa: BLE001
        return ""


def cleanup_orphans() -> int:
    """After a crash or a kill, browser tooling from the previous run may still be alive. Stop it (only our own: the
    pid must still belong to a Playwright MCP process). Returns how many were stopped."""
    stopped = 0
    for pid in _read_pids():
        if "playwright" in _command_line(pid).lower():
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
                else:
                    os.killpg(pid, 9)
                stopped += 1
            except Exception:  # noqa: BLE001
                pass
    _write_pids([])
    return stopped


class PlaywrightMcp:
    def __init__(self, headless: bool = True, startup_seconds: int = 45):
        self.headless = headless
        self.startup_seconds = startup_seconds
        self.proc: subprocess.Popen | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/mcp"  # the MCP server only accepts localhost as its host name

    def __enter__(self):
        if not CLI.exists():
            raise McpStartError("Playwright MCP is not installed. Run `npm install` in the runner folder.")
        self.port = _free_port()
        cmd = ["node", str(CLI), "--browser", "chrome", "--isolated", "--port", str(self.port)]
        if self.headless:
            cmd.append("--headless")
        env = {k: v for k, v in os.environ.items() if "API_KEY" not in k.upper()}  # never hand AI keys to the browser tooling
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, **kwargs)
        _write_pids(sorted(set(_read_pids()) | {self.proc.pid}))
        deadline = time.monotonic() + self.startup_seconds
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise McpStartError("The browser tooling stopped right after starting (is Node installed?).")
            try:
                socket.create_connection(("localhost", self.port), timeout=0.5).close()  # tries IPv4 and IPv6
                return self
            except OSError:
                pass
            time.sleep(0.3)
        self.__exit__()
        raise McpStartError("The browser tooling did not start in time.")

    def __exit__(self, *exc):
        if self.proc is not None:
            _kill_tree(self.proc)
            _write_pids([p for p in _read_pids() if p != self.proc.pid])
        return False
