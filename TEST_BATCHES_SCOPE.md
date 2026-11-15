# Test Batches: scope (approved by the user, 15 Nov 2026, with the additions below)

Goal: run several test scripts in a chosen order in ONE browser session, so a later script (Search) starts where an earlier one (Register) left off, already logged in. Each script still runs alone exactly as today; the person provides the prerequisites for a single run.

## What is built
- A **Test Batches** tab in every project (list, create, edit, delete). A batch has a name, an optional description, a setting **When a script fails: stop the batch / continue with the next script** (chosen per batch; default stop), and an ordered list of scripts (1 to 8).
- **Each script added to a batch asks for its data variation** (DV-1 Default, or DV-2... by name; default DV-1), changeable in the batch editor, shown in the batch screens and in each result. The same script may appear twice with different variations. A script that is not data-driven only offers its own values (DV-1); the screen says so.
- **Run batch** opens one browser, runs the scripts in order in that same page, and reports one result per script (passed, failed, aborted, or not run because an earlier script failed and the batch stops), plus an overall status. Each script that ran is also stored as an ordinary execution of that script (with its data variation), so its own run history shows it; a script that did not run gets no execution.
- A batch run page: the order, each script's result, duration and data variation, a link to each script's full report (steps, screenshot), the overall status and the console output. A batch's page lists its last runs.
- Runs on the runner chosen in the Execute dialog (as for single scripts). Job limit and cancel work as today.

## Additions approved with it
1. The runner program is unchanged: the batch is a normal `run_script` job with a `batch` block; per-script results come from marker steps in the step list (read from the console output when Playwright wrote no report).
2. Deleting a script, test case or requirement is allowed (blocked only while a batch run that uses it is running). Its batch items disappear from the batch; old batch run results keep the script key as text.
3. At most 8 scripts per batch (the runner returns at most 8 screenshots, one per script). The test's own time limit is raised in the generated file; the runner's 10 minute cap still applies.
4. A script that is not in the shape TestMax generates (one `test(...)` at the end) is refused when added, with a message.

## How (technical)
The server joins the scripts' own text into one Playwright test file (each script in its own function so their helpers and data do not clash; one shared `page`; each script's data block filled with the chosen variation). The AI prompts, the script generator and the runner program are not changed.

## Not built
Prerequisite links between test cases; block-level test case generation (would need a second AI prompt, which needs separate approval); scheduling; running batches in parallel; passing values from one script to the next; resuming in a browser left open; bulk delete of batches.

## Verified by
New API tests; then a real run in Chrome with a scratch runner: a 3-script batch in one session (the later script depends on the browser state of the earlier one), stop and continue settings with a script that fails, the same script alone still working, and the screens looked at in the browser. Results: see `PROGRESS.md`.
