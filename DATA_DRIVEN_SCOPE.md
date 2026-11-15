# Data-driven testing: scope (approved by the user, 15 Nov 2026)

Goal: a script no longer carries its test data as fixed text. The test case holds data variations; each time a script runs it uses the values the test case holds at that moment.

Decisions made by the user: change the script generator's surroundings (not the AI prompts) = yes; a variation changes only the typed-in values (steps and expected results stay the same); no CSV/Excel import.

## How
- Each step with real test data gets a stable key (`username`, `password`, `destination_url`...). DV-1 is the test data written in the steps (locked). More variations live on the test case: a name and a value per key; an empty cell means "use DV-1".
- After the AI writes a script, a fixed rule (`app/engines/datadriven.py`) replaces each value typed into a step by `DATA["key"]` and adds a data block. Only a string equal to that step's own test data, inside that step, is replaced; anything else is left and reported.
- When a script is executed or downloaded, the server writes the chosen variation's current values into the data block. The runner is not changed (it still receives one script file), so old runners work.
- Editing only the test data that a data-driven script reads does not make it "Out of date". Any other edit still does.

## Screens
Test case: Key column, variation picker, "Add Data Variations" dialog. Script: "Data-driven" detail, "Make data-driven" for older scripts (no AI), notice for values the AI wrote as typed. Execute: tick the variations to run, one execution each. Report: the variation used and its values (secret-looking keys masked).

## Not built
Import of CSV/Excel; a separate expected result per variation; masking passwords in the test case screen; changing the AI prompts or the runner.
