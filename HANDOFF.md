# Pilot 2 Handoff

Written 14 Nov 2026 from the session in `D:\Projects\TestMax-Independent-Pilot`. Read this first.

## Background
- TestMax development started 19 Jan 2026. Three modules: Requirements, Test Cases, Test Scripts.
- In ~11 months the prod dev team never fixed: proper requirement generation, test case coverage, test case and step quality, autonomous app scan, working Playwright scripts.
- The user built Pilot 1 (`D:\Projects\TestMax-Independent-Pilot`, Python/FastAPI, plain HTML/JS, OpenAI, Playwright MCP + Playwright Test). User reports: requirements excellent, coverage per system prompt, test case quality high, scan 100% autonomous, generated scripts always run on the local machine.
- Pilot 1 limits: single user, global in-memory state, no auth, no database (files under `data/`), one scan and one run at a time, headed Chrome. Pipeline: requirements -> scan (App Map) -> test cases (+ .docx) -> scripts -> run. See `D:\Projects\TestMax-Independent-Pilot\TestMax_Pilot_App_Map.md`.

## Goal of Pilot 2
Prove that the prod backend should be rebuilt entirely. The user plans to redo the prod backend. Pilot 2 is evidence, shown through a look-alike UI for the core flow only.

## Success tests (the proof)
1. Quality holds in a real product: requirements, scan, test cases, scripts run through the UI, saved in Postgres, pilot prompts unchanged.
2. Multi-company: two companies, separate logins and data, no cross-visibility.
3. Concurrency: 10-20 scans/executions at once from different users; queue + worker pool; live status in the UI; per-company concurrency limits.
4. Reliability: kill a worker mid-job -> job recovers or fails cleanly with a clear message; restart the server -> nothing lost.
5. Simplicity: one command starts everything; few moving parts; same code on laptop, server or cloud; no AWS.
6. Cost: measure compute and cost per scan and per run; compare with the current prod bill (below).

## In scope
Screens styled like the prod UI: login, project, requirements (list, detail), scan, test cases (list, detail with steps), scripts (list, viewer), execute with report. Backend: FastAPI, Postgres, Postgres job queue, workers running the pilot engines, runner connection (pairing + job pickup; a simple runner script is enough).

## Out of scope
Jira/Azure DevOps sync, dashboards, fleet/machines pages, billing, AI usage gates, the prod "Test Planning" six-step flow, requirement quality scores, duplicate detection, polished installer, live noVNC viewer, GraphQL.

## Decisions made (user-approved unless marked)
- Backend: Python + FastAPI + PostgreSQL, boring stack, single code base (stack approval still pending, see CLAUDE.md rule 7).
- Execution model: RUNNER-ONLY. Customers connect their own machine (laptop or remote VM) to TestMax. No TestMax-hosted shared execution pool for now. Runner pairing is a device-code flow: runner asks for a code, user approves in the web app, runner receives a revocable per-company key, then holds an outbound WebSocket (HTTPS polling fallback) and claims jobs atomically. Outbound-only, no inbound firewall ports. If ever hosted: one fresh container per job, non-root, CPU/memory caps, no shared volumes, deleted afterwards.
- Data: structured data in Postgres; scripts optionally synced to client Git later; screenshots/video on the runner or client-owned storage.
- Queue: Postgres `SKIP LOCKED`. One jobs table with a type column. Per-company concurrency limits and round-robin fairness.
- API for Pilot 2: the look-alike UI is new, so use a clean REST/JSON + WebSocket API designed around the pilot's data (no GraphQL adapters).

## Production system facts (for comparison)
- Prod: NestJS GraphQL on AWS Lambda, Prisma, RDS PostgreSQL, 4 SQS queues with dead-letter queues, ECS Fargate for browser work (Playwright + MCP agent, healing loop, noVNC), API Gateway WebSocket, Cognito, S3, SES/SendGrid, OpenAI. Org > Workspace > Project multi-tenancy with RBAC. A separate on-prem code base uses MinIO/RabbitMQ/Keycloak.
- Prod sizing: one 8 vCPU/16 GB execution task = 5 concurrent browsers (1 vCPU + 2 GB each, plus overhead). Users see "5 workers busy, you are in queue" when all 5 are in use. The ceiling is configuration/scaling, not clearly a code limit (unverified: check ECS desired/max count, autoscaling policy, Fargate vCPU quota).
- Oct 2026 AWS bill: total USD 3,084.69; ECS (Fargate) USD 2,567.45 (83%, about 9 always-on 8 vCPU/16 GB tasks), VPC 199.69, EC2 100.44, RDS 66.93, load balancers 67.07, ECR 33.89, CloudWatch 28.09, SQS 6.07, API Gateway 5.40, Lambda 4.36, data transfer 4.15, S3 0.66, others ~0. The product services themselves (Lambda, SQS, API Gateway, Cognito, S3) cost about USD 16.

## Prod UI reference
- Location: `D:\Projects\mammothai-main\mammoth-ai` (frontend only; Next.js 16, React 19, Tailwind 4). Backend not included. Read `HANDOVER.md` and `mammoth-ai/docs/DOMAIN.md` there.
- 64 pages, ~930 source files. Backend calls are hand-written GraphQL strings in `mammoth-ai/services/*.service.ts`; `mammoth-ai/docs/frontend-graphql-field-inventory.md` lists every field used.
- Style tokens: `mammoth-ai/app/globals.css` (@theme) and `app/semantic-utilities.css`. Primary green #5ECF80 (hover #4ABF6A, deep #3A9E59), brand navy #154260, page/sidebar background #FAFAFA, text #0A0A0A / #2C2C2C / muted #717182, borders black at 10% opacity, table header #F9FAFC, input #F3F3F5. Fonts: Inter (body), Manrope (marketing), Sora (display). Logos and icons are in `mammoth-ai/public`.
- Screenshots from the user will be placed in `reference/screenshots/` (login, home/sidebar, requirements list, requirement detail, test case detail with steps, scripts list, script viewer, execution report/progress modal). Use them for layout fidelity.
- Prod data shape notes: test case steps carry action type, locators, test data, expected result; scan output is a "Journey Flow" (stages -> actions + action trace); jobs report progress by status query plus WebSocket; execution report expects per-step results, screenshots, video, logs.

## Pilot 1 output shapes (to reuse)
- Test case JSON: `testCaseType`, `title`, `overview`, `preRequisites`, `testObjective`, `expectedResult`, `steps[]` with `stepNo`, `testStep`, `fieldName`, `testData`, `expectedResult`, `sourceElementId`.
- Scan: `scan_result.json` with `parsed` (App Map), `raw`, `schema_issues`.
- Scripts: `TC-001.spec.js` plus `.meta.json`; run via `node @playwright/test/cli.js test ... --headed`.

## Working notes
- Project rules in `CLAUDE.md` require approval before new dependencies and one approved task at a time. First task: write a Pilot 2 scope and task plan for the user's approval.
