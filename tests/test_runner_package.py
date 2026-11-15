"""The downloadable runner package and its local setup page."""
import io
import json
import socket
import subprocess
import sys
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer

import httpx
import pytest

from app import runner_package
from runner import config, main as runner_main, setup_ui
from tests.conftest import GOOD_PASSWORD, login, new_client, signup_org

GOOD_SERVER = "http://192.168.1.20:8000"


def admin_client():
    c = new_client()
    assert signup_org(c).status_code == 201
    return c


def package(c, system="windows", server=GOOD_SERVER):
    return c.get("/api/runners/package", params={"system": system, "server": server})


def names(response):
    return zipfile.ZipFile(io.BytesIO(response.content)).namelist()


# ---- the download ---------------------------------------------------------------------
def test_the_package_has_what_the_runner_needs_and_the_server_address():
    c = admin_client()
    r = package(c)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="testmax-runner-windows.zip"' in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    got = set(z.namelist())
    top = runner_package.TOP
    for needed in ("runner/main.py", "runner/setup_ui.py", "runner/setup_page.html", "runner/package.json", "runner/test-runtime/package.json", "app/engines/scan_core.py",
                   "requirements-runner.txt", "README.txt", "start-runner.bat", "runner-preset.json"):
        assert f"{top}/{needed}" in got, needed
    assert not any(n.endswith((".sh", ".command")) for n in got)  # only the script of the chosen system
    preset = json.loads(z.read(f"{top}/runner-preset.json"))
    assert preset["server"] == GOOD_SERVER and preset["runner_version"] == runner_main.VERSION
    bat = z.read(f"{top}/start-runner.bat")
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")  # Windows line endings only
    assert b"-m runner setup" in bat and GOOD_SERVER.encode() not in bat  # the address is data, never part of a script


def test_other_systems_get_their_own_start_file_with_unix_line_endings_and_run_permission():
    c = admin_client()
    for system, script in (("macos", "start-runner.command"), ("linux", "start-runner.sh")):
        z = zipfile.ZipFile(io.BytesIO(package(c, system).content))
        assert f"{runner_package.TOP}/{script}" in z.namelist() and f"{runner_package.TOP}/start-runner.bat" not in z.namelist()
        info = z.getinfo(f"{runner_package.TOP}/{script}")
        assert (info.external_attr >> 16) & 0o111  # executable
        assert b"\r" not in z.read(info.filename) and z.read(info.filename).startswith(b"#!/usr/bin/env bash")


def test_the_package_never_contains_a_runner_key_or_local_state(tmp_path):
    # the development folder really has these files; none may travel
    r = package(admin_client())
    allowed = {f"{runner_package.TOP}/{n}" for _, n in runner_package.FILES} | {f"{runner_package.TOP}/start-runner.bat", f"{runner_package.TOP}/runner-preset.json"}
    assert set(names(r)) == allowed
    z = zipfile.ZipFile(io.BytesIO(r.content))
    blob = b"".join(z.read(n) for n in z.namelist())
    for n in z.namelist():
        assert "runner.json" not in n and not n.endswith((".log", ".err")) and "node_modules" not in n and ".env" not in n
    local = config.load()
    if local:  # this machine's own runner key must not be anywhere in the download
        assert local["runner_key"].encode() not in blob


def test_the_package_is_self_contained_and_importable(tmp_path):
    z = zipfile.ZipFile(io.BytesIO(package(admin_client()).content))
    z.extractall(tmp_path)
    root = tmp_path / runner_package.TOP
    out = subprocess.run(
        [sys.executable, "-c", "import runner.main, runner.setup_ui, app.engines.scan_core as s, pathlib; print(pathlib.Path(runner.main.__file__).parent.parent)"],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(root)  # imported from the unzipped copy, not from the development folder
    help_out = subprocess.run([sys.executable, "-m", "runner", "setup", "--help"], cwd=root, capture_output=True, text=True, timeout=60)
    assert help_out.returncode == 0 and "--no-browser" in help_out.stdout


def test_the_download_is_for_admins_and_checks_its_input():
    c = admin_client()
    for bad in ("javascript:alert(1)", "ftp://x", "http://user:pw@host", "http://host/path", "http://host:8000/?x=1", "http://ho st", 'http://host"x', "not a url", "http://" + "a" * 250):
        assert package(c, server=bad).status_code == 422, bad
    assert package(c, system="solaris").status_code == 422
    assert new_client().get("/api/runners/package").status_code == 401
    assert c.post("/api/users", json={"name": "Una", "email": "una@acme.example", "password": GOOD_PASSWORD}).status_code == 201
    una = new_client()
    login(una, "una@acme.example")
    assert package(una).status_code == 403  # admins only, like approving a runner


def test_without_an_address_the_servers_own_address_is_used():
    c = admin_client()
    z = zipfile.ZipFile(io.BytesIO(c.get("/api/runners/package", params={"system": "windows"}).content))
    preset = json.loads(z.read(f"{runner_package.TOP}/runner-preset.json"))
    assert preset["server"].startswith("http://testserver")


# ---- the local setup page ----------------------------------------------------------------
@pytest.fixture
def setup_page(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "runner.json")
    monkeypatch.setattr(setup_ui, "PRESET_FILE", tmp_path / "runner-preset.json")
    (tmp_path / "runner-preset.json").write_text(json.dumps({"server": GOOD_SERVER}))
    controller = setup_ui.Controller()
    monkeypatch.setattr(setup_ui, "CONTROLLER", controller)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), setup_ui.make_handler("test-token", port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, headers={"X-Setup-Token": "test-token"})
    yield client, controller, port, tmp_path
    controller.stop()
    httpd.shutdown()
    httpd.server_close()


def test_the_setup_page_is_served_with_its_token_and_strict_headers(setup_page):
    client, controller, port, tmp = setup_page
    r = client.get("/")
    assert r.status_code == 200 and "test-token" in r.text and "__TOKEN__" not in r.text
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'none'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"
    state = client.get("/api/state").json()
    assert state["paired"] is False and state["preset_server"] == GOOD_SERVER and state["running"] is False
    assert {"python", "node", "chrome", "components", "settings", "pairing", "log"} <= set(state)
    assert "runner_key" not in json.dumps(state)


def test_another_website_cannot_drive_the_setup_page(setup_page):
    client, controller, port, tmp = setup_page
    # DNS rebinding: a foreign host name pointing at 127.0.0.1
    assert httpx.get(f"http://127.0.0.1:{port}/", headers={"Host": "evil.example"}).status_code == 403
    assert httpx.get(f"http://127.0.0.1:{port}/api/state", headers={"Host": "evil.example", "X-Setup-Token": "test-token"}).status_code == 403
    # no token / wrong token
    assert httpx.get(f"http://127.0.0.1:{port}/api/state").status_code == 403
    assert httpx.post(f"http://127.0.0.1:{port}/api/start", json={}, headers={"X-Setup-Token": "nope"}).status_code == 403
    # a page on another origin (the browser always sends Origin on a cross-site POST)
    assert client.post("/api/stop", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/stop", json={}, headers={"Origin": f"http://127.0.0.1:{port}"}).status_code == 200
    # bodies are small and must be JSON objects
    assert client.post("/api/settings", content=b"x" * 20000, headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/settings", content=b"[1,2]", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/settings", content=b"{bad", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.get("/etc/passwd").status_code == 404 and client.post("/api/nothing", json={}).status_code == 404


def test_settings_and_address_are_validated_and_saved(setup_page):
    client, controller, port, tmp = setup_page
    for bad in ({"capacity": 0}, {"capacity": 9}, {"capacity": "many"}, {"capacity": None}):
        assert client.post("/api/settings", json={"headed": True, **bad}).status_code == 422, bad
    ok = client.post("/api/settings", json={"headed": True, "capacity": 3})
    assert ok.status_code == 200 and ok.json()["settings"] == {"headed": True, "capacity": 3}
    assert client.get("/api/state").json()["settings"] == {"headed": True, "capacity": 3}  # read back from the file
    for bad in ("", "javascript:alert(1)", "http://user:pw@h", "ftp://h", "http://h/path", "x" * 300):
        assert client.post("/api/pair", json={"server": bad}).status_code == 422, bad
    assert client.post("/api/start", json={}).status_code == 409  # not paired yet


def test_pairing_shows_the_code_and_the_runner_starts_once_approved(setup_page, monkeypatch):
    client, controller, port, tmp = setup_page
    release = threading.Event()
    seen = {}

    def fake_pair(server, name, capacity, on_code=None, cancelled=lambda: False):
        seen.update(server=server, name=name, capacity=capacity)
        on_code("ABCD-EFGH", 600)
        while not release.is_set():
            if cancelled():
                return False, "Pairing was cancelled."
            time.sleep(0.02)
        config.save({"server": server, "runner_key": "tmr_secret", "runner_id": "x", "name": name, "capacity": capacity})
        return True, "Paired."

    started = []
    monkeypatch.setattr(setup_ui, "pair_flow", fake_pair)
    monkeypatch.setattr(controller, "start", lambda: started.append(1))
    assert client.post("/api/pair", json={"server": GOOD_SERVER + "/", "name": "Office PC"}).status_code == 200
    for _ in range(100):
        pairing = client.get("/api/state").json()["pairing"]
        if pairing.get("code"):
            break
        time.sleep(0.02)
    assert pairing["status"] == "waiting" and pairing["code"] == "ABCD-EFGH" and 590 <= pairing["seconds_left"] <= 600
    assert seen == {"server": GOOD_SERVER, "name": "Office PC", "capacity": 2}
    release.set()
    for _ in range(100):
        state = client.get("/api/state").json()
        if state["paired"]:
            break
        time.sleep(0.02)
    assert state["paired"] and state["server"] == GOOD_SERVER and state["name"] == "Office PC" and state["pairing"]["status"] == "done"
    assert "tmr_secret" not in json.dumps(state) and started == [1]  # paired: the runner is started by itself
    # disconnect forgets the key
    client.post("/api/unpair", json={})
    assert config.load() is None and client.get("/api/state").json()["paired"] is False


def test_pairing_can_be_cancelled(setup_page, monkeypatch):
    client, controller, port, tmp = setup_page

    def fake_pair(server, name, capacity, on_code=None, cancelled=lambda: False):
        on_code("WXYZ-2345", 600)
        while not cancelled():
            time.sleep(0.02)
        return False, "Pairing was cancelled."

    monkeypatch.setattr(setup_ui, "pair_flow", fake_pair)
    client.post("/api/pair", json={"server": GOOD_SERVER})
    time.sleep(0.2)
    client.post("/api/pair/cancel", json={})
    for _ in range(100):
        pairing = client.get("/api/state").json()["pairing"]
        if pairing["status"] != "waiting":
            break
        time.sleep(0.02)
    assert pairing == {"status": "error", "message": "Pairing was cancelled."}


def test_the_runner_is_started_stopped_and_its_log_shown(setup_page, monkeypatch):
    client, controller, port, tmp = setup_page
    config.save({"server": GOOD_SERVER, "runner_key": "tmr_x", "runner_id": "1", "name": "PC", "capacity": 2})
    real_popen = subprocess.Popen
    launched = {}

    def fake_popen(cmd, **kwargs):  # a stand-in for `python -m runner run` that behaves like it
        if "runner" not in cmd:  # anything else (the page also asks `node --version`) is real
            return real_popen(cmd, **kwargs)
        launched["cmd"] = cmd
        script = "import time,sys\nprint(\"12:00:00 runner 'PC' online (http://x), 2 slot(s)\", flush=True)\ntime.sleep(60)"
        return real_popen([sys.executable, "-u", "-c", script], **{k: v for k, v in kwargs.items() if k != "cwd"})

    monkeypatch.setattr(setup_ui.subprocess, "Popen", fake_popen)
    client.post("/api/settings", json={"headed": True, "capacity": 4})
    r = client.post("/api/start", json={})
    assert r.status_code == 200
    assert launched["cmd"][1:5] == ["-u", "-m", "runner", "run"] and launched["cmd"][5:] == ["--capacity", "4", "--headed"]
    for _ in range(150):
        state = client.get("/api/state").json()
        if state["online"]:
            break
        time.sleep(0.05)
    assert state["running"] and state["online"] and any("online" in line for line in state["log"])
    assert client.post("/api/start", json={}).status_code == 200  # a second click does not start a second runner
    stopped = client.post("/api/stop", json={}).json()
    assert stopped["running"] is False and stopped["online"] is False
    assert not controller.running()


# ---- pairing flow shared with the command line -------------------------------------------
def test_pair_flow_pairs_with_the_server_and_saves_the_key(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "runner.json")
    admin = admin_client()
    runner_http = new_client()

    class Shim:  # the real ServerClient talks HTTP; this one talks to the in-process app
        def __init__(self, server, key=None):
            pass

        def pair_start(self, name, capacity, os_name, version):
            return runner_http.post("/api/runner/pair/start", json={"name": name, "capacity": capacity, "os": os_name, "version": version}).json()

        def pair_poll(self, device_code):
            return runner_http.post("/api/runner/pair/poll", json={"device_code": device_code}).json()

    monkeypatch.setattr(runner_main, "ServerClient", Shim)
    shown = {}

    def on_code(code, expires_in):
        shown["code"] = code
        assert admin.post("/api/runners/pair", json={"user_code": code, "name": "Approved PC"}).status_code == 201

    monkeypatch.setattr(runner_main.time, "sleep", lambda s: None)
    ok, message = runner_main.pair_flow(GOOD_SERVER + "/", "Office PC", 3, on_code=on_code)
    assert ok and message == "Paired." and len(shown["code"]) == 9
    saved = config.load()
    assert saved["server"] == GOOD_SERVER and saved["runner_key"].startswith("tmr_") and saved["name"] == "Office PC" and saved["capacity"] == 3
    assert [r["name"] for r in admin.get("/api/runners").json()["runners"]] == ["Approved PC"]


def test_pair_flow_reports_problems_in_plain_words(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "runner.json")
    ok, message = runner_main.pair_flow("http://127.0.0.1:9", "PC", 2)  # nothing listens there
    assert not ok and message.startswith("Could not start pairing: Could not reach the server")
    assert config.load() is None
