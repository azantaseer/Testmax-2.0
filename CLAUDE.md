# TestMax Pilot 2 — Permanent Rules

Pilot 2 is a separate project from the verified pilot at `D:\Projects\TestMax-Independent-Pilot`. Never modify that folder.

## Purpose
Pilot 2 exists to PROVE that the TestMax production backend should be rebuilt entirely. Pilot 1 already proved the AI quality (requirements, test cases, scripts, autonomous scan). Pilot 2 must prove what the current prod system cannot do. See `HANDOFF.md` for the success tests.

## Rules
1. Build only the approved Pilot 2 scope in `HANDOFF.md`. No extra features, pages or integrations.
2. Develop one approved task at a time. Write a short scope for approval before building each task.
3. The app must run independently of Claude Code, and start with one command (Docker Compose is the target).
4. Never modify the original TestMax system prompts. Copy them unchanged from `D:\Projects\TestMax-Independent-Pilot\system-prompts`. Any change needs explicit approval.
5. Reuse the pilot's proven engines and logic. Do not rewrite what already works without approval.
6. Do not add dependencies, services or tools without approval. Keep the stack deliberately simple. No Redis, RabbitMQ, Kubernetes or microservices unless a measured need is shown and approved.
7. Stack is PROPOSED, not yet approved: Python + FastAPI, PostgreSQL (SQLAlchemy + Alembic), a Postgres-based job queue, WebSocket for live status, Docker Compose. A look-alike UI for the core flow only. Get the user's approval of the stack and UI framework before building.
8. Verify functionality before declaring success. Report failures honestly with output.
9. Keep API keys and secrets out of logs, the repo and chat. Never commit real keys.
10. Do not copy or expose real customer data, account numbers, names or addresses from reference material.
11. Concise responses. Stop after each task.
