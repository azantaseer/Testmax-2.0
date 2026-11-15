# UAT design audit and Pilot 2 redesign proposal

Source: `https://uat.testmax.ai` (organization "Mammoth-AI QA"), explored on 15 Nov 2026 at a 1440 x 900 window. Nothing was created, changed or deleted there. Screenshots: `reference/uat/*.jpg` (22). Pilot 2 "before": `reference/pilot2-before/*.jpg`. The UAT screenshots show the user's own UAT account and sample OrangeHRM test data only; do not publish them. Draft tokens: `reference/uat/tokens.css`.

## 1. What the UAT has (all pages found)
Shell: top bar (logo, breadcrumb with icons, user chip), left sidebar (organization switcher, "Workspaces +", All Project Dashboard, workspace tree with its projects, collapse handle).
- **All Project Dashboard** (organization): KPI cards (projects, requirements, test cases, scripts, executed), Execution Trends chart with range buttons, Coverage Pipeline (3 progress bars), project cards. (02)
- **Project pages**, one pill tab bar: Dashboard (03), Requirements (04, 05), Test Cases (06), Test Scripts (07), Test Batches (08, empty), Traceability (09), Knowledge Base (10).
- **Requirement detail** (11): back arrow + title, header buttons (Scan App, View Journey, Enhance with AI, Generate Test Cases, more), big Description card with Edit, right Details panel (quality score, suggestions, status, priority...).
- **Dialogs and menus:** Add Requirement split button (20), Create Requirement dialog with rich text editors (21), Filter popover with two panes (22), user menu (12), organization menu (14).
- **User settings** (13): "Account", underline tabs (Account, Notification Preferences), form, password card, Save/Cancel at the bottom right.
- **Organization pages** (from the organization menu): User Management (15; tabs Members, Join Requests, Invitations, Roles), Integrations, AI Administration (16; tabs Connection, Usage, Limits, Activity logs; spend, by user/process/model, daily trend, recent calls), Machine Execution (18; Download Agent, Add Machine), Fleet (17; KPI cards and a "Manage" card grid), Job Monitoring (19; KPI cards and a jobs table), Execution settings.
- Sign-in page (01) with cookie banner. Not opened: Integrations, Execution settings, Notification Preferences, Roles, workspace and project action menus, Scan App dialog, test case and script detail (the user's own earlier screenshots cover those: `reference/screenshots/`).

## 2. Measured design system (CSS values, not screenshot pixels)
**Fonts.** Inter for all text (variable, 400 to 700). Manrope 700 for the big dashboard titles. A monospace face for KPI numbers. `Sora` and `IBM Plex Sans Arabic` are declared but not used on the pages seen.
**Type scale.** 14/20 for table cells, buttons, tabs, menus (letter-spacing -0.15px); 12/16 bold for table headers (color `#6a7282`, letter-spacing -0.15px); 12/500 for pills and small buttons; 16/24 base; 20/600 page title on detail pages; 30/38 600 `#101828` page title on settings pages; 13.5 to 16 for card headings; 10/600 for tiny badges ("HELD BACK").
**Colors.** Brand green `#5ecf80` (hover darker), button text navy `#154260`, link blue `#155dfc`. Text `#0a0a0a`, secondary `rgba(10,10,10,.7)`, muted `#717182` / `#8a9098` / `#5a6068` / `#6a7282`, label `#344054`, heading `#0e1116`. Surfaces: page `#fff`, top bar and sidebar `#fafafa`, table header `#f9fafc`, search/input fill `#f3f3f5`, borders `rgba(0,0,0,.1)` (table rules `#ececef`). Tab bar fill `rgba(94,207,128,.2)`. Warning panel `#fefce8` with border `#fff085`. Avatar purple `#ad46ff`; chart series blue `#155dfc`, green `#00a63e`, purple `#9810fa`.
**Pills** (Tailwind 100 background / 800 text, 12/500, padding 4 x 12, full radius): Positive `#cbfbf1/#005f5a` (teal), Negative `#fae8ff/#8a0194` (fuchsia), Verification and Medium `#d0fae5/#006045` (emerald), High `#fef3c6/#973c00` (amber), scope End-to-End `#e0e7ff/#372aac` (indigo), neutral/held back `#e2e8f0/#314158` (slate), "Not Scanned"/"Scanned" `rgba(94,207,128,.05)` with a 1px ring `rgba(94,207,128,.25)` and navy text, failed status red.
**Shape.** Controls 8px; tab bar and user chip 16px; cards 12 to 16px; small buttons 6px; header buttons on detail pages 4px; shadow-xs `0 1px 2px rgba(16,24,40,.05)` for buttons/inputs; card shadow `0 8px 24px -12px rgba(15,20,30,.08), 0 1px 2px rgba(15,20,30,.04)`.
**Sizes.** Top bar 76px; sidebar 256px; compact button 32px, filter/search 36px, form input 46px (16px text, padding 10 x 14), tab 30px inside a tab bar with 8px padding; table header 40px, row 41px (cell padding 0 12px); modal widths 500 to 1000px; pager buttons 38px, active page 32px green square.
**Motion.** `.15s` transitions; skeleton loaders (grey bars) while data loads.

## 3. What differs in Pilot 2 today (measured on the same window)
| Area | UAT | Pilot 2 |
|---|---|---|
| Fonts | Inter + Manrope headings + mono numbers | Inter only (Manrope and Sora are bundled but unused) |
| Line heights | explicit 20/24 | `normal` |
| Table header | 12 bold `#6a7282`, 40px | 12/500 black, 45.6px |
| Table row | 41px, cell py 7px | 44px, py 10px |
| Primary button | 32 to 36px, padding 0 10 to 14 | 40px, padding 0 16 |
| Tab pill | 30px, padding 0 12 | 32px, padding 0 14 |
| Page frame | breadcrumb inside the top bar with icons; tab bar and the primary action on one row; search + Filter below; pager pinned to the bottom | breadcrumb in the page; description and a "Requirements" heading above the table; stretched wide Previous bar |
| Pills | Tailwind 100/800 palette (teal, fuchsia, emerald, amber, indigo) | own greens/pinks/purples, different tints |
| Loading | skeleton rows | "Loading..." text |
| Empty states | icon in a green circle, title, one line, CTA | table row with a sentence |
| Filter | two-pane popover with search, checkboxes, Clear all, Apply | none (search only) |
| Dialogs | scrolling body, sticky footer, 500 to 1000px | scrolling modal, no sticky footer (except Upload Document) |
| Forms | 46px inputs, label 14/500 `#344054`, red asterisk, xs shadow | 40px inputs |
| Detail pages | card + side Details panel, header buttons 4px radius | similar structure, different sizes |
| Admin pages | Users with pill tabs, AI usage cards, machine/fleet/job KPI cards | simple tables |

## 4. Proposal (needs your approval; nothing below is built yet)
Principle: copy the UAT's look and arrangement for the screens Pilot 2 already has; do not add pages or features Pilot 2 lacks (HANDOFF scope). Keep plain HTML/CSS/JS, no framework, bundled fonts only.
1. **Tokens (small, safe):** replace the ad hoc values by the measured ones in one CSS file (`tokens.css`): colors, type scale with line heights, radii, shadows, control heights, pill palette. Use Manrope for page titles. Re-check every page at 1440 and 1024.
2. **Shell:** top bar 76px with the breadcrumb and icons inside it, user chip as in the UAT, sidebar 256px with the same item style, collapse handle. Pilot 2's tree is Projects, Users, Runners (no workspaces).
3. **List pages (Requirements, Test Cases, Test Scripts, Users, Runners):** one table component: 40/41px rows, bold grey 12px headers, new pill palette, tab bar + action on one row, search + Filter row, pager pinned to the bottom with "Rows per page", skeleton loading, UAT-style empty states. A Filter popover (two panes) on the three list pages using fields we really have (type, requirement, status, created).
4. **Detail pages (requirement, test case, script, execution report):** header, buttons, cards and the right Details panel in UAT sizes; collapsible description card.
5. **Forms and dialogs:** 46px inputs, label style, sticky footer, rich-looking headers; Settings pages in the "Account" style with underline tabs.
6. **Admin pages:** Users as User Management (pill tabs, same table); Runners restyled like Machine Execution (empty state, "Download Agent" = our Connect a Runner), the limit card kept; AI settings in the UAT card style. The Fleet/Job Monitoring KPI cards could reuse data Pilot 2 already has (online runners, running jobs, queue) only if you ask for them.
7. **Improvements beyond the UAT (my suggestions, each optional):** visible keyboard focus rings and contrast checks (WCAG AA), a working narrow-window layout (sidebar collapses to an icon rail), remember the rows-per-page and sort per list, sticky table headers, consistent success/error toasts, a single "unsaved changes" guard on every edit form, and print-safe test case pages.

**Out of scope unless you approve:** Dashboard, Test Batches, Traceability, Knowledge Base, Fleet, Job Monitoring, Integrations, workspaces, rich text editors, score/quality analysis.

**How I would work:** tokens first (one commit-sized change, shows up everywhere), then the shell, then each page group with before/after screenshots at 1440 and 1024, the full test suite after every step, and a stop for your look after tokens + shell + one list page before I continue.
