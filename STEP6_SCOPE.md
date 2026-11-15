# Step 6 — Runner and app scan (scope for approval)

Status: DRAFT 14 Nov 2026. Steps 2 to 5 are finalized (see `PROGRESS.md`). Nothing of Step 6 is built or installed.

## Goal
A customer connects their own machine to TestMax (a "runner"). From a requirement, a user starts a scan; the runner drives a real Chrome through Playwright MCP, the AI explores the app, and the App Map is saved in Postgres and shown in the UI. Quality target: comparable to Pilot 1's scan on the same requirement.

## Design (from the approved runner-only model)
- **Runner** = `python -m runner` on the customer's machine (this laptop for the pilot). It needs Python, Node, Chrome and the Playwright MCP package. It makes outbound HTTPS calls only; no inbound ports.
- **Pairing (device code):** runner asks `/api/runner/pair/start` and shows a short code. An admin enters the code on a new **Runners** page, names the runner, approves. The runner then receives a per-organization key **once** (stored hashed on the server, saved to a local git-ignored `runner.json`). Admins can revoke a runner; a revoked key stops working at once.
- **Connection:** HTTPS long-polling for Pilot 2 (claim, heartbeat, progress). WebSocket is deferred; polling is simpler and already satisfies "outbound-only". The UI keeps polling the job as it does today.
- **Queue reuse:** same `jobs` table, same `SKIP LOCKED` claim, same per-organization cap, lease and recovery. Job type `app_scan` is claimed only by runners of the same organization (server workers never claim it). A runner claims only while it has a free browser slot (default 2).
- **Who does what:**
  - Runner: the tool-calling loop, browser, safety policy, origin lock, budgets, loop guard, secret redaction (Pilot 1 logic, unchanged).
  - Server: holds the AI keys. The runner sends each model call to `/api/runner/llm`; the server picks the provider and model that were fixed at scan start (user setting, else organization, else system default), calls OpenAI or Anthropic, meters tokens on the job, and returns the result. **The runner never sees an AI key.**
  - Server also validates the App Map, builds the test-case and script input contracts (Pilot 1 code), and stores everything.
- **Prompt:** `app-scan-v1.5.txt` copied unchanged from Pilot 1 (SHA-256 checked by a test) and delivered from the server with each job.
- **Both providers:** the scan loop uses a neutral "messages + tools" format; the server converts it to OpenAI function calling or Anthropic tool use. Anthropic can only be tested with mocks until a key is set.

## Tasks (each built, tested and reported before the next)
| # | Task | Done when |
|---|---|---|
| 6a | **Runner pairing and job pickup.** Tables `runners`, `pairing_requests`; runner API (pair, heartbeat, claim, job heartbeat/progress/complete/fail); Runners page (approve code, list with online/offline, revoke); runner script with a stub job. | Pair through the UI; stub job claimed and finished; revoked runner is refused; runner of org A never sees org B's jobs; kill the runner mid-job: job requeued or failed with a clear message |
| 6b | **LLM proxy + neutral tool calling.** `/api/runner/llm`, OpenAI and Anthropic converters, per-job token metering. | Unit tests for both converters (mocked); live OpenAI call through the proxy; runner holds no key; a runner cannot use the proxy for jobs it did not claim |
| 6c | **Scan on the runner.** Port Pilot 1's scan loop, policy and validators; `scans` table; start Playwright MCP per scan; save App Map, history (redacted), contracts. Cancel support. | Live scan of the Sauce Demo login requirement (same BRD and requirement as Pilot 1): status, pages, controls, VERIFIED locators, tool calls and duration compared with Pilot 1's saved scan; origin lock and credential redaction proven by tests |
| 6d | **Scan UI.** "Scan App" on a requirement (URL, policy, authorized-test-environment checkbox, editable requirement text like Pilot 1, runner status), live progress, scan result page (pages, controls, locators, actions, gaps, raw JSON), cancel. | Whole flow through the UI; "no runner online" shown clearly |

## Verification plan
Automated tests for every task (pairing, key hashing, revoke, tenant isolation, claim rules, proxy auth, converters, ported scan functions; I will reuse relevant tests from Pilot 1's `test_scan_engine.py`). Live tests on this laptop with the real OpenAI key against the public Sauce Demo site: one scan from the UI, two scans at once from two organizations (per-organization cap and isolation), runner killed mid-scan, API restarted mid-scan. Baseline for comparison: Pilot 1's saved scans in `data/scans` (read only).

## Needs approval
1. **New dependency:** npm `@playwright/mcp` at Pilot 1's version (^0.0.83) in a new `runner/` folder (own `package.json`). `@playwright/test` is not needed until the scripts step. No new Python packages (`httpx` and `mcp` are installed).
2. **New tables:** `runners`, `pairing_requests`, `scans`; extra job columns (`runner_id`, `cancel_requested`).
3. **Runners page** is added to the screens (already proposed in the first scope).
4. **Chrome mode:** headless by default on the runner (no window), `--headed` option for demos. Pilot 1 used a visible window.
5. **Credentials:** same as Pilot 1: test logins go in the requirement text and are redacted from logs and results, but the requirement snapshot keeps what the user typed.
6. **Packaging:** the runner runs from this repo for now (shares the ported scan code). A standalone installer is out of scope.
7. **Polling instead of WebSocket** for the runner link in this step.

## Not in Step 6
Test cases, scripts, execute and report (next steps); hosted/containerized runners; noVNC; runner auto-update; cost report; per-runner scheduling rules beyond capacity.

## Risks
- Real scans cost money and take 1 to 5 minutes each; I will keep live runs few.
- Chrome or Node could be blocked by the Windows "App Control" policy; it worked for Pilot 1, so unlikely.
- Anthropic scan quality is unproven and not live-testable without a key.
