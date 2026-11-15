# Step 7 — Test case generation and test script generation (scope for approval)

Status: APPROVED 14 Nov 2026, with Execute (7d) included in the same session and every approval named below granted by the user. Built and tested: see `PROGRESS.md`, section Step 7.

## Goal
From a scanned requirement, generate test cases (Pilot 1 stage 3), then one Playwright script per test case (stage 4), as background jobs on the server, saved in Postgres, with screens. Quality target: the same as Pilot 1 on the same requirement (Pilot 1's saved runs: 8 to 23 cases per requirement, all five types, 115 to 260 s and about 35k to 60k tokens per set; scripts 28 to 60 s and 7k to 13k tokens each, all passing `node --check`).

## What is ported (logic unchanged, only storage and transport change)
- `tcg_engine.py`: scan lookup, user message, JSON validation and normalisation (approved verbs, five columns, `sourceElementId` mapping, documented test data, duplicate removal, technical-leak scrub), one bounded retry with the errors fed back, truncation check. The Word file from `docx_builder.py`.
- `tsg_engine.py`: locator candidates built from the scan (never invented by the AI), script header and helpers, body validation, `node --check`, one bounded retry, "blocked, needs test data" detection.
- Prompts `test-case-generation-v2.0.txt` (SHA-256 `b876f5ca...4ae7`) and `test-script-generation-v1.0.txt` (SHA-256 `5b3f31e2...ab99`) copied **unchanged**, with SHA-256 tests that also compare to the Pilot 1 files.
- Scan rule as in Pilot 1: test cases use the **latest scan of the same requirement** that has a usable App Map; a scan of another requirement is refused. The requirement text used is the snapshot saved with the scan.
- Not ported: Pilot 1's file folders, in-memory run state, `update_tsg_contract` (the script job reads the test case from the database), the Run button (see "Execute" below).

## Design
- **Two job types on server workers** (same queue, per-organization cap, lease, recovery, token metering as `generate_requirements`): `generate_test_cases` (one per requirement) and `generate_test_script` (one per test case). Provider and model are fixed when the job starts (`effective_ai` + `require_model`; no hidden default).
- **Generate Scripts for many cases = one job per case**, so scripts of one set run in parallel within the organization's cap and a failure affects one case only.
- **Tables (migration 0007):** `test_case_sets` (one generation: requirement, scan, model, prompt hash, tokens, duration, coverage, warnings, mapping gaps, "not generated" list, raw answer), `test_cases` (project-wide number TC-001..., type, title, overview, prerequisites, objective, expected result, steps as JSON, link to requirement/set/scan), `test_scripts` (number TS-001..., script text, metadata: elements used, "NOT AUTOMATED" notes, steps without a scanned locator, blocked message, model, tokens, prompt hash). All carry `tenant_id` and go through `scoped_select` and the project check.
- **One script per test case; regenerating replaces it.** Regenerating test cases adds a new set (older cases stay; a case can be deleted, which also deletes its script). Version history is out of scope.
- **Word export** built on demand from the database (no files stored), same layout as Pilot 1.
- **Output limits:** Pilot 1 passes `max_completion_tokens` (96000 for cases, 32000 for scripts) and stops with a clear message when the answer is cut off. Pilot 2's `complete()` has no such limit today. I add an optional `max_output_tokens` and a `truncated` flag to `complete()`: OpenAI gets `max_completion_tokens` (same as Pilot 1), Anthropic gets `max_tokens` (capped by its existing setting `ANTHROPIC_MAX_OUTPUT_TOKENS`, default 32000, which may be too small for large requirement sets; I will report if so). The provider code stays in `app/providers.py`.

## Screens (plain HTML/ES modules, prod tokens)
- Project page gets the prod-style **breadcrumb and pill tab bar: Requirements | Test Cases | Test Scripts** (needed to reach the new lists).
- **Requirement detail:** "Generate Test Cases" button (enabled when a scan exists), a start dialog (scan used, model, a note if the scan is PARTIAL), a live progress card like the scan's, link to the result.
- **Test cases list** (as `Test Case 1.png`): Requirement, Test Case id, Title, Type pill, Priority, Script ID, Created, Updated; search, sort, pagination, "Generate Scripts" for selected cases, Word download.
- **Test case detail** (as `Test Case 2/3.png`): description card, steps table with "Technical columns" (shows the source element), details panel, "Generate Test Script", delete, "not generated" list and validation warnings of the set.
- **Test scripts list** (as `Test Scripts 1.png`) and **script viewer** (as `Test Scripts 2/3.png`): dark code card with Copy and Find, details panel, "NOT AUTOMATED" notes, "needs test data" warning, Regenerate.

## Tasks (each built, tested and reported before the next)
| # | Task | Done when |
|---|---|---|
| 7a | Test case generation job, tables, API, Word export, prompt copy, `max_output_tokens` in providers | Ported Pilot 1 unit tests pass; mocked job tests (both providers); isolation tests; one live run on a scanned Mammoth-AI requirement compared with Pilot 1 (count, types, steps, tokens, time) |
| 7b | Test case screens + tab bar + Generate Test Cases dialog | Whole flow in the browser: generate, watch progress, list, detail, download Word, delete |
| 7c | Script generation job, tables, API and screens (list, viewer, bulk Generate Scripts) | Mocked tests; live scripts for several cases of the live set; all pass `node --check`; one or two also run once ad hoc (scratch copy, Pilot 1's Playwright read only) to show they work; compared with Pilot 1's scripts |

## Needs approval
1. **Node.js in the server image** (for `node --check`, part of Pilot 1's script validation). Proposed: copy the `node` binary from the official `node:22-slim` image with a multi-stage `COPY --from` (pinned; image size increase to be measured, I expect under 150 MB). Without it scripts cannot be syntax-checked on the server.
2. **New tables** (above) and the `max_output_tokens` change to `complete()` in providers.
3. **Project breadcrumb and tab bar** (restyles the project page a little).
4. **Script language:** JavaScript (`.spec.js`), as Pilot 1, because the prompt is unchanged. Prod shows TypeScript; the language chip will say "JavaScript".
5. **Execute (decided by the user: built in this session as task 7d;** `@playwright/test` 1.64.1, the version Pilot 1 uses, lives in `runner/test-runtime/`). The original recommendation was the next session. It needs `@playwright/test` on the runner (new dependency), a `run_script` runner job, per-step results, screenshots and logs storage, the progress window, the report screen and run history on the viewer; there are no prod screenshots of those screens, so they are not verified against prod.
6. **Left out on purpose:** editing test cases or steps (pencil and Edit buttons, read-only like Pilot 1), manual "Create Test Case", Scope column, Assigned To, execution status and time columns (empty until Execute), filters.

## Verification plan
Automated: ported Pilot 1 tests for validators, locator building, header, scan matching and documented data; SHA-256 tests for both prompts; mocked jobs for OpenAI and Anthropic (success, invalid JSON, validation retry, truncation, no scan, mismatched scan, no model chosen); two-organization isolation on every endpoint; docx opens with `python-docx`; keep the existing 186 tests passing. Live (real OpenAI key, tell you the token use before and after): one test case set with `gpt-5.5` and, if you wish, one with `gpt-6.1-sol`; about 3 to 5 scripts. Anthropic stays mock-only (no key).

## Risks
- Live generation costs money: about 40k tokens per test case set and 10k per script.
- The latest Mammoth-AI scans had few VERIFIED locators (0 to 7 vs 19 to 40 in later Pilot 1 scans). Scripts may need more fallback locators than in Pilot 1; I will report it, not hide it.
- Test cases with placeholder data (no documented login) produce scripts that stop with `test.skip`; the UI will say why, as Pilot 1 does.
