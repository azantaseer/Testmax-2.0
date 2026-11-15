# Screenshot notes

Observations from the reference screenshots, for the look-alike UI. The screenshots may show the user's real login/email: use placeholder data in Pilot 2, never copy it.

## 01-login.png — Sign In (`app.testmax.ai/authentication?view=sign-in`)
Prod source: `mammoth-ai/app/authentication/page.tsx`, `app/_components/AuthSplitLayout`, `AuthViewTabs`, `lib/auth-onboarding-ui`.

- **Layout:** full-height white page, two columns (split layout). Left column about 55% width, right column about 45%. Content vertically centred. No header/nav bar. Footer centred at the bottom: "Terms & Conditions · Privacy Policy" (small, grey).
- **Left (brand panel), left-aligned text block:**
  - TestMax logo (navy "tmx" mark with green x, "TESTMAX" letter-spaced underneath), about 120 px wide.
  - H1-style line "Revenue-Critical AI for Modern QA Teams" (about 28 px, medium weight, near-black).
  - Sub-line "Transform your business with AI agents tailored to your industry's unique challenges." (about 20 px, near-black, relaxed line height).
  - Paragraph "Turn unstructured requirements into production-ready test cases, scripts, and reports — faster, smarter, and without ambiguity." (about 20 px, muted grey #717182-like).
- **Right (form panel):**
  - Tab switcher pill on top, full card width: "Sign In" | "Sign Up". Track is very light green tint with rounded corners; the active tab is a white rounded segment, text bold-ish black, about 14 px. Sign Up is the inactive tab.
  - Below, a card with a light green 2 px border and large radius (about 20-24 px), white background, generous padding (about 48 px).
  - Card title "Sign In" (about 32 px, medium), subtitle "Enter your credentials here to access your account." (about 13 px, near-black).
  - Field "Email *" (label about 14 px, red asterisk), input about 48 px high, 1 px light grey border, about 8 px radius, white fill, about 16 px text.
  - Field "Password *" with placeholder "Enter your password" and an eye-off toggle icon inside the right end.
  - Row: checkbox "Remember Me" (green checked, default checked, small text) on the left; "Forgot Password?" link in primary green on the right (small).
  - Primary button "Sign In": full width, about 48 px high, primary green #5ECF80 fill, about 8 px radius, dark navy text, about 20 px, medium weight.
- **Behaviour seen in source:** email remembered from last sign-in; validation errors appear as a floating tooltip above the field (red "!" badge, white card with shadow), not shifting layout. Social login and email-verification code steps exist in prod but are out of scope for Pilot 2.
- **Pilot 2 scope for this screen:** Sign In tab only (Sign Up tab omitted or disabled since companies/users are seeded), email + password, Remember Me, Sign In button. Forgot Password and Sign Up: not functional (approval needed to show them as dead links).

## 02-workspace-projects.png — Workspace project list (after clicking a workspace in the left menu)
Page title is "<Workspace>'s Project" (here "Demo's Project"). Shows the app shell used by every later screen.

- **Top bar (full width, height ~78 px, page-grey #FAFAFA, 1 px bottom border, thin pink/red line at very top edge):**
  - Left: TestMax logo (navy tmx mark, "TESTMAX" under it), about 75 px wide.
  - Right: user chip in a rounded bordered pill: green circle avatar with initials ("AT"), name (medium, ~14 px), email below (small, muted), chevron-down. Opens a user menu (not shown).
- **Left sidebar (~255 px wide, #FAFAFA, 1 px right border, collapse tab = small white rounded handle with "<" on the right edge at mid-height):**
  - Organization switcher: rounded grey box with org name ("Mammoth-AI", bold ~14 px) and ">" chevron.
  - Divider, then "Workspaces" heading (muted ~15 px) with "+" at right (add workspace).
  - "All Project Dashboard" item with grid icon (dashboard is out of scope for Pilot 2, see below).
  - Divider, then workspace tree: a workspace row with chevron (expanded), doc icon and name ("Demo"; the row is in a light grey rounded block, text currently shown in an outlined/rename-style box, probably focus or edit state). Nested under it, with a thin vertical guide line, each project as a row: doc icon + name ("Web App Project", muted).
- **Main area (white), padding about 24 px:**
  - Title "Demo's Project" (~22 px, semibold, near-black) and subtitle "View and manage all projects in your organization" (~16 px, muted grey).
  - Top right: primary button "+ Create New Project" (green #5ECF80 fill, dark text, ~14 px, radius ~8 px, plus icon).
  - Search box under the title: ~680 px wide, 36 px high, light grey fill #F3F3F5, no border, magnifier icon, placeholder "Search by title, description, keywords, tags…".
  - Table: header row with light #F9FAFC background; columns: checkbox | Project Name | Description | Assigned Users | Last Updated (with sort chevron). Column dividers are thin vertical lines in the header. Header text small (~12 px), medium weight, near-black. Data row: checkbox, "Web App Project", "—" for empty description, "—" for no assigned users, date "Nov 10, 2026". Row height ~40 px, 1 px bottom border. Horizontal scrollbar bar visible at table bottom (grey track).
  - Footer pagination: "Previous" button (disabled, outlined, light), current page chip "1" (green fill, white text, rounded square), "Rows per page:" label with select showing "10", "Next" button (disabled, outlined).
- **Pilot 2 notes:**
  - Organization = company in Pilot 2. Keep the org name in the switcher (read-only, no switching). Prod has Org > Workspace > Project; HANDOFF scope says project screen only, so propose: keep Workspaces tree in the sidebar with seeded workspaces, no workspace create/edit. Needs approval (Workspace is not in the HANDOFF screen list). Alternative: sidebar lists projects only.
  - "All Project Dashboard" out of scope (dashboards): omit.
  - Assigned Users column: show but empty/seeded (RBAC is out of scope).
  - Create New Project: needs a small modal (not yet seen, awaiting screenshot).
  - User menu (avatar chevron) contents not seen yet; logout needed.
  - Screenshot shows the user's real name and email: use placeholder names in Pilot 2.

## Screenshots 03-10 (sent in chat on 14 Nov 2026; the image files were NOT saved to disk, only described here)
Seen: requirements list, requirement detail (two scroll positions), test cases list, test case detail (two scroll positions), test scripts list, script viewer (two), test batches empty state. Prod header is now a breadcrumb row: "Demo / Web App Project / Requirements / REQ-1032", with a rose banner at the very top when the AI key is rejected ("The connected OpenAI API key was rejected. Open Connection ...").
- **Project tabs (pill bar, light green track, white active pill):** Dashboard | Requirements | Test Cases | Test Scripts | Test Batches | Traceability | Knowledge Base. Pilot 2 needs Requirements, Test Cases, Test Scripts (+ Execute) only.
- **Requirements list:** green split button "Add Requirement", search box with a "+" circle, green "Filter" button, columns: checkbox, Ticket ID (blue link, sortable), Title, Priority (green "Medium" pill), Status ("OPEN" pill), Tags ("FUNCTIONAL"/"END-TO-END" blue pills), Scanned ("Scanned" green / "Not Scanned" grey pill), Score (mini bar + %), Created At, Updated At, Assigned To, Test Cases (dark navy count badge). Pagination as before.
- **Requirement detail:** header buttons (outlined): "Scan App", "View Journey", "Enhance with AI", disabled "Generate Test Cases", kebab menu. Back arrow + big title in a grey bar. Left card: DESCRIPTION (collapsible, "Edit" button) rendered as formatted headings and bullets (Overview, Pre-requisites, Business rules, Schema & fields, BRD & intent, Flow numbered), then ACCEPTANCE CRITERIA card. Right "Details" panel: Requirement Quality (score, clarity, testability, completeness, suggestions), Details (Status, Assigned to, Author, Priority, Project). Pilot 2 keeps: Scan App button, description and acceptance criteria rendered as markdown-like text, a simple details panel; quality scores are out of scope.
- **Test cases list:** green split "Create Test Case", columns: Ticket ID (REQ link), Test Cases (TC-3729 link), Title, Scope (Standard/End-to-End pill), Type (Positive green / Negative pink / Edge purple pill), Priority (High yellow pill), Assigned To (avatar + name), Script ID (TS link), Execution Status ("Aborted" pill), Execution Time, Created Date, Last Updated.
- **Test case detail:** title bar with Scope/Type pills at right, "Generate Test Script" green button + red trash button. Left: TEST CASE DESCRIPTION card (Overview, Prerequisites, Test Objective, Expected Result), TEST STEPS card with a "Technical columns" checkbox and table ID | Test Step | Field Name | Test Data | Expected Result and a pencil per row. Right "Details": Test Script, Requirement (REQ link), Priority, Assigned to, Created, Author, Updated.
- **Test scripts list:** green "Generate Scripts", columns Script ID, Title, Priority, Language ("TypeScript" outlined pill), Test Cases, Execution ID, Execution Status (red "Aborted" pill), Execution time, Created Date, Last Updated, "View Report" button.
- **Script viewer:** green "Execute" button + kebab; dark code editor card (file name chip "script.spec.ts", "Playwright - TS" blue chip, "Last edited", Find / Copy / Edit buttons, line numbers) and a "Details" panel (Framework, Language, Version, Assigned, Author with "AI" chip, Created, Project, Test case) plus "Run history" (total, pass rate bar, avg time). Floating round AI-assistant button bottom right (out of scope).
- **Test batches:** empty state (green circle icon, "No test batches created yet", Create Batch). Out of scope for Pilot 2.
- **Pilot 2 differences to fix later (UI polish task):** add breadcrumb + project tab bar, move Scan App to the requirement detail header, add Scanned column, render description as formatted sections.

## Screenshots saved 14 Nov 2026 (image files now on disk): Test Case 1-3.png, Test Scripts 1-3.png
They show the user's real name and email: use placeholder data in Pilot 2. NOT provided: Generate Test Cases dialog/progress, Generate Test Script / Generate Scripts dialog/progress, Execute progress modal, execution report, View Report screen. Those screens are **not verified against prod** (designed from Pilot 1 behaviour and the prod tokens).
- **Test Case 1.png (list):** breadcrumb `Demo / Web App Project / Test Cases`, project pill tab bar (Test Cases active), green split button "Create Test Case", search box with "+" circle, green "Filter" button. Columns: checkbox, Ticket ID (REQ link), Test Cases (TC id link, sortable), Title (truncated with "..."), Scope (Standard / End-to-End blue pill), Type (Positive green, Negative pink, Edge purple pill), Priority (High, yellow pill), Assigned To (avatar + name), Script ID (TS link, "—" when none), Execution Status ("Aborted" grey pill), Execution Time ("Duration: 48.97s"), Created Date, Last Updated. Horizontal scrollbar, pagination as on the other lists.
- **Test Case 2.png (detail, top):** breadcrumb ends `Test Cases / TC-3730`. Green "Generate Test Script" button (sparkle icon) and a red outlined trash button top right. Back arrow, large title, Scope and Type pills at the right of the title row. Left card "TEST CASE DESCRIPTION *" with an Edit button and sections Overview, Prerequisites, Test Objective, Expected Result (bold headings, plain text). Second card "TEST STEPS *" with a "Technical columns" checkbox; table columns ID | Test Step | Field Name | Test Data (sort chevron) | Expected Result, a kebab in the header, a pencil at each row end; blank cells show "—"; a navigation URL shows as a "View URL" link. Right "Details" panel (collapse ">>"): Test Script (— or link), Requirement (REQ id, mono, blue), Priority (select with yellow pill), Assigned to (avatar select), Created, Author, Updated.
- **Test Case 3.png (detail, scrolled):** long expected results wrap onto several lines in their column; horizontal scrollbar under the steps table; header (tabs, title, buttons) stays fixed while the body scrolls.
- **Test Scripts 1.png (list):** green "Generate Scripts" (sparkle) top right. Columns: checkbox, Script ID (TS link, sortable), Title, Priority, Language ("TypeScript" blue outlined pill), Test Cases (TC link), Execution ID (EX link), Execution Status (red "Aborted" pill), Execution time, Created Date, Last Updated, "View Report" button column.
- **Test Scripts 2.png and 3.png (viewer):** breadcrumb ends `Test Scripts / TS-3729`. Green "Execute" (play icon) + kebab. Dark code card: file chip `script.spec.ts`, blue chip "Playwright - TS", "Last edited 4 days ago", buttons Find / Copy / Edit; line numbers, syntax colours, minimap. Right "Details": Framework, Language (select), Version, Assigned, Author (with a green "AI" chip), Created, Project, Test case (link). Below it "Run history": TOTAL, PASS RATE, AVG TIME, a green/grey bar, "Last run recorded 4 days ago", "Individual". Prod scripts are TypeScript and call a helper (`getTestData`); Pilot scripts are JavaScript with the scan's locators (the prompt is unchanged).
