# Grouping requirements: source document and tags (scope; the user delegated the design and said to start with Requirements)

Goal: in one project, see which BRD a requirement came from and group related requirements. Test cases and test scripts get the same later (they inherit through their requirement); this task is the Requirements list and page only.

## What is built
- **Source document:** every requirement already remembers the document it was generated from. New: a Source column (document name), a Source filter with counts, and clicking a source in a row filters by it.
- **Tags:** free-form labels on a requirement (up to 10, 30 characters each, spaces allowed; an existing spelling is reused, so "login" and "Login" never become two tags). A Tags column with chips, a Tags filter with counts (a requirement matches if it has any chosen tag), clicking a tag chip filters by it.
- **Bulk tagging:** select rows, then Add tag / Remove tag in the selection bar. The requirement page gets a Tags row in its Details card where one requirement's tags can be edited.
- **Active filters:** the applied filters show as removable chips under the search box.
- **API (additive):** list takes `document` and `tag` (repeatable), returns `documents` and `tags` with counts; `POST .../requirements/tags` adds/removes tags on 1 to 200 requirements. Tags are stored on the requirement (migration 0013). Tagging does not change the requirement's version, scans, test cases or scripts.

## Not built (later, on request)
Source and tag filters on Test Cases and Test Scripts; tags on test cases that differ from their requirement; folders; running a whole tag; renaming or deleting a tag everywhere in one click.
