# Step 2 — Site setup: UI shell, login, tenant creation, tenant AI model (scope for approval)

Status: DRAFT 14 Nov 2026. Step 1 (environment) finalized. Nothing of Step 2 is built.

## Goal
`docker compose up` gives a working site where a new company creates its own tenant account, signs in, and its Super Admin picks the tenant's AI provider and model. Nothing else.

## What gets built
1. **Compose:** `db` (postgres:16, named volume) + `api` (FastAPI, serves the UI). Alembic migrations run automatically on start. `.env.example`, git-ignored `.env`, `.gitignore`. No worker yet (Step 5+).
2. **Schema (migration 1):**
   - `tenants`: id, name, ai_provider, ai_model, created_at
   - `users`: id, tenant_id, name, email, password_hash (argon2), role (`super_admin` | `site_admin` | `user`), status (`active` | `pending`), ai_provider and ai_model (nullable; used from Step 3), created_at
   - `sessions`: token hash, user_id, expires_at
   - `join_requests` is **not** a table: a join creates a `users` row with status `pending` (decided by question 2).
3. **API (REST/JSON):** sign up with new organization, sign up to join an organization, sign in, sign out, current user, get/update tenant AI settings (Super Admin only), list available models, test connection. Session cookie: HttpOnly, SameSite=Lax. Every tenant query goes through one tenant-scoped helper.
4. **AI settings:** provider OpenAI or Anthropic; model list loaded from the provider's API (like Pilot 1's "Load Models"), selection stored on the tenant; "Test Connection" makes one tiny call. Providers without a key in `.env` are shown disabled. No engine/adapter work yet (starts at Requirements, Step 5). A function `effective_ai(user)` (user setting, else tenant, else system default) is written now and unit-tested; the per-user UI is Step 3.
5. **UI (look-alike):**
   - Login page as `01-login.png`: Sign In tab; Sign Up tab with "Create your organization account" and a "Join any organization" button.
   - Create-organization form: organization name, your name, email, password.
   - Join-organization form: pick organization, your name, email, password; ends on "Waiting for approval by your admin".
   - App shell as `02-workspace-projects.png` minus workspaces: top bar with logo and user chip (menu with Settings, Sign out), sidebar with organization name and "Projects" (placeholder, empty state), collapse handle.
   - Settings > AI Model page (Super Admin only).

## Not in Step 2
User list/management, role editing, per-user model UI, approving join requests (all Step 3), projects (Step 4), requirements (Step 5), forgot password, email verification, social login, dashboards.

## Done when (all verified and reported with output)
- From a clean state (`docker compose down -v`, then `docker compose up`) the site is reachable at one URL with no manual steps.
- Create tenant A and tenant B through the UI; each Super Admin sees only their own organization name and settings.
- Automated tests pass: password hashing, session expiry/logout, tenant isolation (A's token cannot read or change B's settings), Super Admin-only settings, `effective_ai` precedence, pending users cannot sign in.
- AI settings: set tenant A to OpenAI and tenant B to Anthropic, saved values survive `docker compose restart`; Test Connection succeeds with real keys and fails clearly without one. No key ever appears in logs, API responses or the repo.
- Login and shell checked in the browser against the two screenshots; differences listed honestly.
- Prompts are not touched in this step.

## Needs your decision (defaults in bold; "approve with defaults" is enough)
1. **UI option:** **A** plain HTML + ES modules + hand-written CSS from prod tokens, served by FastAPI (no build step), B Next.js, or C React + Vite.
2. **Join flow:** **user picks an organization from a searchable list; account is created as `pending`; the organization's admin approves in Step 3.** Concern: a public list of all tenant names lets anyone see which companies use TestMax. Alternative: user types the exact organization name (no list).
3. **One account per email, belonging to exactly one organization** (not several).
4. **Roles:** Super Admin = everything including AI settings and admins; Site Admin = manages users; User = works in projects. Only Super Admin vs others is enforced in Step 2.
5. **AI keys:** **platform keys in server `.env`** (tenants only choose provider/model). Alternative: each tenant enters its own key (stored encrypted) — more work.
6. **Password rule:** **minimum 10 characters, no other rules.**
7. **Organization name unique** (case-insensitive) so the join list is unambiguous: **yes.**
8. **Assets:** copy `testmax-logo` SVG/PNG from `mammoth-ai/public`; Manrope font from `app/fonts` (read-only copy); Inter and Sora are Google Fonts: **download once and self-host** so the app has no external calls, or fall back to system fonts.
9. **Screenshots (optional but improves fidelity):** prod Sign Up tab, user menu dropdown, any settings/AI page. If not supplied, I design them from the prod tokens and mark them "not verified against prod".

## Dependencies
None new beyond what is already installed (Step 1). Docker images: `postgres:16`, `python:3.14-slim`.

---

## RESULT (14 Nov 2026) — built and verified, awaiting user sign-off

**Verified (output seen):**
- Clean start: `docker compose down -v` then `up -d --build` (78 s including image build) gives `db` and `api` healthy; `/health` ok; migrations ran automatically.
- 39 automated tests pass (auth, sessions, throttle, tenant isolation, Super Admin-only, AI precedence, provider error translation, security headers).
- UI (built-in browser): created tenant A through the UI; validation tooltip shown; join flow (search, choose, submit) ends on "Waiting for approval"; pending user gets a clear 403 message at sign-in; second tenant B created; B's admin sees only "Birch Labs"; AI Model page opens for Super Admin.
- AI settings, using fake keys only: A saved OpenAI, B saved Anthropic; both survive `docker compose restart` and `down` then `up`; sessions survive too. Test Connection and Load Models reached the real providers with a fake key and returned "rejected the server's API key", with no key text in the response or in the container logs.
- Without keys: providers show disabled "No API key configured on the server"; Test Connection and Save return that message.
- Final state: blank keys, database wiped (`down -v`), stack running and healthy.

**NOT verified (honest list):**
- Test Connection and Load Models with REAL OpenAI/Anthropic keys: success path only covered by mocked tests. Needs keys in `.env` (user to add), then a live check.
- Sign Up choice page, create/join forms, pending page, user menu and AI Model page are NOT compared against prod (no screenshots; designed from prod tokens). Login and shell were compared by eye against screenshots 01/02 and look close; pixel-level differences not measured. A small left-panel spacing fix was made after the first comparison and not re-compared.
- Mobile layout only roughly checked by CSS, not tested.

**Known limits / decisions to remember:**
- "Forgot Password?" link and Sign Up social options omitted (no dead links).
- Sign-up reveals whether an email or organization name already exists (needed for clear errors); the join list is public (names only, min 2 characters, max 20 results).
- Login throttle (10 failures per email per 5 min) is in-memory per process: fine for one api container, to be moved to Postgres before running several.
- UI files are copied into the image: a UI edit needs `docker compose up -d --build`.
- Models come from the provider's list; `DEFAULT_AI_MODEL=gpt-5.5` is the system default (as in Pilot 1).
