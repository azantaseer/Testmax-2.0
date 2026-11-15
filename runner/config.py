"""Local runner settings (runner.json, next to this file; git-ignored). Holds the server URL and the runner key."""
import json
import os
from pathlib import Path

# TESTMAX_RUNNER_CONFIG lets several runners (several organizations) live on one machine, each with its own file.
CONFIG_PATH = Path(os.environ.get("TESTMAX_RUNNER_CONFIG") or Path(__file__).resolve().parent / "runner.json")


def load() -> dict | None:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
