# TestMax Pilot 2: Technical Guide

Written 17 Nov 2026 from the code as committed (git tag `pilot2-test-batches`, plus one later UI commit). This is the single document that explains how Pilot 2 is built, how it runs, and how to set it up again on another server. Things that were **not** built or **not** verified are marked as such. Other documents in the repo: `CLAUDE.md` (permanent rules), `HANDOFF.md` (why Pilot 2 exists), `SCOPE.md` and the `*_SCOPE.md` files (what each step was approved to be), `PROGRESS.md` (what was verified, step by step).

---

## 1. What Pilot 2 is

TestMax is a test-automation product. A person gives it a requirements document; it produces requirements, scans the real web application, writes manual test cases and Playwright scripts, runs the scripts in a real Chrome browser and reports the result.

**Pilot 1** (`D:\Projects\TestMax-Independent-Pilot`, never modified) proved that the AI quality is good. **Pilot 2** proves what the current production system cannot do, so that the production backend can be rebuilt:

| Proof | How Pilot 2 shows it |
|---|---|
| Quality holds inside a real product | The Pilot 1 engines and the four system prompts are reused unchanged; results are stored in Postgres |
| Several companies, separate data | Every row has a `tenant_id`; every query goes through `scoped_select` |
| Concurrency | One Postgres job queue, a worker pool, runners with slots, a per-organization limit |
| Reliability | Jobs hold a lease; a job whose worker dies is requeued or failed with a clear message |
| Simplicity | One command (`docker compose up -d --build`), three containers, no Redis, queue server or Kubernetes |

Pipeline: **Requirements → App scan → Test cases → Test scripts → Execute → Report**. Later additions: data-driven testing (data variations), requirement source and tags, and test batches.

---

## 2. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Server language | **Python 3.14** | Same version as Pilot 1 |
| Web framework | **FastAPI 0.162** on **uvicorn 0.54** | REST/JSON API, plus serving the static UI |
| Database | **PostgreSQL 16** | Structured data, the job queue, uploaded documents (as bytes), screenshots (as bytes) |
| ORM and migrations | **SQLAlchemy 2.1** (psycopg 3) and **Alembic 1.20** | 14 migrations, `migrations/versions/0001` to `0014` |
| Job queue | A `jobs` table in Postgres, claimed with `SELECT ... FOR UPDATE SKIP LOCKED` | No separate queue service |
| Background workers | Python process `python -m app.worker` (thread pool) | Runs the AI jobs |
| Browser work | **Runner**: a Python program on the customer's machine, which starts **Node.js** tools | Playwright MCP 0.0.83 for the scan, Playwright Test 1.64.1 for execution, real **Google Chrome** |
| AI providers | **OpenAI** (`openai` 3.41) and **Anthropic** (`anthropic` 2.8) | One adapter class each, in `app/providers.py` |
| Passwords | **argon2-cffi** | Sessions are random tokens, only a SHA-256 hash is stored |
| Document reading | `pypdf` (PDF), `python-docx` (DOCX) | TXT, MD, CSV, TSV are read as plain text |
| Word export | `python-docx` | Test cases as a `.docx` (`app/engines/docx_builder.py`) |
| Front end | **Plain HTML, CSS and JavaScript ES modules** | No framework, no build step, no npm for the UI, no CDN |
| Fonts | Inter, Manrope, Sora, a monospace face | Bundled in `app/static/fonts` |
| Tests | **pytest 9** | 386 tests |
| Containers | **Docker Compose** | `db`, `api`, `worker` |

Deliberately **not** used: Redis, RabbitMQ, Kubernetes, microservices, GraphQL, an SPA framework, a CSS framework, WebSockets. The live status in the UI is plain polling of `GET /api/jobs/{id}` (once a second). WebSocket was in the first proposal but was never built.

`CLAUDE.md` rule 6: no new dependency, service or tool without approval.

---

## 3. Repository map

```
TestMax-Pilot2/
├── CLAUDE.md                 Permanent rules for working on this project
├── HANDOFF.md                Why Pilot 2 exists, success tests, prod facts
├── SCOPE.md, STEP2/6/7_SCOPE.md, DATA_DRIVEN_SCOPE.md, REQUIREMENT_GROUPING_SCOPE.md,
│   UI_REDESIGN_SCOPE.md, TEST_BATCHES_SCOPE.md     Approved scopes, one per piece of work
├── PROGRESS.md               What was built and verified, honestly, step by step
├── README.md                 Short run instructions
├── TECHNICAL_GUIDE.md        This file
├── Dockerfile, docker-compose.yml, .dockerignore, .env.example
├── requirements.txt          Pinned Python packages
├── alembic.ini, pytest.ini, .gitattributes (keeps bytes as written), .gitignore
├── system-prompts/           The four original AI prompts (never edited)
├── migrations/versions/      Alembic migrations 0001 to 0014
├── app/                      The server (FastAPI) and the engines
│   ├── main.py               App assembly, security headers, static files, SPA fallback
│   ├── config.py             Settings from environment variables
│   ├── db.py, deps.py, errors.py, security.py, validation.py
│   ├── models.py, models_work.py, models_runner.py, models_tests.py   Tables
│   ├── routers/              HTTP endpoints (one file per area)
│   ├── engines/              The ported Pilot 1 logic and the new rules
│   ├── jobs.py, worker.py, handlers.py, handlers_tests.py            Queue, worker, AI job handlers
│   ├── runner_jobs.py, runner_auth.py, runner_target.py, runner_package.py   Runner side of the server
│   ├── run_service.py, scan_service.py, batch_service.py              Saving what a runner reports
│   ├── providers.py, llm.py, llm_types.py, ai_providers.py, ai_settings.py, ai_api.py   Multi-model AI
│   ├── deletion.py, editing.py                                        Delete rules, hand-editing rules
│   └── static/               The whole UI (index.html, css/, js/, fonts/, assets/)
├── runner/                   The program that runs on a customer machine
├── tests/                    pytest suite
└── reference/                Screenshots and design notes used while building the UI
```

### 3.1 Server files (`app/`)

| File | Lines | What it does |
|---|---:|---|
| `main.py` | 79 | Builds the FastAPI app, includes all routers, adds the security middleware (CSRF origin check, `X-Content-Type-Options`, `X-Frame-Options: DENY`, CSP `default-src 'self'`, `Cache-Control: no-store` on `/api`), serves `/static`, answers `/health`, falls back to `index.html` for any non-API path |
| `config.py` | 56 | `Settings` dataclass read from environment variables (table in section 5) |
| `db.py`, `deps.py` | 26, 44 | Engine and session; `current_user` (cookie `tm_session`), `require_super_admin`, `scoped_select` (every tenant-owned query starts here) |
| `errors.py` | 37 | `AppError(status, code, message)` and handlers: every error is `{"error": {"code", "message", "fields"?}}` |
| `security.py` | 58 | argon2 hashing, a dummy hash so login timing does not reveal which emails exist, session token hashing, in-memory login throttle (10 failures in 5 minutes per email) |
| `models*.py` | | All tables (section 8) |
| `jobs.py` | 258 | The queue: `enqueue`, `claim` (SKIP LOCKED, per-organization cap, fairness), `heartbeat`, `succeed`, `fail`, `retry_or_fail`, `release`, `reap_expired`, `request_cancel` |
| `worker.py` | 145 | The server-side worker loop (section 6.2) |
| `handlers.py`, `handlers_tests.py` | 91, 233 | What each AI job type does: generate requirements, generate test cases, generate a test script |
| `runner_jobs.py` | 39 | Which job types go to runners (`app_scan`, `runner_stub`, `run_script`), the claim payload, the `COMPLETERS` table |
| `runner_auth.py` | 45 | Runner key auth (`Authorization: Bearer tmr_...`), "online" = seen within 30 seconds |
| `runner_target.py` | 98 | Choosing a specific runner for a job and checking it can take it |
| `runner_package.py` | 89 | Builds the downloadable runner `.zip` from an allow-list of files |
| `run_service.py` | 136 | Saves the report of a script run (`TsRun`, steps, screenshots) |
| `scan_service.py` | 113 | Saves a scan; re-validates the App Map, scrubs secrets, builds the contracts for test case and script generation |
| `batch_service.py` | 193 | Saves a batch run: cuts the step list per script, creates one execution per script |
| `providers.py` | 318 | OpenAI and Anthropic adapters (client, list models, completion, tool calling, quirks) |
| `llm.py`, `llm_types.py` | 65, 29 | One entry point (`complete`, `converse`, `chat`) used by everything that calls a model |
| `ai_providers.py`, `ai_settings.py`, `ai_api.py` | 109, 45, 70 | Model listing and testing, effective model (user > organization > system), shared request models |
| `deletion.py` | 132 | Deleting requirements, test cases, scripts; refuses items with a running job |
| `editing.py` | 219 | Hand-editing requirements and test cases with version checks |

### 3.2 Routers (`app/routers/`, all under `/api`)

| File | Endpoints (summary) |
|---|---|
| `auth.py` | `POST /auth/login`, `/auth/logout`, `/auth/signup-organization`, `/auth/signup-join`; `GET /auth/me`; `GET /public/organizations` |
| `me.py` | The signed-in person's own settings: password, AI model |
| `tenant.py` | Organization AI model settings |
| `users.py` | Users of the organization: create, edit, role, status, reset password, delete |
| `projects.py` | Projects: list, create, edit, delete, members, `assignable-users` |
| `requirements.py` | Upload a document and generate requirements; list (search, filters, tags, source); read; edit; delete; tags; start a scan; start test case generation; `GET /jobs/{id}`, `/projects/{id}/jobs`, `POST /jobs/{id}/cancel` |
| `scans.py` | Scan results |
| `test_cases.py` | Test case sets, cases, `.docx` export, edit, delete, data variations (`PUT .../data-sets`) |
| `test_scripts.py` | Generate (jobs), list (filters), read, download, delete, "make data-driven" |
| `test_runs.py` | Execute a script, history, the execution report and its screenshots |
| `test_batches.py` | Batches: list, create, edit, delete, run; read a batch run |
| `runners.py` | Admin side: pair, list, status, limits, revoke, package download. Runner side: `pair/start`, `pair/poll`, `heartbeat`, `claim`, `jobs/{id}/progress`, `heartbeat`, `complete`, `fail`, and `POST /runner/llm` (the AI proxy) |

All paths of project data look like `/api/projects/{project_id}/...`. All write endpoints reject a cross-origin `Origin` header.

### 3.3 Engines (`app/engines/`)

| File | Lines | Origin | What it does |
|---|---:|---|---|
| `requirements.py` | 106 | Pilot 1, unchanged logic | Extracts text from PDF/DOCX/TXT/MD/CSV/TSV, sends the unchanged prompt plus the text, parses and validates the JSON reply |
| `scan_core.py` | 603 | Pilot 1 `scan_engine.py` | The scan rules shared by server and runner: origin lock, scan policies (`READ_ONLY`, `INTERACTIVE`), App Map schema validation, secret scrubbing |
| `tcg.py` | 519 | Pilot 1 `tcg_engine.py` | Test case generation: constants, documented test data extraction, validation and normalisation of the model's JSON, coverage summary |
| `tsg.py` | 669 | Pilot 1 `tsg_engine.py` | Test script generation: builds locators from the scan (the AI never invents them), the fixed script header and helper functions, body validation, `node --check` syntax check |
| `docx_builder.py` | 319 | New | Test cases as a Word document |
| `datadriven.py` | 239 | New (Pilot 2) | Data keys, data variations, rewriting typed values into `DATA["key"]`, filling the data block at run time |
| `batch.py` | 167 | New (Pilot 2) | Joins several scripts into one Playwright test; marker steps; cutting the result per script |

Rule 5 of `CLAUDE.md`: reuse the proven Pilot 1 engines; do not rewrite what works. Each ported file says in its header which parts are "ported unchanged".

### 3.4 The runner (`runner/`)

| File | Lines | What it does |
|---|---:|---|
| `__main__.py`, `main.py` | 4, 269 | Command line (`pair`, `run`, `setup`), the job loop, handling of cancel and heartbeats, VERSION `0.3.0` |
| `api.py`, `config.py` | 59, 22 | HTTP client for the server; reads/writes `runner.json` (the runner key). Env `TESTMAX_RUNNER_CONFIG` moves that file |
| `scan_loop.py` | 249 | The scan: a model-driven loop that browses through Playwright MCP; every model call goes through the server's proxy |
| `mcp_proc.py` | 123 | Starts one Playwright MCP server (own browser, own port) per scan and always stops it; records child PIDs in `children.json` so orphans can be cleaned up |
| `test_run.py` | 245 | Executes one script file with Playwright Test and reports per-step results, log and screenshots |
| `setup_ui.py`, `setup_page.html` | 359, 190 | A small local web page (pair, start, stop, headed switch, slots, log) opened by the downloaded package |
| `package/` | | `start-runner.bat`, `.command`, `.sh`, `README.txt`, `requirements-runner.txt` for the download |
| `package.json` | | `@playwright/mcp` 0.0.83 |
| `test-runtime/package.json` | | `@playwright/test` 1.64.1 |

### 3.5 Front end (`app/static/`)

No build step. `index.html` loads CSS files and one module, `js/main.js`.

- **`js/main.js`**: the router (matches `location.pathname`, mounts a view), link handling (`data-link`), unsaved-changes guard.
- **`js/api.js`, `store.js`, `dom.js`**: `fetch` wrapper with `ApiError`; global state (`state.me`); the `h(tag, props, ...children)` element builder (text is always inserted as text, never as HTML) and inline SVG icons.
- **`js/components.js`, `table.js`, `selection.js`**: modal, toast, confirm dialog, pagination, search box, Filter popover, skeleton rows, empty state, table frame, row selection with bulk delete.
- **`js/views/`**: one file per screen: `auth`, `shell` (top bar, breadcrumb, sidebar), `projects`, `projectFrame` (the tab bar), `requirements`, `tags`, `scan`, `testCases`, `dataSets`, `testScripts`, `testRuns`, `testBatches`, `runners`, `runnerPicker`, `users`, `aiSettings`, `mySettings`, `jobs`, `projectDetail`.
- **`css/`**: `tokens.css` (all colours, sizes, fonts; loaded first), `app.css`, `forms.css`, `table.css`, `step6.css`, `step7.css`, `batches.css`.

The look follows the UAT application (`reference/uat/AUDIT.md` has the measured design system): Inter 14/20, 41 px table rows, 36 px buttons, green `#5ECF80`, navy `#154260`, a 76 px top bar, a 256 px sidebar. The UI was checked in Chromium only.

---

## 4. Docker

### 4.1 The image (`Dockerfile`)

One image is used for both the `api` and the `worker` service.

1. `FROM node:22-slim AS node`: only to copy the `node` binary. **Node is in the server image only for `node --check`**, which syntax-checks generated scripts. The server never runs a browser.
2. `FROM python:3.14-slim`, copy `node`, `pip install -r requirements.txt`.
3. Copies `alembic.ini`, `migrations/`, `app/`, `runner/` (only so the server can offer the runner as a download; the runner never runs in this container) and `system-prompts/`.
4. Creates a non-root user `testmax` (uid 10001) and runs as it.
5. `EXPOSE 8000`, a health check that calls `/health`.
6. Default command: `alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000`. **Migrations run every time the `api` container starts.**

`.dockerignore` keeps out `.env`, `.git`, `tests`, `reference`, all `*.md`, runner state (`runner.json`, `children.json`, logs, `node_modules`), and `DEV_TEST_ACCOUNTS.md`. The code is **baked into the image**: after any code change run `docker compose up -d --build`.

### 4.2 The compose file (`docker-compose.yml`)

| Service | Image | What it is | Port |
|---|---|---|---|
| `db` | `postgres:16` | The database. Volume `pgdata` keeps the data. Health check `pg_isready` | `127.0.0.1:5432` (only this computer) |
| `api` | built from `Dockerfile` | The web server and API. Starts after `db` is healthy. Reads `.env` | `${API_BIND:-127.0.0.1}:8000:8000` |
| `worker` | same image | `python -m app.worker`. Starts after `api` is healthy. Health check disabled | none |

All three have `restart: unless-stopped`. `DATABASE_URL` is built from `POSTGRES_PASSWORD` for `api` and `worker`.

Useful commands:

```bash
docker compose up -d --build        # start (or rebuild after code changes)
docker compose ps                   # status
docker compose logs -f api worker   # logs
docker compose up -d --scale worker=3   # more AI throughput (the worker is designed for several copies)
docker compose down                 # stop, keep data
docker compose down -v              # stop and WIPE the database
```

### 4.3 What is not in Docker

- **The runner.** It must run on a machine with a real Chrome (and Node 20+, Python 3.11+). It is the customer's machine, not a TestMax container. This is the "RUNNER-ONLY" decision in `HANDOFF.md`: TestMax does not host a shared browser pool.
- **A reverse proxy / HTTPS.** Not set up (section 12).

### 4.4 Windows note

After a restart Windows can reserve port 8000 (Hyper-V/WinNAT), so Docker fails to publish it. It was fixed with `net stop winnat`, `docker compose up -d`, `net start winnat`.

---

## 5. Configuration (environment variables)

Set in `.env` (copied from `.env.example`; `.env` is git-ignored). Real environment variables win over `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | required | Database password (compose refuses to start without it) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | empty | Platform AI keys. Organizations choose only the provider and model, never enter a key. Empty disables a provider |
| `DEFAULT_AI_PROVIDER`, `DEFAULT_AI_MODEL` | empty | Optional system default. If empty, each organization must choose a model before AI work starts |
| `COOKIE_SECURE` | `false` | Set `true` when served over HTTPS (the sign-in cookie then gets the `Secure` flag) |
| `API_BIND` | `127.0.0.1` | Which address the API port is published on. `0.0.0.0` lets other computers connect |
| `SESSION_HOURS` / `REMEMBER_DAYS` | 12 / 30 | Sign-in lifetime |
| `PASSWORD_MIN_LENGTH` | 10 | |
| `JOB_TENANT_CONCURRENCY` | 3 | Default number of jobs one organization may run at once |
| `JOB_TENANT_CONCURRENCY_MAX` | 20 | The highest value an administrator may set |
| `JOB_LEASE_SECONDS` / `JOB_HEARTBEAT_SECONDS` | 45 / 10 | See section 6 |
| `JOB_MAX_ATTEMPTS` | 2 | How often a job may be tried (run jobs use 1) |
| `WORKER_CONCURRENCY` | 2 | Jobs one worker process runs at once |
| `ANTHROPIC_MAX_OUTPUT_TOKENS` | 32000 | Output ceiling for Anthropic |
| `LLM_TIMEOUT_SECONDS` | 900 | |
| `JOB_MAX_TOKENS` | 4,000,000 | Token budget of one job (the scan can make many model calls) |

Per-organization concurrency is stored on the organization (`tenants.job_concurrency`) and edited on the Runners page; empty means the default.

---

## 6. Jobs, workers and runners (the core concept)

### 6.1 What a job is

A **job** is one unit of background work. Every job is a row in the `jobs` table: `type`, `status` (`queued`, `running`, `succeeded`, `failed`), `payload` (JSON input), `result` (JSON output), `error`, `progress` (one line of text), `attempts`, `max_attempts`, `cancel_requested`, `lease_expires_at`, owner (`tenant_id`, `project_id`, `created_by`), who runs it (`worker_id` or `runner_id`), an optional `target_runner_id`, and metering (`provider`, `model`, `tokens_in`, `tokens_out`, `duration_ms`).

| Job type | Runs on | Handler |
|---|---|---|
| `generate_requirements` | Server worker | `handlers.generate_requirements` |
| `generate_test_cases` | Server worker | `handlers_tests.generate_test_cases` |
| `generate_test_script` | Server worker | `handlers_tests.generate_test_script` |
| `app_scan` | Runner | `scan_service.complete_app_scan` (saves the result) |
| `run_script` | Runner | `run_service.complete_run_script` (a batch is a `run_script` job with a `batch` block) |
| `runner_stub` | Runner | A test job that echoes |

AI work is split in two kinds: the **server worker** calls the model directly; the **runner** never holds an AI key and reaches the model through the server's proxy.

### 6.2 The queue

- **Claim:** one SQL statement (`app/jobs.py`) picks the oldest queued job of the organization with the fewest running jobs (fairness), skipping rows other claimers hold (`FOR UPDATE SKIP LOCKED`), only if the organization is under its limit, only of the types the claimer can handle, and only if the job is not tied to a different runner. An advisory lock serializes claims so the organization cap is exact.
- **Lease and heartbeat:** a running job holds a lease (45 s). Its worker or runner renews it every 10 s. A runner's heartbeat reply also carries `cancel_requested`.
- **Recovery (`reap_expired`):** a running job whose lease expired is put back in the queue if it has attempts left, otherwise it fails with: "The worker or runner running this job stopped responding and the job did not complete after N attempts. Please start it again." Script runs have `max_attempts = 1` because a run may already have changed data in the application under test, so they are never repeated on their own.
- **Clean stop:** on SIGTERM the worker gives running jobs 8 seconds, then hands unfinished jobs straight back to the queue without using up an attempt.
- **Retry:** a handler raising `Retryable` goes back to the queue while attempts remain; `JobFailed` ends the job at once with the message shown to the user.
- **Cancel:** a queued job is cancelled at once; a running job is told to stop on the next heartbeat and the runner kills its browser tree.
- **Server restart:** nothing is lost, because everything is in Postgres. Running jobs are recovered by lease expiry.

### 6.3 The server worker (`app/worker.py`)

A loop: reap expired jobs; if a thread is free, claim one job of a type in `HANDLERS`; run it in a thread pool; renew leases of in-flight jobs every 10 s. It is stateless, so several copies can run (`--scale worker=N`). A crash in a handler is logged on the server and the user sees only "An unexpected error occurred while running this job."

### 6.4 The runner

The **runner** is the customer's machine that opens browsers. It connects **outward** to TestMax over HTTP(S); no inbound port is needed.

**Pairing (device-code flow):**
1. `python -m runner pair --server <url>` (or the setup page) calls `POST /api/runner/pair/start` and gets a short user code, e.g. `RUWZ-AAJ3`, valid for 10 minutes.
2. An administrator enters the code on the Runners page (`POST /api/runners/pair`).
3. The runner polls `pair/poll`, receives its **runner key** (`tmr_...`) once and saves it in `runner.json`. The server stores only the SHA-256 hash of the key.
4. Revoking the runner in the UI makes the key invalid at once; its waiting jobs tied to it fail with a message.

**Working loop:** heartbeat (also sends version, OS and free slots) → `POST /runner/claim` with `free_slots` → for each claimed job run it in a thread, send progress text (`/progress`), renew the lease (`/heartbeat`), and finally `/complete` with the result or `/fail`. A runner is "online" if it was seen within 30 seconds. **Slots** (capacity, default 2 to 3) are how many jobs that machine takes at once.

**Choosing a runner:** the Execute and Scan dialogs show "Run on" when the organization has two or more runners; a job can be tied to one runner (`jobs.target_runner_id`).

**Two jobs on the runner:**
- **`app_scan`:** starts Playwright MCP (own Chrome and port), runs the model-driven scan loop (`scan_loop.py`) with the unchanged scan prompt, with an origin lock and a scan policy; every model call goes to `POST /api/runner/llm`, which checks that the job is running on this runner, picks the system prompt itself, enforces a token budget (4,000,000) and a body size limit (6 MB), and answers with the model's text or tool calls. The result is an **App Map** (pages, controls, locators, evidence). The server re-validates it and scrubs secrets.
- **`run_script`:** writes the script to a temporary folder with a `playwright.config.js` (installed Chrome, 180 s test timeout, 15 s expect timeout, one worker, screenshot on), runs `node @playwright/test/cli.js test ...`, reads the live output for progress, then Playwright's JSON report for per-step results (`Step N ...` titles) and screenshots. The test process never sees API keys (`API_KEY`, `OPENAI`, `ANTHROPIC`, `SECRET`, `TOKEN` variables are removed). A run is stopped after 10 minutes (env `TESTMAX_RUN_MAX_SECONDS`). A script that skips itself is reported as skipped, never as a pass.

**Downloadable package:** `GET /api/runners/package` builds a zip from an explicit allow-list of files (never `runner.json`, logs or anything from `.env`) with the server address in a JSON file. It contains start scripts for Windows (`.bat`), macOS (`.command`) and Linux (`.sh`); the first start installs what it needs and opens the local setup page.

Runner versions: `0.3.0` now. Servers refuse to start script jobs on runners older than 0.2.

### 6.5 Concurrency limit

One number per organization (default 3, administrators can set 1 to 20 on the Runners page): the most jobs of that organization **running at the same time**, counting every job type together (AI jobs and browser jobs). A job also needs a free runner slot, so the real parallelism of browser jobs is the smaller of the limit and the runner slots. Lowering it never stops running jobs.

---

## 7. The AI side

### 7.1 System prompts

Four original prompts, in `system-prompts/`, copied unchanged from Pilot 1 and **never edited** (`CLAUDE.md` rule 4). Each engine reads its file as bytes, records the SHA-256 and passes the text as the system prompt. The tests pin the hashes, so any change fails a test.

| File | Used by | SHA-256 pinned in tests |
|---|---|---|
| `TestMax_Requirements_Extraction_System_Prompt_v1.2.txt` | Requirement extraction (`engines/requirements.py`) | `400b0fcc549326c4...481e30` |
| `app-scan-v1.5.txt` | The app scan (`scan_core.py`, via the proxy) | `0ebceb1c8941eb7c...68f968` |
| `test-case-generation-v2.0.txt` | Test case generation (`tcg.py`) | `b876f5cabaf6535d...a74ae7` |
| `test-script-generation-v1.0.txt` | Test script generation (`tsg.py`) | `5b3f31e2f586425f...cab99` |

(The full hashes are in `tests/test_requirements.py`, `test_scan.py`, `test_tcg.py`, `test_tsg.py`.) The Pilot 2 additions (data variations, batches) were built **around** the AI: after the AI writes a script, a fixed program rewrites it; the AI is never shown the new variables.

### 7.2 Multi-model

`app/providers.py` holds one adapter class per provider and a registry `REGISTRY = {openai, anthropic}`. The rest of the app never branches on a provider name: it calls `llm.complete / converse / chat`. An adapter provides: client creation, list models, plain completion, tool calling, error translation and model quirks. Adding a provider = one adapter + one registry line.

Model choice has three levels: **user setting > organization setting > system default**. There is no hidden default in the code: if nothing is chosen, AI work refuses to start with a clear message. The AI settings pages list models live from the provider, have a **Test** button, and check tool-calling support (needed by the scan). Platform keys live only in the server environment.

Switching to a newer model of the same provider usually works, but quality was only validated with the models used so far; a new model may reject a request parameter, and the scan needs tool calling.

### 7.3 Generation flow

1. **Requirements:** upload a PDF/DOCX/TXT/MD/CSV/TSV → text stored in `documents` → job `generate_requirements` → model → parsed JSON → `requirements` rows (summary, description, acceptance criteria, source document, tags).
2. **Scan:** from a requirement, give a URL and scan policy → job `app_scan` on a runner → App Map saved in `scans` with contracts for the next stages.
3. **Test cases:** job `generate_test_cases` takes the requirement and the scan → model → validated cases (type POSITIVE/NEGATIVE/VERIFICATION/BOUNDARY/EDGE, steps with action verbs, test data, expected result, `sourceElementId` pointing at a scanned control) → `test_case_sets` and `test_cases`; downloadable as `.docx`.
4. **Test scripts:** job `generate_test_script` per case → the engine builds the locators from the scan, the model writes only the body → validation (no template literals, no `if/else`, no immediate-value asserts, `expectText`/`expectValue` helpers only, up to 10 locators per element) → syntax check with `node --check` → saved in `test_scripts`. Steps that would stop at once (for example a placeholder password) make the script "needs test data" and it is not run.
5. **Execute:** job `run_script` on a runner → `test_runs` with steps, log and screenshots.

---

## 8. Data model

Every tenant-owned table has `tenant_id` with `ON DELETE CASCADE`.

| Table | Purpose |
|---|---|
| `tenants` | Organizations (unique case-insensitive name, AI choice, job limit) |
| `users` | Email (unique, lowercase), argon2 hash, role (`super_admin`, `site_admin`, `user`), status (`active`, `pending`, `disabled`), own AI choice |
| `sessions` | Hash of the session token, expiry |
| `projects`, `project_members` | Projects (unique name per organization, case-insensitive) and who is assigned |
| `jobs` | The queue (section 6) |
| `documents` | Uploaded files: bytes and extracted text |
| `requirements` | Summary, description, acceptance criteria, model, `version`, `edited`, `tags`, source document |
| `runners`, `pairing_requests` | Runners (hash of key, capacity, info, `last_seen_at`, `revoked_at`) and pairing codes |
| `scans` | Status `COMPLETE`/`PARTIAL`/`FAILED`, App Map, raw model output, history, contracts for test case and script generation |
| `test_case_sets` | One test case generation (model, prompt hash, tokens, coverage, warnings, what could not be generated) |
| `test_cases` | Title, type, overview, prerequisites, steps (JSON, each with `dataKey` when it has test data), expected result, `version`, `data_sets` (variations) |
| `test_scripts` | One per test case: code, `data_keys`, `data_gaps`, `case_version` (to flag "out of date"), `blocked_message`, model and token metering |
| `test_runs`, `test_run_artifacts` | One execution of a script: status, steps, log, the exact `script_code`, `data_set`, screenshots (bytes); `batch_run_id`, `batch_position` |
| `test_batches`, `test_batch_items`, `test_batch_runs` | Batches, their ordered scripts with a variation each, and the result of each run (a snapshot, readable after the batch changes) |

Numbering: human keys are `REQ-001`, `TC-001`, `TS-001`, `EX-001`, `TB-001` (batch), `BR-001` (batch run). Each is a counter per project; a project row lock (`SELECT ... FOR UPDATE`) keeps concurrent inserts from clashing.

Migrations (`migrations/versions`): 0001 initial (tenants, users, sessions), 0002 disabled status, 0003 projects, 0004 jobs/documents/requirements, 0005 runners and scans, 0006 provider registry, 0007 test cases, 0008 test scripts, 0009 test runs, 0010 editing (versions), 0011 runner choice and job limit, 0012 data-driven, 0013 requirement tags, 0014 test batches.

---

## 9. Security and multi-tenancy

- **Isolation:** every query on tenant data starts with `scoped_select(Model, user)`, which adds `tenant_id == user.tenant_id`. Another organization's id returns 404, never 403, so existence is not revealed. Tests cover cross-organization reads and writes for each area.
- **Sign-in:** argon2 password hash; session cookie `tm_session` (HttpOnly, SameSite=Lax; `Secure` when `COOKIE_SECURE=true`); random token, only its SHA-256 stored; failed-login throttle; constant-time check even for unknown emails.
- **CSRF:** SameSite cookie plus a check that the `Origin` of every write request matches the `Host`.
- **Roles:** Super Admin (everything, including organization AI model, job limit and Site Admins), Site Admin (manage users), User (work in assigned projects).
- **Runners:** a bearer key per runner, hashed in the database; one key = one organization; revocable. A runner can only touch jobs claimed by itself.
- **Secrets:** AI keys only in the server environment. The runner holds no AI key. The model proxy picks the system prompt. The test process never receives secret-looking environment variables. The App Map and generated data are scrubbed of known secrets from the requirement text. The run report masks secret-looking data keys (`password`, `token`, `pin`, `api key`...).
- **Content:** the UI inserts text as text (never HTML); CSP `default-src 'self'`; screenshots are served `nosniff`.
- **Known limits:** the server speaks plain HTTP; passwords inside test data are still stored in script text in the database, as before.

---

## 10. Features built beyond the basic pipeline

### 10.1 Data-driven testing (`engines/datadriven.py`, scope `DATA_DRIVEN_SCOPE.md`)
A step with real test data gets a stable key (`username`, `password`, `destination_url`...). **DV-1** is the test data written in the steps; more variations live on the test case (a name and a value per key; an empty value means "use DV-1"). After the AI writes a script, a fixed rule replaces each typed value inside its own step by `DATA["key"]` and adds a data block (`// TESTMAX-DATA-BEGIN ... END`). When a script is executed or downloaded the server writes the chosen variation's **current** values into the block, so editing a value never needs a new script or an AI call. The runner is unchanged. Editing only data that the script reads does not mark it "Out of date". Only typed-in values vary; steps and expected results are the same for every variation.

### 10.2 Test batches (`engines/batch.py`, `batch_service.py`, scope `TEST_BATCHES_SCOPE.md`)
A batch is 1 to 8 scripts that run in order in **one browser session**, each with its own data variation, with a per-batch setting **stop** or **continue** on failure. The server joins the scripts into one Playwright test: every script sits in its own function (own `ELEMENTS`, helpers, `DATA`), all share one `page`. It is sent as an ordinary `run_script` job with a `batch` block, so the runner is unchanged. Marker steps (`Step 0 TMX-START n key`, `TMX-DONE`, `TMX-FAIL`) split the runner's flat step list into one result per script (read from the console output if Playwright wrote no report, for example after a cancel). Results per script: passed, failed, aborted, not run. Each script that ran is also an ordinary execution with its variation, tagged "Part of BR-nnn". Deleting a script removes its place in batches; a running batch blocks deleting what it uses. Not built: prerequisite links, block-level test case generation, scheduling, parallel batches, passing values between scripts.

### 10.3 Requirements grouping (`REQUIREMENT_GROUPING_SCOPE.md`)
Source document column and filter, free-form tags (up to 10 per requirement, 30 characters), add/remove tags on selected rows, tag editor on the requirement page. Tags do not change the requirement's version, scans, test cases or scripts.

### 10.4 Editing with versions (`editing.py`)
Requirements and test cases can be edited by hand. Every edit carries the version the person saw; a stale edit is refused with 409. Steps keep their scan source control only if they already had it, so nobody can make a script click a control the scan never saw. Editing a test case flags its script "Out of date" (it is never regenerated silently, since that costs an AI call).

### 10.5 UI redesign (`UI_REDESIGN_SCOPE.md`)
Tokens, shell, list component (search, Filter popover, skeletons, empty states), detail pages, dialogs with sticky footers, unsaved-changes guard, keyboard focus ring, contrast checked by script, narrow-window sidebar, print styles.

---

## 11. Tests

`.venv/Scripts/python.exe -m pytest -q` (386 tests, about 4.5 minutes). They need the compose database running (`docker compose up -d db`); they create their own database `testmax_test` on it, drop and rebuild its schema with Alembic at the start, and truncate between tests, so the real `testmax` data is not touched. Fake provider keys are set before the app is imported, so no real key is used and no money is spent (the model is replaced by fakes).

| File | Covers |
|---|---|
| `test_auth`, `test_users`, `test_projects`, `test_tenant_ai` | Sign-up, sign-in, roles, isolation between organizations, AI settings |
| `test_registry`, `test_llm_proxy` | Provider adapters, model proxy rules |
| `test_requirements`, `test_requirement_groups`, `test_filters` | Upload and generation, source and tags, list filters |
| `test_jobs` | Queue, claim, fairness, limits, lease, recovery, release |
| `test_runners`, `test_runner_choice`, `test_runner_package` | Pairing, claim, choice of runner, the download |
| `test_scan` | Scan saving, validation, scrubbing |
| `test_tcg`, `test_tsg`, `test_data_driven`, `test_edit`, `test_delete` | Test cases, scripts, data variations, editing, deleting |
| `test_execute`, `test_batches` | Running scripts and batches, reports (some tests run the real Playwright Test with scripts that need no browser; the batch test runs the joined script in Node against a stand-in) |
| `test_static` | Static files are revalidated and linked files exist |

One known timing-sensitive test: `test_runner_package.py::test_pairing_shows_the_code_and_the_runner_starts_once_approved` can fail when the machine is busy.

---

## 12. Setting it up again on another server

### 12.1 Server (the part with the web app, API, workers and database)

Needs: Linux or Windows server, **Docker with Compose v2**, outbound internet (to OpenAI/Anthropic), about 2 GB of memory for the three containers (no browsers run here).

```bash
git clone https://github.com/ProdAdmin5/TestMax2.0.git
cd TestMax2.0
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD to a long random value,
#            set OPENAI_API_KEY and/or ANTHROPIC_API_KEY,
#            set API_BIND=0.0.0.0 if people connect from other computers
docker compose up -d --build
curl http://127.0.0.1:8000/health      # {"status":"ok"}
```

Then open `http://<server>:8000`, choose **Create your organization account** (the first user becomes the organization's Super Admin), and under Organization AI Model pick a provider and model.

**Network and firewall:** open TCP 8000 only to the networks that need it. Keep Postgres (`127.0.0.1:5432`) closed to the network; the compose file already binds it to the local machine only.

**HTTPS (recommended for anything beyond a trusted network; not set up in Pilot 2):** put a reverse proxy (Caddy, nginx or a cloud load balancer) in front of port 8000 with a certificate, keep `API_BIND=127.0.0.1` (so only the proxy reaches it), and set `COOKIE_SECURE=true`. Test that sign-in still works. This was not tried.

**Data and backup:** all data (documents, screenshots, scripts, results, users) is in the Docker volume `pgdata`. Back it up with:

```bash
docker compose exec db pg_dump -U testmax testmax > testmax-backup.sql
# restore into an empty database:
docker compose exec -T db psql -U testmax testmax < testmax-backup.sql
```

**Updating:** `git pull`, then `docker compose up -d --build`. The `api` container runs `alembic upgrade head` on start, so new migrations apply by themselves. Take a backup first.

**More throughput for AI jobs:** `docker compose up -d --scale worker=3`, and raise `WORKER_CONCURRENCY` if needed. Raising the organization limit alone (Runners page) does not add worker capacity.

### 12.2 Runner(s) (the machine that opens browsers)

Needs: Windows, macOS or Linux with **Google Chrome**, **Python 3.11 or newer** and **Node.js 20 or newer**; outbound access to the TestMax address.

1. In TestMax: **Runners → Connect a Runner**, type the address this runner will use to reach the server (for example `http://192.168.1.20:8000`), pick the operating system, **Download runner**. Open TestMax by that same address first.
2. Unzip, start `start-runner.bat` (macOS `start-runner.command`, Linux `./start-runner.sh`). The first start installs the Python and Node pieces, then opens a local setup page with a pairing code.
3. Enter the code on the Runners page. The runner starts by itself afterwards; the setup page has the headed switch, the number of slots and the log.

From a source checkout instead:

```bash
cd runner && npm install && cd test-runtime && npm install && cd ../..
python -m runner pair --server http://SERVER:8000
python -m runner run [--headed]
```

Use one runner per machine and keep its folder path short (Windows stops at 260 characters in a file path, which once made per-script screenshots disappear). Never copy `runner.json` to another machine: it is that runner's key; pair each machine on its own.

### 12.3 Development setup on a laptop

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt     # Windows; Linux/macOS: .venv/bin/python
docker compose up -d db                                          # just the database, for the tests
.venv/Scripts/python.exe -m pytest -q
docker compose up -d --build                                     # whole stack at http://127.0.0.1:8000
```

---

## 13. Rules the project is built under (`CLAUDE.md`)

1. Build only the approved scope; one approved task at a time, with a short scope written first for approval.
2. The app must run independently of Claude Code and start with one command.
3. Never modify the original system prompts; copy them unchanged from Pilot 1.
4. Reuse the proven Pilot 1 engines; do not rewrite what works without approval.
5. No new dependencies, services or tools without approval; keep the stack simple.
6. Verify before declaring success; report failures honestly with output.
7. Keep API keys and secrets out of logs, the repo and chat; never commit real keys.
8. Do not copy or expose real customer data.
9. Never modify the verified Pilot 1 folder.

---

## 14. Known gaps and honest limits

- **Not built:** WebSocket live status (polling is used), Jira/Azure DevOps sync, dashboards, billing, email (no verification mail, no invitations), a polished installer `.exe`, noVNC live browser view, cost/metering report screens (the data is stored per job: provider, model, tokens, duration), scheduling, parallel batches.
- **Not verified:** a runner on a second computer or another network; macOS and Linux start files (written the same way, never run); a deployment behind HTTPS; browsers other than Chrome; UI in browsers other than Chromium; more than three browsers at once or memory use under load; the 10 to 20 concurrent scans/executions load test from `HANDOFF.md` test 3 was designed for but its numbers are in `PROGRESS.md`, not in this guide.
- **Design limits:** the same job limit counts AI and browser jobs together; a job tied to a runner that stays offline waits until someone cancels it; untied jobs can be taken by an old runner (0.1.x) that is online; data variations and batch edits are last-write-wins; passwords typed into test data are stored in script text; the server speaks plain HTTP.
- **Quality:** AI quality was validated with the models used so far; a different model needs the check described in section 7.2.

---

## 15. Timeline

| Date | Step |
|---|---|
| 14 Nov 2026 | `HANDOFF.md`, scope, rules, environment check |
| 14 to 15 Nov | Site, users, projects, requirements, runners and pairing, scan, test cases, scripts, execute and reports, concurrency (slots), choose a runner, job limit |
| 15 Nov | UAT design study, UI redesign (phases a to f) |
| 15 Nov | Project put under git: baseline `07d1949` (tag `pilot2-before-data-driven`) |
| 15 Nov | Data-driven testing (`5104065`) |
| 15 Nov | Requirement source and tags (`13ecac4`, tag `pilot2-requirement-grouping`) |
| 15 Nov | Test batches (`466977b`, tag `pilot2-test-batches`) |
| 17 Nov | Pushed to `https://github.com/ProdAdmin5/TestMax2.0`; this guide |

For the exact numbers and what each step verified, read `PROGRESS.md`.
