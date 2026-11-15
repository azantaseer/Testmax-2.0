# TestMax Pilot 2

Run (needs Docker Desktop):

```bash
cp .env.example .env      # then set POSTGRES_PASSWORD and, for AI, OPENAI_API_KEY / ANTHROPIC_API_KEY
docker compose up -d --build
```

Open http://127.0.0.1:8000. Stop with `docker compose down` (data kept) or `docker compose down -v` (data wiped).

Runner (the machine that opens the browser; it needs Google Chrome, Python 3.11+ and Node 20+):

1. In TestMax open **Runners > Connect a Runner**, type the address the runner will use (another computer needs this
   computer's network address, for example `http://192.168.1.20:8000`, and the server must accept connections from your
   network: set `API_BIND=0.0.0.0` in `.env`), choose the operating system and **Download runner**.
2. Unzip it and double-click `start-runner.bat` (macOS: `start-runner.command`, Linux: `./start-runner.sh`). The first
   start installs what it needs; then a local setup page opens in the browser. It shows a pairing code, which you enter
   in TestMax; after that it starts by itself (Headed switch, slots, Start/Stop and the log are on that page).

Command line (development, same machine as the project folder):

```bash
cd runner && npm install && cd test-runtime && npm install
python -m runner pair --server http://127.0.0.1:8000
python -m runner run [--headed]
python -m runner setup            # the same setup page the downloaded package opens
```

Tests (needs the db container running: `docker compose up -d db`):

```bash
.venv/Scripts/python.exe -m pytest -q
```

Never commit `.env`. Scope and plan: `SCOPE.md`, `STEP2_SCOPE.md`.
