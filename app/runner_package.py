"""The downloadable runner: a zip the server builds on demand (Runners page > Connect a Runner).

What is in it is an explicit allow-list, never a folder walk: the runner's own files, the one engine module it shares
with the server, the start scripts and the server address. Never `runner.json` (it holds a runner key), never logs,
never anything from `.env`. The server address is the only thing that differs per download (it goes into a JSON file,
never into a script).
"""
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
RUNNER_DIR = ROOT / "runner"
TOP = "testmax-runner"

SYSTEMS = {"windows": "start-runner.bat", "macos": "start-runner.command", "linux": "start-runner.sh"}

# (source file, name inside the zip). Python source and the setup page first, then the tooling manifests.
FILES = [
    (RUNNER_DIR / "__init__.py", "runner/__init__.py"),
    (RUNNER_DIR / "__main__.py", "runner/__main__.py"),
    (RUNNER_DIR / "api.py", "runner/api.py"),
    (RUNNER_DIR / "config.py", "runner/config.py"),
    (RUNNER_DIR / "main.py", "runner/main.py"),
    (RUNNER_DIR / "mcp_proc.py", "runner/mcp_proc.py"),
    (RUNNER_DIR / "scan_loop.py", "runner/scan_loop.py"),
    (RUNNER_DIR / "test_run.py", "runner/test_run.py"),
    (RUNNER_DIR / "setup_ui.py", "runner/setup_ui.py"),
    (RUNNER_DIR / "setup_page.html", "runner/setup_page.html"),
    (RUNNER_DIR / "package.json", "runner/package.json"),
    (RUNNER_DIR / "package-lock.json", "runner/package-lock.json"),
    (RUNNER_DIR / "test-runtime" / "package.json", "runner/test-runtime/package.json"),
    (RUNNER_DIR / "test-runtime" / "package-lock.json", "runner/test-runtime/package-lock.json"),
    (APP_DIR / "__init__.py", "app/__init__.py"),
    (APP_DIR / "engines" / "__init__.py", "app/engines/__init__.py"),
    (APP_DIR / "engines" / "scan_core.py", "app/engines/scan_core.py"),
    (RUNNER_DIR / "package" / "requirements-runner.txt", "requirements-runner.txt"),
    (RUNNER_DIR / "package" / "README.txt", "README.txt"),
]
SCRIPTS = {name: RUNNER_DIR / "package" / name for name in SYSTEMS.values()}


class PackageError(Exception):
    pass


def clean_server(value: str) -> str:
    """The server address as an http(s) origin; PackageError when it is not one."""
    value = (value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment or len(value) > 200:
        raise PackageError("Enter the address as http://host:port (for example http://192.168.1.20:8000).")
    if any(c in value for c in " \t\r\n\"'<>`\\"):
        raise PackageError("The address contains characters that are not allowed.")
    return value


def _runner_version() -> str:
    for line in (RUNNER_DIR / "main.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("VERSION = "):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def build(system: str, server: str) -> bytes:
    if system not in SYSTEMS:
        raise PackageError("Choose Windows, macOS or Linux.")
    server = clean_server(server)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def add(name: str, data: bytes, mode: int = 0o644) -> None:
            info = zipfile.ZipInfo(f"{TOP}/{name}", date_time=datetime.now(timezone.utc).timetuple()[:6])
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)

        for source, name in FILES:
            add(name, source.read_bytes())
        script = SYSTEMS[system]
        text = SCRIPTS[script].read_text(encoding="utf-8").replace("\r\n", "\n")
        if script.endswith(".bat"):
            text = text.replace("\n", "\r\n")  # cmd.exe wants Windows line endings
        add(script, text.encode("utf-8"), 0o755)
        add("runner-preset.json", json.dumps({"server": server, "runner_version": _runner_version()}, indent=2).encode())
    return out.getvalue()
