import { del, get, patch, post } from "../api.js";
import { confirmDialog, errorText, modal, pagination, toast } from "../components.js";
import { h, icon } from "../dom.js";
import { rowSelection } from "../selection.js";
import { remember, savedRows, savedSort, emptyState, filterPopover, listToolbar, searchBox, skeletonTable, tableBlock } from "../table.js";
import { addGuard, navigate, PROVIDER_LABEL, state } from "../store.js";
import { formatDate, formatDay, projectFrame, typePill } from "./projectFrame.js";
import { jobTracker } from "./jobs.js";
import { scriptBatch } from "./testScripts.js";
import { dataSetsDialog, valueFor, variationPicker } from "./dataSets.js";

// The data Key column in the Test Steps table is hidden on request; set to true to show it again.
const SHOW_KEY_COLUMN = false;

const SOURCE_LABEL = { system: "system default", tenant: "your organization's choice", user: "your own setting" };

export function scriptLink(project, script) {
  if (!script) return h("span", { class: "muted-text" }, "—");
  const link = h("a", { class: "row-link cell-key", href: `/projects/${project.id}/test-scripts/${script.id}`, "data-link": "" }, script.key);
  return script.stale ? h("span", { class: "stale-link" }, link, h("span", { class: "tpill stale", title: "The test case was edited after this script was written" }, "Out of date")) : link;
}

/** "http(s) address" test data is shown as a link (like prod's "View URL"); everything else as text. */
function testDataCell(value) {
  const text = String(value ?? "");
  if (!text || text === "—") return h("span", { class: "dash" }, "—");
  if (/^https?:\/\/\S+$/i.test(text)) return h("a", { class: "row-link", href: text, target: "_blank", rel: "noopener noreferrer", title: text }, "View URL");
  return text;
}

function dashed(value) {
  const text = String(value ?? "").trim();
  return text && text !== "—" ? text : h("span", { class: "dash" }, "—");
}

// ---------------------------------------------------------------- Generate Test Cases dialog (from a requirement)
export async function generateCasesModal(project, requirement, onStarted) {
  let plan;
  try {
    plan = await get(`/api/projects/${project.id}/requirements/${requirement.id}/test-cases/plan`);
  } catch (err) {
    toast(errorText(err), "error");
    return;
  }
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const submit = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Generate Test Cases");
  const scan = plan.scan;
  const blocked = !scan ? "This requirement has no saved App Scan yet. Run Scan App first." : !plan.model_chosen ? "No AI model is selected yet. Choose one under Organization AI Model (Super Admin) or My Settings." : plan.running_job ? "Test cases for this requirement are already being generated." : null;
  if (blocked) submit.disabled = true;
  const form = h(
    "form",
    { class: "form-stack tight", novalidate: true },
    h("p", { class: "modal-text", style: "margin:0" }, "The AI writes test cases from this requirement and the App Map of its latest scan: positive, negative, verification, boundary and edge cases, each with numbered steps."),
    h(
      "dl",
      { class: "meta-grid", style: "margin:0" },
      h("dt", {}, "Requirement"),
      h("dd", {}, requirement.key),
      h("dt", {}, "Scan used"),
      h("dd", {}, scan ? `${formatDate(scan.created_at)} · ${scan.status === "COMPLETE" ? "Complete" : "Partial"} · ${scan.application_url}` : "none"),
      h("dt", {}, "AI model"),
      h("dd", {}, plan.model_chosen ? `${PROVIDER_LABEL[plan.provider] || plan.provider} · ${plan.model} (${SOURCE_LABEL[state.me.ai.source] || ""})` : "none selected"),
    ),
    scan && scan.status === "PARTIAL" ? h("div", { class: "notice" }, "This scan is partial: some pages or controls were not reached, so some cases may be listed as not generated.") : null,
    blocked ? h("div", { class: "banner-error" }, blocked) : null,
    banner,
    h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), submit),
  );
  const m = modal({ title: "Generate Test Cases", content: form, width: 600 });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    banner.hidden = true;
    submit.disabled = true;
    submit.textContent = "Starting…";
    try {
      const job = await post(`/api/projects/${project.id}/requirements/${requirement.id}/test-cases/generate`);
      m.close();
      onStarted({ ...job, requirement_id: requirement.id });
    } catch (err) {
      submit.disabled = false;
      submit.textContent = "Generate Test Cases";
      banner.textContent = errorText(err);
      banner.hidden = false;
    }
  });
}

// ---------------------------------------------------------------- panel on the requirement page
export function testCasesPanel(project, requirement) {
  const listHost = h("div", { class: "table-host" });
  const tracker = jobTracker({
    titles: { running: "Writing test cases…", done: "Test cases generated", failed: "Test case generation failed" },
    label: () => requirement.key,
    doneText: (job) => `${job.result?.test_case_count ?? 0} test cases saved.`,
    onFinished: (job) => {
      if (job.status === "succeeded") toast(`Generated ${job.result.test_case_count} test cases.`);
      loadSets();
    },
  });

  async function loadSets() {
    try {
      const { sets } = await get(`/api/projects/${project.id}/test-case-sets?requirement_id=${requirement.id}`);
      listHost.replaceChildren(
        sets.length
          ? h(
              "div",
              { class: "table-wrap" },
              h(
                "table",
                { class: "data-table" },
                h("thead", {}, h("tr", {}, ...["Generated", "Test cases", "Types", "Model", "Duration", ""].map((t) => h("th", { scope: "col" }, t)))),
                h(
                  "tbody",
                  {},
                  sets.map((s) => {
                    const counts = Object.entries(s.coverage?.type_counts || {}).filter(([, n]) => n > 0);
                    return h(
                      "tr",
                      {},
                      h("td", {}, formatDate(s.created_at)),
                      h("td", {}, String(s.test_case_count), s.not_generated?.length ? h("span", { class: "muted-text small" }, ` (+${s.not_generated.length} not generated)`) : null),
                      h("td", { class: "small" }, counts.map(([k, n]) => `${n} ${k.toLowerCase()}`).join(", ") || "—"),
                      h("td", {}, `${PROVIDER_LABEL[s.provider] || s.provider} · ${s.model}`),
                      h("td", {}, s.duration_seconds != null ? `${s.duration_seconds}s` : "—"),
                      h(
                        "td",
                        { class: "cell-actions" },
                        h("div", { class: "row-actions" },
                          h("a", { class: "btn btn-secondary btn-sm", href: `/projects/${project.id}/test-cases?requirement=${requirement.id}`, "data-link": "" }, "View test cases"),
                          h("a", { class: "btn btn-secondary btn-sm", href: `/api/projects/${project.id}/test-case-sets/${s.id}/docx`, download: "" }, "Word"),
                        ),
                      ),
                    );
                  }),
                ),
              ),
            )
          : h("div", { class: "muted-text", style: "font-size:14px" }, requirement.scan ? "No test cases yet. Use Generate Test Cases." : "Scan the app first, then generate test cases from the scan."),
      );
    } catch (err) {
      listHost.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load test cases.")));
    }
  }

  const root = h("div", {}, h("h2", { class: "section-heading" }, "Test Cases"), tracker.el, h("div", { style: "margin-top:12px" }, listHost));
  loadSets();
  tracker.resume(project.id, "generate_test_cases", (j) => j.requirement_id === requirement.id);
  return { el: root, follow: tracker.follow };
}

// ---------------------------------------------------------------- list
export function testCasesView(projectId) {
  const root = h("div", { class: "fill" }, h("div", {}, "Loading…"));
  (async () => {
    let project;
    try {
      project = await get(`/api/projects/${projectId}`);
    } catch (err) {
      root.replaceChildren(h("div", { class: "banner-error" }, err.status === 404 ? "This project was not found." : errorText(err)));
      return;
    }
    document.title = `Test Cases — ${project.name} — TestMax`;
    const params = new URLSearchParams(location.search);
    const initialRequirement = params.get("requirement") || "";
    const q = { page: 1, pageSize: savedRows(10), search: "", ...savedSort("cases", { sort: "key", dir: "asc" }, ["key", "title", "type", "created"]), filters: initialRequirement ? { requirement: [initialRequirement] } : {} };
    const HEADERS = ["", "Requirement", "Test Case", "Title", "Type", "Steps", "Script", "Created"];
    const tableHost = h("div", { class: "table-host fill" });
    const selection = rowSelection({
      url: `/api/projects/${project.id}/test-cases/delete`,
      singular: "test case",
      plural: "test cases",
      consequence: ["Its test script and executions are deleted with it.", "Their test scripts and executions are deleted with them."],
      onDone: () => load(),
    });
    const search = searchBox({
      placeholder: "Search by title, description, type…",
      label: "Search test cases",
      onSearch: (text) => {
        selection.clear(); // a changed search must not leave hidden rows selected
        q.search = text;
        q.page = 1;
        load();
      },
    });
    const filter = filterPopover({
      fields: [
        { key: "requirement", label: "Requirement", type: "options", options: [] },
        { key: "type", label: "Type", type: "options", options: ["POSITIVE", "NEGATIVE", "VERIFICATION", "BOUNDARY", "EDGE"].map((t) => ({ value: t, label: t.charAt(0) + t.slice(1).toLowerCase() })) },
        { key: "script", label: "Script", type: "options", options: [{ value: "with_script", label: "Has a script" }, { value: "without_script", label: "No script yet" }, { value: "out_of_date", label: "Script out of date" }] },
        { key: "created", label: "Created", type: "date" },
      ],
      value: q.filters,
      onApply: (value) => {
        selection.clear();
        q.filters = value;
        q.page = 1;
        if (!value.requirement) history.replaceState({}, "", location.pathname);
        load();
      },
    });
    // the Requirement choices: the project's requirements (the first 100)
    get(`/api/projects/${project.id}/requirements?page_size=100`)
      .then((d) => filter.setOptions("requirement", d.requirements.map((r) => ({ value: r.id, label: `${r.key} — ${r.summary}` }))))
      .catch(() => {});

    function sortHead(label, key) {
      const active = q.sort === key;
      return h("th", { scope: "col", "aria-sort": active ? (q.dir === "asc" ? "ascending" : "descending") : "none" }, h("button", { class: "sort-btn", type: "button", onclick: () => { q.dir = active && q.dir === "asc" ? "desc" : "asc"; q.sort = key; remember("sort.cases", { sort: q.sort, dir: q.dir }); load(); } }, label, h("span", { class: "sort-arrow" }, active ? (q.dir === "asc" ? "▲" : "▼") : "")));
    }

    function queryString() {
      const qs = new URLSearchParams({ page: q.page, page_size: q.pageSize, q: q.search, sort: q.sort, direction: q.dir });
      const f = q.filters;
      (f.requirement || []).forEach((v) => qs.append("requirement_id", v));
      (f.type || []).forEach((v) => qs.append("type", v));
      (f.script || []).forEach((v) => qs.append("script", v));
      if (f.created?.from) qs.set("created_from", f.created.from);
      if (f.created?.to) qs.set("created_to", f.created.to);
      return qs;
    }

    function clearAll() {
      q.search = "";
      q.filters = {};
      q.page = 1;
      search.input.value = "";
      filter.setValue({});
      selection.clear();
      history.replaceState({}, "", location.pathname);
      load();
    }

    async function load() {
      try {
        const data = await get(`/api/projects/${project.id}/test-cases?${queryString()}`);
        if (!data.test_cases.length && data.total > 0 && q.page > 1) {
          q.page = Math.ceil(data.total / q.pageSize); // the last page was emptied by a delete
          return load();
        }
        selection.begin();
        if (!data.test_cases.length) {
          const narrowed = !!(q.search.trim() || filter.count());
          tableHost.replaceChildren(
            narrowed
              ? emptyState({ title: "No test cases match", text: "Try a different search or clear the filters.", actions: [h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: clearAll }, "Clear search and filters")] })
              : emptyState({
                  title: "No test cases yet",
                  text: "Scan a requirement, then choose Generate Test Cases on its page.",
                  actions: [h("a", { class: "btn btn-primary btn-md", href: `/projects/${project.id}`, "data-link": "" }, "Go to requirements")],
                }),
          );
          selection.done();
          return;
        }
        const rows = data.test_cases.map((c) =>
          h(
            "tr",
            {},
            selection.box(c.id, c.key),
            h("td", { class: "cell-key" }, c.requirement_key || "—"),
            h("td", { class: "cell-key" }, h("a", { class: "id-link", href: `/projects/${project.id}/test-cases/${c.id}`, "data-link": "" }, c.key)),
            h("td", { class: "cell-strong cell-title" }, h("a", { class: "row-link", href: `/projects/${project.id}/test-cases/${c.id}`, "data-link": "", title: c.title }, c.title)),
            h("td", {}, typePill(c.type)),
            h("td", {}, String(c.step_count)),
            h("td", {}, scriptLink(project, c.script)),
            h("td", {}, formatDay(c.created_at)),
          ),
        );
        tableHost.replaceChildren(
          tableBlock(h("tr", {}, selection.head(), h("th", { scope: "col" }, "Requirement"), sortHead("Test Case", "key"), sortHead("Title", "title"), sortHead("Type", "type"), h("th", { scope: "col" }, "Steps"), h("th", { scope: "col" }, "Script"), sortHead("Created", "created")), rows),
          pagination({ page: data.page, pageSize: data.page_size, total: data.total, onPage: (p) => { q.page = p; load(); }, onPageSize: (n) => { q.pageSize = n; q.page = 1; load(); } }),
        );
        selection.done();
      } catch (err) {
        tableHost.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load test cases.")));
      }
    }
    const content = h("div", { class: "fill" }, listToolbar(search.el, filter.el), selection.bar, tableHost);
    root.replaceChildren(projectFrame(project, "test-cases", content));
    tableHost.replaceChildren(skeletonTable(HEADERS));
    load();
  })();
  return root;
}

// ---------------------------------------------------------------- detail
const CASE_TYPES = ["POSITIVE", "NEGATIVE", "VERIFICATION", "BOUNDARY", "EDGE"];
const TYPE_LABEL = { POSITIVE: "Positive", NEGATIVE: "Negative", VERIFICATION: "Verification", BOUNDARY: "Boundary", EDGE: "Edge" };
const blank = (v) => (v == null || v === "—" ? "" : String(v));

export function testCaseDetailView(projectId, caseId) {
  const root = h("div", {}, h("div", {}, "Loading…"));
  let editing = false; // while a person is editing, a refresh from a finished script job must not throw the typing away
  let focusStep = null;
  let warnings = [];

  async function load() {
    let project;
    let c;
    try {
      [project, c] = await Promise.all([get(`/api/projects/${projectId}`), get(`/api/projects/${projectId}/test-cases/${caseId}`)]);
    } catch (err) {
      root.replaceChildren(h("a", { class: "back-link", href: `/projects/${projectId}/test-cases`, "data-link": "" }, "← Test cases"), h("div", { class: "banner-error", style: "margin-top:16px" }, err.status === 404 ? "This test case was not found." : errorText(err)));
      return;
    }
    document.title = `${c.key} — TestMax`;
    const batch = scriptBatch(project, () => { if (!editing) load(); });
    const set = c.set;

    // ------------------------------------------------------------ read-only page
    function readView() {
      const technical = h("input", { class: "cb", type: "checkbox", id: "tech" });
      const stepsBody = h("tbody");
      const stepsHead = h("thead");
      const dataInfo = c.data || { keys: [], default: {}, sets: [] };
      const hasKeys = dataInfo.keys.length > 0;
      let shown = 0; // which data variation the Test Data column shows (0 = DV-1)
      function drawSteps() {
        const tech = technical.checked;
        stepsBody.replaceChildren(
          ...c.steps.map((s) =>
            h(
              "tr",
              {},
              h("td", { class: "num" }, String(s.stepNo)),
              h("td", {}, s.testStep),
              h("td", {}, dashed(s.fieldName)),
              SHOW_KEY_COLUMN && hasKeys ? h("td", {}, s.dataKey ? h("code", { class: "key-chip" }, s.dataKey) : h("span", { class: "dash" }, "\u2014")) : null,
              h("td", {}, s.dataKey ? testDataCell(valueFor(dataInfo, shown, s.dataKey)) : testDataCell(s.testData)),
              h("td", { class: "sec" }, dashed(s.expectedResult)),
              tech ? h("td", { class: "tech" }, s.sourceElementId || "—") : null,
              h("td", { class: "row-edit" }, h("button", { class: "icon-btn", type: "button", "aria-label": `Edit step ${s.stepNo}`, title: "Edit this step", onclick: () => { focusStep = s.stepNo; editing = true; render(); } }, icon("pencil"))),
            ),
          ),
        );
        const dataHead = dataInfo.sets.length
          ? h("th", { scope: "col", class: "c-data" }, variationPicker(dataInfo, shown, (i) => { shown = i; drawSteps(); }))
          : h("th", { scope: "col", class: "c-data" }, "Test Data");
        stepsHead.replaceChildren(h("tr", {}, h("th", { scope: "col", class: "c-id" }, "ID"), h("th", { scope: "col", class: "c-step" }, "Test Step"), h("th", { scope: "col", class: "c-field" }, "Field Name"), SHOW_KEY_COLUMN && hasKeys ? h("th", { scope: "col", class: "c-key" }, "Key") : null, dataHead, h("th", { scope: "col", class: "c-expected" }, "Expected Result"), tech ? h("th", { scope: "col", class: "c-tech" }, "Source element") : null, h("th", { scope: "col", class: "row-edit" }, h("span", { class: "sr-only" }, "Edit"))));
      }
      technical.addEventListener("change", drawSteps);
      drawSteps();

      const topNotes = [];
      const notes = [];
      if (c.script?.stale) topNotes.push(h("div", { class: "notice stale-note" }, h("span", {}, h("strong", {}, "The test script is out of date. "), `This test case was edited after ${c.script.key} was written, so the script may no longer match it. Regenerate the script to use the edited steps.`)));
      if (warnings.length) topNotes.push(h("div", { class: "flagged" }, h("strong", {}, "Saved, with advice about the steps"), h("ul", {}, warnings.map((w) => h("li", {}, w)))));
      if (set?.warnings?.length) notes.push(h("div", { class: "flagged" }, h("strong", {}, "Checks made while generating"), h("ul", {}, set.warnings.map((w) => h("li", {}, w)))));
      if (set?.mapping_gaps?.length) notes.push(h("div", { class: "flagged" }, h("strong", {}, "Steps without a matching scanned control"), h("ul", {}, set.mapping_gaps.map((w) => h("li", {}, w)))));
      if (set?.not_generated?.length) notes.push(h("div", { class: "flagged" }, h("strong", {}, "Not generated (the scan is missing a control)"), h("ul", {}, set.not_generated.map((n) => h("li", {}, `${n.title} — missing: ${n.missingElement}. ${n.reason}`)))));

      const scriptButton = h("button", { class: "btn btn-ai btn-md", type: "button", onclick: () => batch.start([c.id]) }, icon("sparkle"), c.script ? "Regenerate Test Script" : "Generate Test Script");
      const editButton = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => { focusStep = null; editing = true; render(); } }, icon("pencil"), "Edit");
      const trash = h("button", { class: "btn btn-outline-danger btn-md icon-only", type: "button", "aria-label": "Delete test case", title: "Delete test case", onclick: async () => {
        const ok = await confirmDialog({ title: `Delete ${c.key}?`, message: `This deletes the test case${c.script ? " and its test script" : ""}. This cannot be undone.`, confirmLabel: "Delete", danger: true });
        if (!ok) return;
        try {
          await del(`/api/projects/${projectId}/test-cases/${caseId}`);
          toast("Test case deleted.");
          navigate(`/projects/${projectId}/test-cases`);
        } catch (err) {
          toast(errorText(err), "error");
        }
      } }, icon("trash"));

      const content = h(
        "div",
        {},
        batch.el,
        h("div", { class: "detail-head" }, h("a", { class: "back", href: `/projects/${projectId}/test-cases`, "data-link": "", "aria-label": "Back to test cases" }, icon("arrowLeft")), h("h1", {}, c.title), h("div", { class: "pills" }, typePill(c.type))),
        ...topNotes.map((note) => h("div", { style: "margin-bottom:16px" }, note)),
        h(
          "div",
          { class: "detail-grid" },
          h(
            "div",
            {},
            h(
              "div",
              { class: "card" },
              h("div", { class: "steps-head" }, h("p", { class: "card-label", style: "margin:0" }, "Test case description"), h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => { focusStep = null; editing = true; render(); } }, icon("pencil"), "Edit")),
              h("h3", {}, "Overview"),
              h("p", { class: "keep-lines" }, c.overview || "—"),
              h("h3", {}, "Prerequisites"),
              c.prerequisites.length ? h("ul", {}, c.prerequisites.map((p) => h("li", {}, p))) : h("p", {}, "—"),
              h("h3", {}, "Test Objective"),
              h("p", { class: "keep-lines" }, c.objective || "—"),
              h("h3", {}, "Expected Result"),
              h("p", { class: "keep-lines" }, c.expected_result),
            ),
            h(
              "div",
              { class: "card" },
              h(
                "div",
                { class: "steps-head" },
                h("p", { class: "card-label", style: "margin:0" }, "Test steps"),
                h(
                  "div",
                  { class: "toolbar-right" },
                  hasKeys ? h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => dataSetsDialog({ project_id: projectId, id: c.id, key: c.key, title: c.title, data: dataInfo }, () => load()) }, icon("plus"), c.data.sets.length ? "Data variations" : "Add Data Variations") : null,
                  h("label", { for: "tech" }, technical, "Technical columns"),
                ),
              ),
              h("div", { class: "steps-wrap" }, h("table", { class: "steps-table" }, stepsHead, stepsBody)),
            ),
            ...notes,
          ),
          h(
            "div",
            {},
            h(
              "aside",
              { class: "side-card" },
              h("h3", {}, "Details"),
              h(
                "div",
                { class: "side-body" },
                h("div", { class: "kv-block" }, h("span", { class: "k" }, "Test Script"), h("span", { class: "v" }, scriptLink(project, c.script))),
                h("div", { class: "kv-block" }, h("span", { class: "k" }, "Requirement"), h("span", { class: "v cell-key" }, h("a", { class: "row-link", href: `/projects/${projectId}/requirements/${c.requirement_id}`, "data-link": "" }, c.requirement_key || "—"))),
                h("div", { class: "kv-block" }, h("span", { class: "k" }, "Type"), h("span", { class: "v" }, typePill(c.type))),
                h("div", { class: "kv" }, h("span", { class: "k" }, "Created"), h("span", { class: "v" }, formatDay(c.created_at))),
                c.edited ? h("div", { class: "kv" }, h("span", { class: "k" }, "Edited"), h("span", { class: "v" }, `${formatDay(c.updated_at)} (version ${c.version})`)) : null,
                set ? h("div", { class: "kv" }, h("span", { class: "k" }, "Generated by"), h("span", { class: "v" }, h("span", { class: "tpill ai" }, "AI"), ` ${set.model}`)) : null,
                set ? h("div", { class: "kv" }, h("span", { class: "k" }, "Test case set"), h("span", { class: "v" }, h("a", { class: "row-link", href: `/api/projects/${projectId}/test-case-sets/${set.id}/docx`, download: "" }, "Word (.docx)"))) : null,
              ),
            ),
          ),
        ),
      );
      root.replaceChildren(projectFrame(project, "test-cases", content, { trail: [{ label: c.key }], actions: h("div", { class: "toolbar-right" }, editButton, scriptButton, trash) }));
      batch.resume();
    }

    // ------------------------------------------------------------ edit page
    function editView() {
      const work = c.steps.map((s) => ({ testStep: s.testStep, fieldName: blank(s.fieldName), testData: blank(s.testData), expectedResult: blank(s.expectedResult), sourceElementId: s.sourceElementId || null, dataKey: s.dataKey || null }));
      const title = h("input", { class: "input title-input", id: "e-title", maxlength: "300", value: c.title, "aria-label": "Title" });
      const type = h("select", { class: "input type-select", id: "e-type", "aria-label": "Type" }, CASE_TYPES.map((t) => h("option", { value: t, selected: t === c.type }, TYPE_LABEL[t])));
      const area = (id, value, rows, max) => {
        const el = h("textarea", { class: "input edit-area", id, rows: String(rows), maxlength: String(max) });
        el.value = value;
        return el;
      };
      const overview = area("e-overview", c.overview || "", 3, 5000);
      const prerequisites = area("e-prereq", (c.prerequisites || []).join("\n"), 3, 15000);
      const objective = area("e-objective", c.objective || "", 2, 2000);
      const expected = area("e-expected", c.expected_result || "", 2, 2000);
      const snapshot = () => JSON.stringify([title.value, type.value, overview.value, prerequisites.value.split(String.fromCharCode(10)).map((l) => l.trim()).filter(Boolean), objective.value, expected.value, work]);
      const original = snapshot();
      const dirty = () => snapshot() !== original;
      const release = addGuard(() => {
        if (!form.isConnected) {
          release();
          return false;
        }
        return dirty();
      });

      const banner = h("div", { class: "banner-error", role: "alert", hidden: true, style: "text-align:left;margin-bottom:16px" });
      const fail = (message, el) => {
        banner.replaceChildren(message);
        banner.hidden = false;
        banner.scrollIntoView({ block: "nearest" });
        if (el) el.focus();
      };

      const stepsBody = h("tbody");
      function drawRows() {
        stepsBody.replaceChildren(
          ...work.map((s, i) => {
            const bind = (el, key) => {
              el.addEventListener("input", () => { s[key] = el.value; });
              return el;
            };
            const field = (key, label, tag, extra) => {
              const el = h(tag, { class: "input cell-input", "aria-label": `Step ${i + 1} ${label}`, maxlength: extra.max, ...(tag === "textarea" ? { rows: "2" } : { type: "text" }) });
              el.value = s[key];
              return bind(el, key);
            };
            const stepText = field("testStep", "test step", "textarea", { max: "1000" });
            if (focusStep === i + 1) setTimeout(() => { stepText.focus(); stepText.scrollIntoView({ block: "center" }); focusStep = null; }, 0);
            const move = (delta) => () => {
              const j = i + delta;
              [work[i], work[j]] = [work[j], work[i]];
              drawRows();
            };
            return h(
              "tr",
              {},
              h("td", { class: "num" }, String(i + 1)),
              h("td", {}, stepText),
              h("td", {}, field("fieldName", "field name", "input", { max: "200" })),
              h("td", {}, field("testData", "test data", "input", { max: "500" })),
              h("td", {}, field("expectedResult", "expected result", "textarea", { max: "1000" })),
              h(
                "td",
                { class: "row-tools" },
                h("button", { class: "icon-btn", type: "button", "aria-label": `Move step ${i + 1} up`, title: "Move up", disabled: i === 0, onclick: move(-1) }, "↑"),
                h("button", { class: "icon-btn", type: "button", "aria-label": `Move step ${i + 1} down`, title: "Move down", disabled: i === work.length - 1, onclick: move(1) }, "↓"),
                h("button", { class: "icon-btn danger", type: "button", "aria-label": `Delete step ${i + 1}`, title: "Delete this step", disabled: work.length === 1, onclick: () => { work.splice(i, 1); drawRows(); } }, icon("trash")),
              ),
            );
          }),
        );
      }
      drawRows();
      const addStep = h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => {
        work.push({ testStep: "", fieldName: "", testData: "", expectedResult: "", sourceElementId: null, dataKey: null });
        focusStep = work.length;
        drawRows();
      } }, icon("plus"), "Add step");

      const cancel = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: async () => {
        if (dirty() && !(await confirmDialog({ title: "Discard your changes?", message: "The changes you made to this test case have not been saved.", confirmLabel: "Discard", danger: true }))) return;
        editing = false;
        load(); // show what is saved on the server, not an older copy
      } }, "Cancel");
      const save = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Save changes");
      const form = h("form", { class: "edit-form", novalidate: true });
      const lbl = (text, id) => h("label", { class: "label", for: id }, text);
      form.append(
        banner,
        h(
          "div",
          { class: "card" },
          h("p", { class: "card-label" }, "Test case description"),
          h("div", { class: "field" }, lbl("Overview", "e-overview"), overview),
          h("div", { class: "field" }, lbl("Prerequisites (one per line)", "e-prereq"), prerequisites),
          h("div", { class: "field" }, lbl("Test Objective", "e-objective"), objective),
          h("div", { class: "field" }, lbl("Expected Result", "e-expected"), expected),
        ),
        h(
          "div",
          { class: "card" },
          h("div", { class: "steps-head" }, h("p", { class: "card-label", style: "margin:0" }, "Test steps"), addStep),
          h("p", { class: "field-hint" }, "One action per step, starting with an action word (Navigate, Click, Select, Enter, Hover, Check, Uncheck, Clear, Press, Upload, Scroll, Capture). Leave Field Name, Test Data or Expected Result empty if they do not apply."),
          h("div", { class: "steps-wrap" }, h("table", { class: "steps-table editing" }, h("thead", {}, h("tr", {}, ...["ID", "Test Step", "Field Name", "Test Data", "Expected Result"].map((t) => h("th", { scope: "col" }, t)), h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Tools")))), stepsBody)),
        ),
        c.script ? h("div", { class: "notice" }, `${c.script.key} is not changed by saving. After saving, use Regenerate Test Script to write it from the edited steps.`) : null,
        h("div", { class: "edit-bar" }, cancel, save),
      );
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        banner.hidden = true;
        if (!title.value.trim()) return fail("The title cannot be empty.", title);
        if (!expected.value.trim()) return fail("The expected result cannot be empty.", expected);
        for (let i = 0; i < work.length; i += 1) {
          if (!work[i].testStep.trim()) return fail(`Step ${i + 1} has no text. Write the action, or delete the step.`);
        }
        save.disabled = true;
        save.textContent = "Saving…";
        try {
          const saved = await patch(`/api/projects/${projectId}/test-cases/${caseId}`, {
            version: c.version,
            title: title.value,
            type: type.value,
            overview: overview.value,
            prerequisites: prerequisites.value.split("\n"),
            objective: objective.value,
            expected_result: expected.value,
            steps: work.map((s) => ({ testStep: s.testStep, fieldName: s.fieldName, testData: s.testData, expectedResult: s.expectedResult, sourceElementId: s.sourceElementId, dataKey: s.dataKey })),
          });
          warnings = saved.warnings || [];
          editing = false;
          toast("Test case saved.");
          load(); // fresh copy (script flag, versions) from the server
        } catch (err) {
          save.disabled = false;
          save.textContent = "Save changes";
          fail(err.status === 409 ? h("span", {}, errorText(err), " ", h("button", { class: "link", type: "button", onclick: () => { editing = false; load(); } }, "Reload now")) : errorText(err));
        }
      });

      const content = h(
        "div",
        {},
        h("div", { class: "detail-head" }, h("a", { class: "back", href: `/projects/${projectId}/test-cases`, "data-link": "", "aria-label": "Back to test cases" }, icon("arrowLeft")), title, type),
        form,
      );
      root.replaceChildren(projectFrame(project, "test-cases", content, { trail: [{ label: c.key }, { label: "Edit" }] }));
      if (focusStep == null) setTimeout(() => title.focus(), 0);
    }

    function render() {
      if (editing) editView();
      else readView();
    }
    window.scrollTo({ top: 0 });
    render();
  }
  load();
  return root;
}
