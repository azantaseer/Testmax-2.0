"""The runner's local setup page: `python -m runner setup` (the downloaded package's start script runs it).

A tiny web page on THIS computer only (127.0.0.1) to pair the runner with TestMax, set it up, start and stop it and read
its log, so nobody has to type commands. Standard library only (no new dependency).

Safety (a web page on localhost can otherwise be driven by any website you visit):
- it listens on 127.0.0.1 only, and every request must carry that exact Host header (stops DNS rebinding);
- every API call needs a random token that is only in the page we serve (stops other sites, which cannot read it);
- a request with a foreign Origin is refused; request bodies are small and strictly checked.
The runner key stays in runner.json (owner-only file); it is never sent to the page.
"""
import collections
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from runner import config
from runner.main import OS_NAME, VERSION, pair_flow
from runner.test_run import kill_tree

ROOT = Path(__file__).resolve().parent.parent
PRESET_FILE = ROOT / "runner-preset.json"  # written into the downloaded package: the server address (no secrets)
PAGE = Path(__file__).with_name("setup_page.html")
MAX_BODY = 10_000
LOG_LINES = 400


# ---------------------------------------------------------------- this computer
def find_chrome() -> str | None:
    candidates: list[Path] = []
    if sys.platform == "win32":
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(var):
                candidates.append(Path(os.environ[var]) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    return next((str(c) for c in candidates if c.exists()), None)


def node_version() -> str | None:
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10, shell=(os.name == "nt"))
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def components_installed() -> bool:
    return (ROOT / "runner" / "node_modules" / "@playwright" / "mcp").exists() and (ROOT / "runner" / "test-runtime" / "node_modules" / "@playwright" / "test").exists()


def read_preset() -> dict:
    try:
        return json.loads(PRESET_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def settings_path() -> Path:
    return config.CONFIG_PATH.with_name(config.CONFIG_PATH.stem + ".settings.json")


def read_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"headed": bool(data.get("headed", False)), "capacity": min(max(int(data.get("capacity", 2) or 2), 1), 8)}


def write_settings(settings: dict) -> None:
    settings_path().write_text(json.dumps(settings), encoding="utf-8")


def valid_server(value: str) -> str | None:
    """The server address as typed, cleaned; None when it is not a usable http(s) address."""
    value = (value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or len(value) > 200 or parsed.path not in ("", "/"):
        return None
    return value


# ---------------------------------------------------------------- the state behind the page
class Controller:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.log: collections.deque = collections.deque(maxlen=LOG_LINES)
        self.proc: subprocess.Popen | None = None
        self.online = False
        self.revoked = False
        self.pairing: dict = {"status": "idle"}
        self.cancel_pairing = threading.Event()
        self.started_at: float | None = None

    # ---- log / process
    def add_log(self, line: str) -> None:
        with self.lock:
            self.log.append(line.rstrip())

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:  # ends when the process ends
            self.add_log(line)
            if "' online (" in line:
                self.online = True
            if "revoked" in line:
                self.revoked = True
        proc.wait()
        with self.lock:
            if self.proc is proc:
                self.online = False
                self.add_log(f"The runner stopped (exit code {proc.returncode}).")

    def start(self) -> str | None:
        """Start `python -m runner run`; returns a problem message or None."""
        with self.lock:
            if self.running():
                return None
            if not config.load():
                return "Connect this computer to TestMax first."
            settings = read_settings()
            cmd = [sys.executable, "-u", "-m", "runner", "run", "--capacity", str(settings["capacity"])]
            if settings["headed"]:
                cmd.append("--headed")
            kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
            try:
                proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **kwargs)
            except OSError as err:
                return f"Could not start the runner ({type(err).__name__})."
            self.proc, self.online, self.revoked, self.started_at = proc, False, False, time.time()
            self.add_log("Starting the runner…")
            threading.Thread(target=self._pump, args=(proc,), daemon=True).start()
            return None

    def stop(self) -> None:
        with self.lock:
            proc, self.proc, self.online = self.proc, None, False
        if proc is not None and proc.poll() is None:
            kill_tree(proc)
            self.add_log("The runner was stopped.")

    # ---- pairing
    def begin_pairing(self, server: str, name: str) -> None:
        with self.lock:
            if self.pairing.get("status") == "waiting":
                return
            self.cancel_pairing.clear()
            self.pairing = {"status": "waiting", "code": None, "expires_at": None, "message": ""}
        capacity = read_settings()["capacity"]

        def on_code(code: str, expires_in: int) -> None:
            with self.lock:
                self.pairing.update(code=code, expires_at=time.time() + expires_in)

        def work() -> None:
            ok, message = pair_flow(server, name, capacity, on_code=on_code, cancelled=self.cancel_pairing.is_set)
            with self.lock:
                self.pairing = {"status": "done" if ok else "error", "message": message}
            if ok:
                self.start()  # a paired runner is ready to work at once

        threading.Thread(target=work, daemon=True).start()

    # ---- what the page shows
    def snapshot(self) -> dict:
        cfg = config.load()
        with self.lock:
            pairing = dict(self.pairing)
            if pairing.get("expires_at"):
                pairing["seconds_left"] = max(0, int(pairing["expires_at"] - time.time()))
            pairing.pop("expires_at", None)
            running = self.running()
            return {
                "version": VERSION,
                "os": OS_NAME,
                "python": platform.python_version(),
                "node": node_version(),
                "chrome": find_chrome(),
                "components": components_installed(),
                "preset_server": read_preset().get("server", ""),
                "computer_name": platform.node() or "Runner",
                "paired": bool(cfg),
                "server": (cfg or {}).get("server", ""),
                "name": (cfg or {}).get("name", ""),
                "settings": read_settings(),
                "pairing": pairing,
                "running": running,
                "online": running and self.online,
                "revoked": self.revoked,
                "log": list(self.log),
            }


CONTROLLER = Controller()


# ---------------------------------------------------------------- HTTP
def make_handler(token: str, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "TestMaxRunnerSetup"

        def log_message(self, *args) -> None:  # keep the console clean
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _allowed(self) -> bool:
            if self.headers.get("Host", "") not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {f"http://{h}" for h in allowed_hosts}

        def _read_raw(self) -> bytes | None:
            """The request body, always consumed (an unread body makes the connection reset instead of delivering our
            answer). None when it is too large or the length is not a number."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self.close_connection = True
                return None
            if length < 0:
                self.close_connection = True
                return None
            data = self.rfile.read(min(length, 1_000_000)) if length else b""  # never hold more than 1 MB
            if length > MAX_BODY:
                self.close_connection = True
                return None
            return data

        @staticmethod
        def _parse(raw: bytes | None) -> dict | None:
            if raw is None:
                return None
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                return None
            return data if isinstance(data, dict) else None

        def do_GET(self) -> None:  # noqa: N802
            if not self._allowed():
                return self._json(403, {"error": "Not allowed."})
            path = urlparse(self.path).path
            if path == "/":
                page = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", token)
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if path == "/api/state":
                if self.headers.get("X-Setup-Token") != token:
                    return self._json(403, {"error": "Not allowed."})
                return self._json(200, CONTROLLER.snapshot())
            self._json(404, {"error": "Not found."})

        def do_POST(self) -> None:  # noqa: N802
            raw = self._read_raw()  # first, so every answer below reaches the client
            if not self._allowed() or self.headers.get("X-Setup-Token") != token:
                return self._json(403, {"error": "Not allowed."})
            body = self._parse(raw)
            if body is None:
                return self._json(400, {"error": "Bad request."})
            path = urlparse(self.path).path
            if path == "/api/pair":
                server = valid_server(str(body.get("server", "")))
                if not server:
                    return self._json(422, {"error": "Enter the TestMax address, for example http://192.168.1.20:8000"})
                CONTROLLER.begin_pairing(server, str(body.get("name", ""))[:80])
            elif path == "/api/pair/cancel":
                CONTROLLER.cancel_pairing.set()
            elif path == "/api/settings":
                try:
                    capacity = int(body.get("capacity", 2))
                except (TypeError, ValueError):
                    return self._json(422, {"error": "Slots must be a number from 1 to 8."})
                if not 1 <= capacity <= 8:
                    return self._json(422, {"error": "Slots must be a number from 1 to 8."})
                write_settings({"headed": bool(body.get("headed")), "capacity": capacity})
                if CONTROLLER.running():
                    CONTROLLER.add_log("Settings saved. They apply the next time the runner starts.")
            elif path == "/api/start":
                problem = CONTROLLER.start()
                if problem:
                    return self._json(409, {"error": problem})
            elif path == "/api/stop":
                CONTROLLER.stop()
            elif path == "/api/unpair":
                CONTROLLER.stop()
                try:
                    config.CONFIG_PATH.unlink()
                except OSError:
                    pass
                CONTROLLER.pairing = {"status": "idle"}
                CONTROLLER.add_log("This computer was disconnected. Ask an administrator to revoke it on the Runners page.")
            else:
                return self._json(404, {"error": "Not found."})
            self._json(200, CONTROLLER.snapshot())

    return Handler


def run_setup(port: int = 8765, open_browser: bool = True) -> int:
    token = secrets.token_urlsafe(24)
    server = None
    for candidate in range(port, port + 11):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), make_handler(token, candidate))
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        print(f"Could not open a local port ({port} to {port + 10} are all busy). Close other TestMax runner windows and try again.")
        return 1
    url = f"http://127.0.0.1:{port}/"
    print(f"TestMax runner setup is open at {url}")
    print("Keep this window open while the runner works. Press Ctrl+C to stop everything.")
    if config.load():
        CONTROLLER.start()  # already paired on an earlier visit: just get to work
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        CONTROLLER.cancel_pairing.set()
        CONTROLLER.stop()
        server.server_close()
    return 0
