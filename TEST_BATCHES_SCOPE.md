# Test Batches: scope (for approval)

Goal: run several test scripts in a chosen order in ONE browser session, so a later script (Search) starts where an earlier one (Register) left off, already logged in. Each script still runs alone exactly as today; the person provides the prerequisites for a single run.

## What is built
- A **Test Batches** tab in every project (list, create, edit, delete). A batch has a name, an optional description, a setting **When a script fails: stop the batch / continue with the next script** (chosen per batch; default stop), and an ordered list of scripts. Each script in the list can use one of its data variations (default DV-1). The same script may appear twice.
- **Run batch** opens one browser, runs the scripts in order in that same page, and reports one result per script (passed, failed, or not run because an earlier script failed and the batch stops). Each script's result is also stored as an ordinary execution of that script, so its own run history shows it.
- A batch run page: the order, each script's result and duration, link to each script's full report (steps, screenshots), the data variation used, and the overall status. A batch's page lists its last runs.
- Runs on the runner chosen in the Execute dialog (as for single scripts). Job limit and cancel work as today.

## How (technical)
The server joins the scripts' own text into one Playwright test file (each script in its own block so their helpers and data do not clash; one shared `page`; each script's data block filled with the chosen variation). The AI prompts, the script generator and the runner program are not changed. Step names carry the script key so the report can be split per script.

## Not built
Prerequisite links between test cases; block-level test case generation (would need a second AI prompt, which needs separate approval); scheduling; running batches in parallel; passing values from one script to the next; resuming in a browser left open.

## Verified by
New API tests; then a real run in Chrome with a scratch runner: a 3-script batch in one session (the later script depends on the browser state of the earlier one), stop and continue settings with a script that fails, the same script alone still working, and the screens looked at in the browser.
