# Pilot 2 — Scope and Task Plan (for approval)

Status: DRAFT, 14 Nov 2026. Nothing has been built or installed. Sources: `CLAUDE.md`, `HANDOFF.md`, Pilot 1 `TestMax_Pilot_App_Map.md` (read only).

**Goal:** produce evidence that the TestMax prod backend should be rebuilt, by showing what prod cannot do today: multi-company isolation, real concurrency, crash recovery, one-command deploy and a measured cost per scan/run, with Pilot 1 AI quality kept intact.

**Reference screenshots:** being added one by one in `reference/screenshots/` (see `NOTES.md` there). Layout fidelity depends on them.

---

## REVISION 1 (14 Nov 2026) — user decisions, supersede conflicting text below

**New in Pilot 2 (beyond HANDOFF):**
1. **Self-service tenant creation:** login page offers "Create your organization account" (user becomes the tenant's Super Admin) and a "Join any organization" button.
2. **Tenant-level user management:** each tenant's Super Admin / Site Admin creates and manages that tenant's users.
3. **Multi-model AI (OpenAI and Anthropic):** provider/model chosen at **tenant level** (default for all users); each **user can override** it in their own settings. Precedence: user setting > tenant setting > system default.
4. **Workspaces removed.** Hierarchy is Tenant > Project. Sidebar lists projects directly.

**Working method:** strictly one module at a time, next module only after the user finalizes the previous one. Order:
1. Environment setup (check, install what is missing)
2. Site setup: UI shell, login, tenant creation, tenant-level AI model choice
3. User management per tenant, with per-user AI model
4. Project creation
5. Requirements
(then, same method: runner, scan, test cases, scripts, execute/report, metering, load/reliability, evidence report; the old task list in section 6 is a rough guide only, its order is superseded)

**Impact on the plan (needs approval):**
- Pilot 1 engines call OpenAI only (`chat.completions`, OpenAI tool-calling in the scan loop). Anthropic support needs a thin provider adapter and the `anthropic` package. Prompts stay unchanged (rule 4). Claude quality on these prompts is **unproven**; test 1 must be repeated per provider.
- Test 2 (isolation) now also covers tenant self-signup and user-level settings. Test 6 (cost) is measured per provider.
- Python target version: 3.14 (what is installed and what Pilot 1 uses), not 3.12.

**New open questions:**
- A. "Join any organization": how does a user join? (invite code from a tenant admin; request to join approved by admin; or by email domain?) Proposal: join request approved by the tenant admin.
- B. Roles: what can Super Admin vs Site Admin vs normal User do? Proposal: Super Admin = everything incl. AI model and Site Admins; Site Admin = manage users; User = work in projects.
- C. AI keys: platform-owned keys in server `.env` (TestMax pays; simplest), or each tenant enters its own key (stored encrypted in Postgres)? Proposal for Pilot 2: platform keys in `.env`.
- D. Pilot 1 uses OpenAI only per its App Map. Is there an Anthropic version of the engines somewhere, or is this new work?
- E. Email verification on self-signup: no email service is in scope. Proposal: none in Pilot 2 (tenant created immediately).

**Environment check, 14 Nov 2026 (this machine):** see the "Environment" section at the end.

---

## 1. The six success tests and how each is measured

| # | Test | Measured by | Pass line (proposed, please confirm) |
|---|---|---|---|
| 1 | Quality in a real product | Run the same BRD and target app used in Pilot 1 through the UI only. Compare output with Pilot 1: requirement count, scan control/locator counts and status, test case count and types, scripts that execute. System prompt files verified by SHA-256 equal to the Pilot 1 files (automated test). All data read back from Postgres. | Comparable to Pilot 1 on the same input; scripts run on a runner and pass where Pilot 1's passed; prompt hashes identical |
| 2 | Multi-company | Two companies, each with its own users and runner. Automated cross-tenant test hits every endpoint, the WebSocket and runner endpoints with company A's token against company B's IDs; plus a manual UI check. | 0 cross-company reads or writes; IDs of another company return 404 |
| 3 | Concurrency | 10-20 simultaneous jobs (mix of scans and executions) submitted by different users in 2+ companies. Record queue wait, start-to-finish time, peak running, per-company cap respected, UI live status updates, failures. | All jobs finish or fail with a clear message; per-company limit never exceeded; UI shows live states; no job starved (fairness) |
| 4 | Reliability | (a) Kill a worker process mid-job. (b) Kill a runner mid-job. (c) `docker compose restart` of API/DB during jobs. Check job state, message shown, nothing lost. | Job is requeued and completes, or fails with a clear reason within the lease timeout; no job stuck "running"; no data lost after restart |
| 5 | Simplicity | `docker compose up` on a clean machine from a fresh clone and `.env`; count of containers/services; run same compose on a second machine (a rented VM). No AWS services. | One command, 3 services (db, api, worker) + runner outside; identical files on laptop and VM |
| 6 | Cost | Each job records LLM tokens in/out (priced from a configured price table), wall-clock seconds, browser-seconds on the runner. Report cost per scan and per execution, plus fixed server cost (VM + Postgres). Compare with Oct 2026 AWS bill (USD 3,084.69; Fargate 2,567.45). | A table, with stated assumptions. See caveat in 7 |

---

## 2. Screens in scope (look-alike of prod, core flow only)

1. Login
2. Project (list, create, open)
3. Requirements: list, detail (includes BRD upload + Generate)
4. Scan (start, live progress, App Map summary)
5. Test cases: list, detail with steps (+ .docx download)
6. Scripts: list, viewer (+ Generate)
7. Execute with report (live progress, per-step results, screenshots, logs)

**Needs approval (not in HANDOFF screen list but required by the approved runner model):** a small **Runners** page: approve a pairing code, see online/offline, revoke. Without it the device-code flow has no UI. Company/user creation is a seed CLI, not a screen.

Out of scope stays as in HANDOFF.md.

---

## 3. Backend components

| Component | Responsibility |
|---|---|
| API (FastAPI) | REST/JSON, auth, tenancy scoping, WebSocket for live status, serves the UI, runner endpoints |
| Postgres | companies, users, sessions, projects, requirements, scans (App Map as JSONB), test case sets/cases/steps, scripts, runs/step results, jobs, runners, usage metering |
| Job queue | One `jobs` table: type, company_id, status, payload, attempts, lease/heartbeat, result, error. Claimed with `SELECT … FOR UPDATE SKIP LOCKED`. Per-company concurrency cap, round-robin fairness |
| Worker (same image, separate process, scalable) | Runs server-side jobs: requirements, test cases, scripts (LLM only, no browser). Ports Pilot 1 engines |
| Runner endpoints | Pairing, claim job, heartbeat, upload result/artifacts, LLM proxy |
| Live status | Job state changes pushed over WebSocket (LISTEN/NOTIFY from Postgres, no extra service) |
| Engines (copied from Pilot 1) | `tcg_engine`, `tsg_engine`, `docx_builder`, requirements logic from `main.py`, `scan_engine` loop. Logic unchanged; only storage (files to Postgres) and transport (see 5) change. System prompts copied byte for byte |

---

## 4. Proposed stack — **NEEDS APPROVAL (CLAUDE.md rules 6 and 7)**

| Item | Proposal |
|---|---|
| Language/API | Python 3.14 + FastAPI + Uvicorn (same as Pilot 1) |
| DB | PostgreSQL 16, SQLAlchemy 2 + Alembic, psycopg 3 |
| Queue | Postgres `SKIP LOCKED` (no Redis/RabbitMQ) |
| Live status | WebSocket + Postgres LISTEN/NOTIFY |
| Auth | Email + password, hashed (argon2), server-side session cookie for the web app. No external IdP in Pilot 2 |
| Deploy | Docker Compose: `db`, `api`, `worker` (same image) |
| AI | OpenAI, same models/config as Pilot 1 (`openai`, `python-docx`, `pypdf` as in Pilot 1) |
| Runner | Python script (+ Node, Playwright MCP, Playwright Test, Chrome as in Pilot 1); optional runner Docker image later |
| Tests | pytest (as in Pilot 1) |
| UI | **Decision needed**, options: **A (recommended)** plain HTML + ES modules + hand-written CSS from prod tokens, served by FastAPI, no build step, nothing new to deploy. **B** Next.js/React/Tailwind like prod: closest visual and code reuse from `mammoth-ai`, adds a Node build/service and ~dozens of dependencies. **C** React + Vite static build: middle ground |

Exact package list with versions will be submitted for approval in Task 1 before install.

---

## 5. Runner design

- **Runner-only execution.** Anything needing a browser (scan, execute) runs on a customer-connected machine. LLM-only jobs run on server workers.
- **Pairing (device-code):** runner calls `POST /runner/pair` -> gets short code. User enters code on Runners page -> server issues a revocable per-company key. Key shown once to the runner, stored hashed on the server.
- **Connection:** outbound WebSocket to the API; fallback to HTTPS long-poll. No inbound ports.
- **Claiming:** runner asks for a job; server claims atomically (SKIP LOCKED) for that runner's company only, honouring the company concurrency cap and the runner's own capacity (N concurrent browsers).
- **Lease:** runner heartbeats every ~10 s. Missed heartbeats beyond the lease (~45 s) -> job requeued (max 2 attempts) or failed with "runner lost, job not completed after N attempts".
- **Scan on runner:** the scan loop and Playwright MCP run on the runner (browser must reach the target app from there). **Proposal:** the runner does not hold the OpenAI key; every LLM call goes through a server `/runner/llm` proxy that meters tokens per company/job. This is a transport change inside `scan_engine`; needs approval under rule 5.
- **Execute on runner:** runner downloads the script, runs `playwright test` headless (headed optional), streams step/log events, uploads per-step results and failure screenshots (small; stored on the Compose volume, see open questions).
- **Security:** runner key scoped to one company; revocable; origin lock, scan policy and secret redaction from Pilot 1 stay on the runner. Hosted execution (one container per job) is explicitly not built.

---

## 6. Ordered task plan (each: scope for approval -> build -> verify -> stop)

| # | Task | Done when / verified by | Tests |
|---|---|---|---|
| 0 | Approve stack, UI option, open questions (this document) | User sign-off | - |
| 1 | **Foundation:** repo layout, Compose (`db`, `api`, `worker`), Alembic baseline, `/health`, `.env.example`, prompts copied + SHA-256 test | `docker compose up` gives healthy stack; hash test passes | 5 |
| 2 | **Companies, users, auth, tenancy:** schema, login/session, seed CLI (2 companies), company scoping helper applied on every query | Automated cross-tenant tests pass | 2 |
| 3 | **Job queue + worker + live status:** jobs table, claim/lease/heartbeat, retries, per-company cap, fairness, WebSocket events, stub job type | 20 stub jobs across 2 companies respect caps; kill worker -> recovery or clean failure; API restart loses nothing | 3, 4 |
| 4 | **Projects + requirements (server job):** BRD upload, port requirement extraction, store in Postgres | Same BRD as Pilot 1 gives comparable requirements; metering columns filled | 1, 6 |
| 5 | **Runner pairing and job pickup:** device-code flow, key, WS + polling fallback, runner script, LLM proxy, stub browser job | Runner pairs, claims stub job, heartbeat, revocation stops it; kill runner mid-job recovers/fails clearly | 2, 4 |
| 6 | **Scan on runner:** port scan loop via proxy, App Map to Postgres | Scan of Pilot 1 target app, status and counts comparable to Pilot 1 | 1, 6 |
| 7 | **Test cases + scripts (server jobs):** port `tcg`/`tsg`/`docx_builder`, store in Postgres, locators built as in Pilot 1 | Cases and scripts generated from stored scan; syntax check passes; .docx opens | 1 |
| 8 | **Execute on runner + report:** run script, stream per-step results, screenshots, logs | Pilot 1 passing case also passes here; failed case shows clear report | 1, 6 |
| 9 | **UI shell + Login + Project** (after screenshots exist) | Log in as two companies, see only own projects | 2 |
| 10 | **UI: Requirements screens + Runners page** | End-to-end requirement generation via UI | 1 |
| 11 | **UI: Scan + Test cases screens** (live status) | Scan from UI with live progress; case detail with steps | 1, 3 |
| 12 | **UI: Scripts + Execute/report screens** | Full flow BRD -> run result through UI only | 1 |
| 13 | **Metering and cost report:** per-job tokens/time/browser-seconds, price table, cost per scan/run output | Cost table generated from real runs | 6 |
| 14 | **Load and reliability campaign:** 10-20 concurrent jobs, kill tests, restart tests, second-machine deploy | Measured numbers recorded | 3, 4, 5 |
| 15 | **Evidence report:** one document: result per test with data, failures reported honestly, comparison with prod bill | Report delivered | all |

Tasks 9-12 could be built earlier, interleaved with 4-8, once screenshots are in; the order above keeps backend proof first.

---

## 7. Open questions (need the user's answer)

1. **Approve stack in 4**, and pick **UI option A, B or C**.
2. **Screenshots:** when will they be added? UI tasks (9-12) are blocked on them for fidelity.
3. **Target app and BRD for test 1:** reuse the Pilot 1 target app, BRD and credentials? (Please confirm which; credentials stay in the local `.env`, not in the repo.)
4. **Hardware for test 3:** 20 browsers is about 20 vCPU / 40 GB using prod's 1 vCPU + 2 GB per browser. Use a rented VM for the runners (and to prove test 5 on a second machine)? Cost and provider need approval. Laptop-only would cap concurrency at a lower number.
5. **OpenAI rate limits** at 10-20 simultaneous scans: acceptable to measure and report throttling, or is a higher-tier key available?
6. **Screenshots and video in the report:** store small failure screenshots on the API's Docker volume (proposed); skip video for Pilot 2? HANDOFF says screenshots/video live on the runner or client storage, so this is a pilot-only shortcut.
7. **Runner capacity:** default concurrent browsers per runner (proposed 3) and per-company job cap (proposed 5)?
8. **Runners page** (approve pairing codes) added to the screen list: approved?
9. **Scan LLM through the server proxy** (runner holds no OpenAI key): approved, given it changes engine transport?
10. **Cost test caveat:** with runner-only, browser compute is paid by the customer, so TestMax's own compute is only server + DB + LLM. Prod's USD 2,567 Fargate bill is mostly browser compute. Should the comparison show both (a) TestMax-side cost only and (b) total cost if TestMax also paid for browser compute at Fargate rates? Also, the Oct 2026 count of scans/runs in prod is needed to turn the bill into a cost per scan/run; do you have it?
11. **Docker Desktop** installed on this machine (Windows) so Compose is the dev target?
12. **Pass lines** in section 1: confirm or change.

**Stop point:** nothing proceeds until the user approves this scope and answers questions 1-3 at minimum.

---

## Environment (checked read-only on 14 Nov 2026; nothing installed yet)

| Item | State |
|---|---|
| Windows 11 Enterprise, 12 logical CPUs (i7-1255U), 15.7 GB RAM, D: drive has space | OK; 15.7 GB RAM caps local browser concurrency (see open question 4) |
| Python 3.14.3 (`C:\Python314`) | Present |
| Node v24.18.0, npm 11.12.0 | Present |
| Git 2.56 | Present |
| Google Chrome | Present (`C:\Program Files\Google\Chrome`) |
| winget 1.29 | Present |
| Playwright MCP / Playwright Test | Only inside Pilot 1's `node_modules` (`@playwright/mcp ^0.0.83`, `@playwright/test ^1.64.1`). Pilot 2 needs its own copy later, at the runner task. Never touch Pilot 1's |
| FastAPI, Uvicorn, OpenAI, pydantic, python-docx, pypdf, python-dotenv, python-multipart, httpx, mcp, pytest | Only in Pilot 1's `.venv`. Pilot 2 needs its own venv with pinned versions |
| PostgreSQL | **Missing** (no `psql`, no service, port 5432 free) |
| Docker / Docker Compose | **Missing** |
| WSL | Installed but no distribution |
| Virtualization | Hypervisor running (Windows security features on), so WSL2/Docker can work |
| Admin rights | Current session is **not elevated**; installing Docker Desktop or PostgreSQL will trigger a Windows UAC prompt the user must accept |
| Windows policy | "App Control for Business" is Enforced: a downloaded tool could be blocked; will be seen on first run |
| Keys | `OPENAI_API_KEY` and (new) `ANTHROPIC_API_KEY` go into a local, git-ignored `.env`; never in chat or repo |

**Remaining to get (proposal, needs approval):**
1. Docker Desktop (winget `Docker.DockerDesktop`) so the target is Compose from day one (rule 3, test 5). Alternative: native PostgreSQL 16 (winget `PostgreSQL.PostgreSQL.16`) for now and Docker later. Note: Docker Desktop has a paid licence for larger companies; confirm Mammoth-AI is eligible or use Docker Engine in WSL instead.
2. Pilot 2 Python venv at `D:\Projects\TestMax-Pilot2\.venv`, with Pilot 1's pinned versions for shared packages plus the new ones: `sqlalchemy`, `alembic`, `psycopg[binary]`, `argon2-cffi`, `websockets`, `anthropic`.
3. Deferred to the runner task: `@playwright/mcp`, `@playwright/test` (same versions as Pilot 1).

### Environment setup result (14 Nov 2026) — Step 1 done, awaiting user sign-off
- Installed Docker Desktop 4.99.0 (Docker Engine 29.9.3, Compose v5.7.0) via winget. Engine starts and runs containers.
- Verified: a throwaway `postgres:16` container (PostgreSQL 16.16) started, accepted a query from the Pilot 2 venv via psycopg, and was removed. No containers or volumes left.
- Created `D:\Projects\TestMax-Pilot2\.venv` (Python 3.14.3). Shared packages pinned to Pilot 1 versions; new: sqlalchemy 2.1.8, alembic 1.20.1, psycopg 3.3.9, argon2-cffi 25.1.0, websockets 17.4, anthropic 2.8.0. All import cleanly. Full freeze in `requirements.txt`.
- Not done on purpose: Playwright packages (runner task), `.env` / API keys (user places them locally).
- Note: in an already-open terminal, Docker may not be on PATH until a new terminal is opened (`C:\Program Files\Docker\Docker\resources\bin`).
