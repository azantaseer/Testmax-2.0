import { ApiError, get, post } from "../api.js";
import { errorText, modal, pagination, toast } from "../components.js";
import { h, icon } from "../dom.js";
import { confirmAndDelete, rowSelection } from "../selection.js";
import { remember, savedRows, savedSort, emptyState, filterPopover, listToolbar, searchBox, skeletonTable, tableBlock } from "../table.js";
import { navigate, PROVIDER_LABEL, state } from "../store.js";
import { formatDate, formatDay, projectFrame, typePill } from "./projectFrame.js";

const SOURCE_LABEL = { system: "system default", tenant: "your organization's choice", user: "your own setting" };

export function statusPill(status) {
  const label = { passed: "Passed", failed: "Failed", skipped: "Skipped", aborted: "Aborted", running: "Running", queued: "Queued" }[status] || status;
  return h("span", { class: `tpill ${status}` }, label);
}

// ---------------------------------------------------------------- batch of script jobs (one card for any number of cases)
/** One progress card for a set of script generation jobs. onDone() is called when every job has ended. */
export function scriptBatch(project, onDone = () => {}) {
  const host = h("div", { class: "job-stack" });
  const jobs = new Map();
  let polling = false;

  function render() {
    if (!jobs.size) return host.replaceChildren();
    const list = [...jobs.values()];
    const active = list.filter((j) => j.status === "queued" || j.status === "running");
    const failed = list.filter((j) => j.status === "failed");
    const done = list.filter((j) => j.status === "succeeded");
    const cls = active.length ? "running" : failed.length ? "failed" : "done";
    const title = active.length ? `Writing test scripts… ${done.length + failed.length} of ${list.length} finished` : failed.length ? `${done.length} script${done.length === 1 ? "" : "s"} generated, ${failed.length} failed` : `${done.length} test script${done.length === 1 ? "" : "s"} generated`;
    const card = h("div", { class: `job-card ${cls}`, role: "status" }, h("div", { class: "job-head" }, active.length ? h("span", { class: "spinner", "aria-hidden": "true" }) : null, h("strong", {}, title)));
    const first = list[0];
    if (first.model) card.append(h("div", { class: "job-meta" }, `${PROVIDER_LABEL[first.provider] || first.provider} · ${first.model}`));
    card.append(
      h(
        "div",
        { class: "progress-steps" },
        list.map((j) => {
          const dot = j.status === "succeeded" ? "passed" : j.status === "failed" ? "failed" : "running";
          const text = j.status === "failed" ? j.error : j.status === "succeeded" ? (j.result?.blocked ? "saved — needs test data before it can run" : `saved as ${j.result?.script_key}`) : j.stalled ? "waiting for a worker" : j.progress || "";
          return h("div", { class: "row" }, h("span", { class: `dot ${dot}` }), h("strong", {}, j.filename || j.test_case_key || ""), h("span", { class: j.status === "failed" ? "job-error" : "muted-text" }, text));
        }),
      ),
    );
    if (!active.length) card.append(h("button", { class: "link", type: "button", onclick: () => { jobs.clear(); render(); } }, "Dismiss"));
    host.replaceChildren(card);
  }

  async function poll() {
    if (polling) return;
    polling = true;
    while (host.isConnected || !host.parentNode) {
      const active = [...jobs.values()].filter((j) => j.status === "queued" || j.status === "running");
      if (!active.length) break;
      await new Promise((r) => setTimeout(r, 1500));
      if (!host.isConnected) break;
      await Promise.all(
        active.map(async (j) => {
          try {
            jobs.set(j.id, { ...(await get(`/api/jobs/${j.id}`)), filename: j.filename });
          } catch (err) {
            if (err instanceof ApiError && err.status === 404) jobs.delete(j.id);
          }
        }),
      );
      render();
    }
    polling = false;
    if (![...jobs.values()].some((j) => j.status === "queued" || j.status === "running") && jobs.size) onDone();
  }

  function add(list) {
    list.forEach((j) => jobs.set(j.id, j));
    render();
    poll();
  }

  /** Ask the server to start the given test cases; shows what was started and what was skipped. */
  async function start(caseIds) {
    try {
      const data = await post(`/api/projects/${project.id}/test-scripts/generate`, { test_case_ids: caseIds });
      data.skipped.forEach((s) => toast(`${s.key}: ${s.reason}`, "error"));
      if (data.jobs.length) add(data.jobs);
      return data;
    } catch (err) {
      toast(errorText(err), "error");
      return null;
    }
  }

  async function resume() {
    try {
      const { jobs: list } = await get(`/api/projects/${project.id}/jobs?type=generate_test_script&limit=50`);
      const active = list.filter((j) => j.status === "queued" || j.status === "running").map((j) => ({ ...j, filename: j.filename || j.test_case_key }));
      if (active.length) add(active);
    } catch {
      /* the page still works without this */
    }
  }

  return { el: host, add, start, resume };
}

// ---------------------------------------------------------------- Generate Scripts dialog
export async function generateScriptsModal(project, batch, preselect = []) {
  let rows;
  try {
    const data = await get(`/api/projects/${project.id}/test-cases?page_size=100&sort=key&direction=asc`);
    rows = data.test_cases;
  } catch (err) {
    toast(errorText(err), "error");
    return;
  }
  const ai = state.me.ai;
  const missing = rows.filter((c) => !c.script);
  const list = rows.filter((c) => !c.script || preselect.includes(c.id));
  const picks = new Map(list.map((c) => [c.id, h("input", { class: "cb", type: "checkbox", id: `p-${c.id}`, checked: preselect.length ? preselect.includes(c.id) : true })]));
  const all = h("input", { class: "cb", type: "checkbox", id: "p-all", checked: !preselect.length });
  const count = h("span", { class: "muted-text small" });
  const update = () => {
    const n = [...picks.values()].filter((p) => p.checked).length;
    count.textContent = `${n} of ${list.length} selected`;
    submit.disabled = n === 0;
    all.checked = n === list.length && n > 0;
  };
  all.addEventListener("change", () => {
    picks.forEach((p) => (p.checked = all.checked));
    update();
  });
  picks.forEach((p) => p.addEventListener("change", update));
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const submit = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Generate Scripts");
  const form = h(
    "form",
    { class: "form-stack tight", novalidate: true },
    h("p", { class: "modal-text", style: "margin:0" }, "The AI writes one Playwright script per test case, using the locators recorded by the scan. Test cases that already have a script are not listed."),
    list.length
      ? h("div", {}, h("label", { class: "check-row", for: "p-all", style: "font-weight:600" }, all, "Select all"), h("div", { class: "check-list", style: "max-height:260px" }, list.map((c) => h("label", { class: "check-row", for: `p-${c.id}` }, picks.get(c.id), h("span", { class: "cell-key" }, c.key), h("span", { class: "trunc", style: "flex:1;min-width:0" }, c.title), typePill(c.type)))))
      : h("div", { class: "notice" }, rows.length ? "Every test case already has a script." : "There are no test cases yet. Generate test cases from a scanned requirement first."),
    h("div", { class: "effective" }, ai.provider ? `AI model: ${PROVIDER_LABEL[ai.provider]} · ${ai.model} (${SOURCE_LABEL[ai.source]})` : "AI model: none selected yet."),
    banner,
    h("div", { class: "modal-actions" }, count, h("span", { style: "flex:1" }), h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), submit),
  );
  const m = modal({ title: "Generate Scripts", content: form, width: 640 });
  update();
  if (!list.length) submit.disabled = true;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const chosen = list.filter((c) => picks.get(c.id).checked).map((c) => c.id);
    if (!chosen.length) return;
    submit.disabled = true;
    submit.textContent = "Starting…";
    const data = await batch.start(chosen);
    if (data) m.close();
    else {
      submit.disabled = false;
      submit.textContent = "Generate Scripts";
    }
  });
  void missing;
}

// ---------------------------------------------------------------- list
export function testScriptsView(projectId) {
  const root = h("div", { class: "fill" }, h("div", {}, "Loading…"));
  (async () => {
    let project;
    try {
      project = await get(`/api/projects/${projectId}`);
    } catch (err) {
      root.replaceChildren(h("div", { class: "banner-error" }, err.status === 404 ? "This project was not found." : errorText(err)));
      return;
    }
    document.title = `Test Scripts — ${project.name} — TestMax`;
    const q = { page: 1, pageSize: savedRows(10), search: "", ...savedSort("scripts", { sort: "key", dir: "asc" }, ["key", "created", "updated"]), filters: {} };
    const HEADERS = ["", "Script ID", "Title", "Type", "Language", "Test Case", "Execution ID", "Execution Status", "Execution time", "Created", "Last Updated", ""];
    const tableHost = h("div", { class: "table-host fill" });
    const batch = scriptBatch(project, () => load());
    const selection = rowSelection({
      url: `/api/projects/${project.id}/test-scripts/delete`,
      singular: "test script",
      plural: "test scripts",
      consequence: ["Its executions are deleted with it; the test case stays and can get a new script.", "Their executions are deleted with them; the test cases stay and can get a new script."],
      onDone: () => load(),
    });
    const search = searchBox({
      placeholder: "Search by title or code…",
      label: "Search test scripts",
      onSearch: (text) => {
        selection.clear(); // a changed search must not leave hidden rows selected
        q.search = text;
        q.page = 1;
        load();
      },
    });
    const filter = filterPopover({
      fields: [
        { key: "type", label: "Type", type: "options", options: ["POSITIVE", "NEGATIVE", "VERIFICATION", "BOUNDARY", "EDGE"].map((t) => ({ value: t, label: t.charAt(0) + t.slice(1).toLowerCase() })) },
        { key: "state", label: "State", type: "options", options: [{ value: "up_to_date", label: "Up to date" }, { value: "out_of_date", label: "Out of date" }, { value: "blocked", label: "Needs test data" }] },
        { key: "created", label: "Created", type: "date" },
      ],
      onApply: (value) => {
        selection.clear();
        q.filters = value;
        q.page = 1;
        load();
      },
    });
    function sortHead(label, key) {
      const active = q.sort === key;
      return h("th", { scope: "col", "aria-sort": active ? (q.dir === "asc" ? "ascending" : "descending") : "none" }, h("button", { class: "sort-btn", type: "button", onclick: () => { q.dir = active && q.dir === "asc" ? "desc" : "asc"; q.sort = key; remember("sort.scripts", { sort: q.sort, dir: q.dir }); load(); } }, label, h("span", { class: "sort-arrow" }, active ? (q.dir === "asc" ? "▲" : "▼") : "")));
    }
    function queryString() {
      const qs = new URLSearchParams({ page: q.page, page_size: q.pageSize, q: q.search, sort: q.sort, direction: q.dir });
      const f = q.filters;
      (f.type || []).forEach((v) => qs.append("type", v));
      (f.state || []).forEach((v) => qs.append("state", v));
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
      load();
    }
    async function load() {
      try {
        const data = await get(`/api/projects/${project.id}/test-scripts?${queryString()}`);
        if (!data.scripts.length && data.total > 0 && q.page > 1) {
          q.page = Math.ceil(data.total / q.pageSize); // the last page was emptied by a delete
          return load();
        }
        selection.begin();
        if (!data.scripts.length) {
          const narrowed = !!(q.search.trim() || filter.count());
          tableHost.replaceChildren(
            narrowed
              ? emptyState({ title: "No test scripts match", text: "Try a different search or clear the filters.", actions: [h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: clearAll }, "Clear search and filters")] })
              : emptyState({
                  title: "No test scripts yet",
                  text: "The AI writes one Playwright script per test case.",
                  actions: [h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => generateScriptsModal(project, batch) }, icon("sparkle"), "Generate Scripts")],
                }),
          );
          selection.done();
          return;
        }
        const rows = data.scripts.map((s) =>
          h(
            "tr",
            {},
            selection.box(s.id, s.key),
            h("td", { class: "cell-key" }, h("a", { class: "id-link", href: `/projects/${project.id}/test-scripts/${s.id}`, "data-link": "" }, s.key)),
            h("td", { class: "cell-strong cell-title" }, h("a", { class: "row-link", href: `/projects/${project.id}/test-scripts/${s.id}`, "data-link": "", title: s.title }, s.title), s.blocked ? h("div", { class: "muted-text small" }, "Needs test data before it can run") : null, s.stale ? h("div", { class: "small" }, h("span", { class: "tpill stale", title: "The test case was edited after this script was written" }, "Out of date")) : null),
            h("td", {}, typePill(s.type)),
            h("td", {}, h("span", { class: "tpill lang" }, "JavaScript")),
            h("td", { class: "cell-key" }, h("a", { class: "row-link", href: `/projects/${project.id}/test-cases/${s.test_case_id}`, "data-link": "" }, s.test_case_key || "—")),
            h("td", { class: "cell-key" }, s.last_run ? h("a", { class: "row-link", href: `/projects/${project.id}/test-runs/${s.last_run.id}`, "data-link": "" }, s.last_run.key) : h("span", { class: "muted-text" }, "—")),
            h("td", {}, s.last_run ? statusPill(s.last_run.status) : h("span", { class: "muted-text" }, "—")),
            h("td", {}, s.last_run && s.last_run.duration_seconds != null ? `${s.last_run.duration_seconds}s` : "—"),
            h("td", {}, formatDay(s.created_at)),
            h("td", {}, formatDay(s.updated_at)),
            h("td", { class: "cell-actions" }, s.last_run ? h("a", { class: "btn btn-secondary btn-sm", href: `/projects/${project.id}/test-runs/${s.last_run.id}`, "data-link": "" }, "View Report") : null),
          ),
        );
        tableHost.replaceChildren(
          tableBlock(h("tr", {}, selection.head(), sortHead("Script ID", "key"), h("th", { scope: "col" }, "Title"), h("th", { scope: "col" }, "Type"), h("th", { scope: "col" }, "Language"), h("th", { scope: "col" }, "Test Case"), h("th", { scope: "col" }, "Execution ID"), h("th", { scope: "col" }, "Execution Status"), h("th", { scope: "col" }, "Execution time"), sortHead("Created", "created"), sortHead("Last Updated", "updated"), h("th", { scope: "col" }, "")), rows),
          pagination({ page: data.page, pageSize: data.page_size, total: data.total, onPage: (p) => { q.page = p; load(); }, onPageSize: (n) => { q.pageSize = n; q.page = 1; load(); } }),
        );
        selection.done();
      } catch (err) {
        tableHost.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load test scripts.")));
      }
    }
    const generate = h("button", { class: "btn btn-ai btn-md", type: "button", onclick: () => generateScriptsModal(project, batch) }, icon("sparkle"), "Generate Scripts");
    const content = h("div", { class: "fill" }, batch.el, listToolbar(search.el, filter.el), selection.bar, tableHost);
    root.replaceChildren(projectFrame(project, "test-scripts", content, { actions: h("div", { class: "toolbar-right" }, generate) }));
    tableHost.replaceChildren(skeletonTable(HEADERS));
    load();
    batch.resume();
  })();
  return root;
}

// ---------------------------------------------------------------- code viewer
const KEYWORDS = new Set("const let var function async await return if else for while new try catch throw typeof of in true false null undefined".split(" "));

/** Colours one line of JavaScript. Plain text nodes only (never HTML), so the script can never run in the page. */
function highlightLine(line, find) {
  const out = [];
  const re = /(\/\/.*$)|('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")|(\b\d+(?:\.\d+)?\b)|([A-Za-z_$][\w$]*)(?=\s*\()|([A-Za-z_$][\w$]*)|(\s+|[^\sA-Za-z_$\d]+)/g;
  let m;
  while ((m = re.exec(line))) {
    const [text, comment, str, num, fn, word] = m;
    let cls = "";
    if (comment) cls = "tok-c";
    else if (str) cls = "tok-s";
    else if (num) cls = "tok-n";
    else if (fn) cls = KEYWORDS.has(text) ? "tok-k" : "tok-f";
    else if (word) cls = KEYWORDS.has(text) ? "tok-k" : "";
    out.push(cls ? h("span", { class: cls }, text) : text);
  }
  if (!find) return out;
  return out; // find highlights whole lines (see below)
}

export function codeViewer({ code, fileName, label, editedAt }) {
  const lines = code.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  const body = h("div", { class: "code-body", tabindex: "0", role: "region", "aria-label": "Script code" });
  const findRow = h("div", { class: "code-find", hidden: true });
  const findInput = h("input", { type: "search", placeholder: "Find in script", "aria-label": "Find in script" });
  const findCount = h("span", {});
  findRow.append(findInput, findCount);
  const rows = lines.map((line, i) => {
    const row = h("div", { class: "code-line" }, h("span", { class: "ln" }, String(i + 1)), h("span", { class: "tx" }, ...highlightLine(line)));
    return row;
  });
  body.append(...rows);
  function applyFind() {
    const needle = findInput.value;
    let hits = 0;
    let first = null;
    rows.forEach((row, i) => {
      const hit = needle && lines[i].toLowerCase().includes(needle.toLowerCase());
      row.classList.toggle("hit", Boolean(hit));
      if (hit) {
        hits += 1;
        first = first || row;
      }
    });
    findCount.textContent = needle ? `${hits} line${hits === 1 ? "" : "s"}` : "";
    if (first) first.scrollIntoView({ block: "center" });
  }
  findInput.addEventListener("input", applyFind);
  const copy = h("button", { class: "code-btn", type: "button", onclick: async () => {
    try {
      await navigator.clipboard.writeText(code);
      toast("Script copied.");
    } catch {
      toast("Could not copy. Select the code and copy it.", "error");
    }
  } }, "Copy");
  const find = h("button", { class: "code-btn", type: "button", onclick: () => {
    findRow.hidden = !findRow.hidden;
    if (!findRow.hidden) findInput.focus();
    else {
      findInput.value = "";
      applyFind();
    }
  } }, "Find");
  return h(
    "div",
    { class: "code-card" },
    h("div", { class: "code-bar" }, h("span", { class: "file-chip" }, fileName), h("span", { class: "fw-chip" }, label), h("span", { class: "code-meta" }, editedAt), h("div", { class: "code-tools" }, find, copy)),
    findRow,
    body,
  );
}

// If this script is being executed right now (e.g. the page was reloaded), show that run again.
async function resumeRun(project, script, reload) {
  try {
    const { jobs } = await get(`/api/projects/${project.id}/jobs?type=run_script&limit=20`);
    const running = jobs.find((j) => (j.status === "queued" || j.status === "running") && j.filename === script.key);
    if (running) import("./testRuns.js").then((m) => m.executeScript(project, script, { job: running, onFinished: reload }));
  } catch {
    /* nothing to resume */
  }
}

// ---------------------------------------------------------------- viewer
export function testScriptDetailView(projectId, scriptId) {
  const root = h("div", {}, h("div", {}, "Loading…"));
  async function load() {
    let project;
    let s;
    try {
      [project, s] = await Promise.all([get(`/api/projects/${projectId}`), get(`/api/projects/${projectId}/test-scripts/${scriptId}`)]);
    } catch (err) {
      root.replaceChildren(h("a", { class: "back-link", href: `/projects/${projectId}/test-scripts`, "data-link": "" }, "← Test scripts"), h("div", { class: "banner-error", style: "margin-top:16px" }, err.status === 404 ? "This test script was not found." : errorText(err)));
      return;
    }
    document.title = `${s.key} — TestMax`;
    const batch = scriptBatch(project, () => load());
    const notes = [];
    if (s.stale) notes.push(h("div", { class: "notice stale-note", style: "margin-bottom:16px" }, h("span", {}, h("strong", {}, "This script is out of date. "), "The test case was edited after it was written, so the script may no longer match it. "), h("button", { class: "btn btn-ai btn-sm", type: "button", onclick: () => batch.start([s.test_case_id]) }, icon("sparkle"), "Regenerate")));
    if (s.data_driven && s.data_gaps.length) {
      notes.push(
        h("div", { class: "flagged", style: "margin-bottom:16px" }, h("strong", {}, "Some values are written into the script as typed. "), `Step ${s.data_gaps.map((g) => g.stepNo).join(", ")} ${s.data_gaps.length === 1 ? "has" : "have"} test data that the script does not read from its data block (${s.data_gaps.map((g) => g.key).join(", ")}). A data variation will not change ${s.data_gaps.length === 1 ? "it" : "them"}. Regenerate the script to try again.`),
      );
    }
    if (!s.data_driven && !s.blocked_message) {
      const convert = h("button", { class: "btn btn-secondary btn-sm", type: "button" }, "Make data-driven");
      convert.addEventListener("click", async () => {
        convert.disabled = true;
        try {
          await post(`/api/projects/${projectId}/test-scripts/${scriptId}/make-data-driven`);
          toast("The script now reads its values from a data block.");
          load();
        } catch (err) {
          convert.disabled = false;
          toast(errorText(err), "error");
        }
      });
      notes.push(h("div", { class: "notice stale-note", style: "margin-top:0;margin-bottom:16px" }, h("span", {}, h("strong", {}, "This script has its test data written into it. "), "Make it data-driven to run it with different data variations of the test case. No AI call is needed."), convert));
    }
    if (s.blocked_message) notes.push(h("div", { class: "flagged", style: "margin-bottom:16px" }, h("strong", {}, "Needs test data. "), s.blocked_message));
    if (s.not_automated.length) notes.push(h("div", { class: "flagged", style: "margin-bottom:16px" }, h("strong", {}, "Not automated"), h("ul", {}, s.not_automated.map((n) => h("li", {}, n)))));
    if (s.steps_without_locator.length) notes.push(h("div", { class: "flagged", style: "margin-bottom:16px" }, h("strong", {}, "Steps without a scanned control: "), s.steps_without_locator.join(", ")));

    const execute = h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => import("./testRuns.js").then((m) => m.executeScript(project, s, { onFinished: () => load() })) }, "▷ Execute");
    const regenerate = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => batch.start([s.test_case_id]) }, icon("sparkle"), "Regenerate");
    const download = h("a", { class: "btn btn-secondary btn-md", href: `/api/projects/${projectId}/test-scripts/${scriptId}/download`, download: "" }, "Download");
    const trash = h("button", { class: "btn btn-outline-danger btn-md icon-only", type: "button", "aria-label": "Delete test script", title: "Delete test script", onclick: async () => {
      const result = await confirmAndDelete({ url: `/api/projects/${projectId}/test-scripts/delete`, items: [{ id: s.id, key: s.key }], singular: "test script", plural: "test scripts", consequence: ["Its executions are deleted with it; the test case stays and can get a new script.", "Their executions are deleted with them; the test cases stay and can get a new script."] });
      if (result && result.deleted.length) navigate(`/projects/${projectId}/test-scripts`);
    } }, icon("trash"));

    const historySlot = h("div", { style: "margin-top:16px" });
    import("./testRuns.js").then((m) => historySlot.replaceChildren(m.runHistoryCard(project, s.run_stats)));
    const content = h(
      "div",
      {},
      h("div", { class: "detail-head" }, h("a", { class: "back", href: `/projects/${projectId}/test-scripts`, "data-link": "", "aria-label": "Back to test scripts" }, icon("arrowLeft")), h("h1", {}, s.title)),
      batch.el,
      ...notes,
      h(
        "div",
        { class: "detail-grid" },
        h("div", {}, codeViewer({ code: s.code, fileName: s.file_name, label: "Playwright · JS", editedAt: `Last edited ${formatDate(s.updated_at)}` })),
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
              h("div", { class: "kv" }, h("span", { class: "k" }, "Framework"), h("span", { class: "v" }, s.framework)),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Language"), h("span", { class: "v" }, "JavaScript")),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Author"), h("span", { class: "v" }, h("span", { class: "tpill ai" }, "AI"), ` ${s.model || ""}`)),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Test data"), h("span", { class: "v" }, s.data_driven ? `Data-driven (${s.data_keys.length} value${s.data_keys.length === 1 ? "" : "s"})` : "Fixed values")),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Created"), h("span", { class: "v" }, formatDay(s.created_at))),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Project"), h("span", { class: "v" }, project.name)),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Test case"), h("span", { class: "v cell-key" }, h("a", { class: "row-link", href: `/projects/${projectId}/test-cases/${s.test_case_id}`, "data-link": "" }, s.test_case_key || "—"))),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Generated in"), h("span", { class: "v" }, `${s.duration_seconds ?? "—"}s · ${s.attempts} attempt${s.attempts === 1 ? "" : "s"}`)),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Tokens"), h("span", { class: "v" }, `${s.tokens_in ?? 0} in · ${s.tokens_out ?? 0} out`)),
            ),
          ),
          historySlot,
        ),
      ),
    );
    resumeRun(project, s, load);
    root.replaceChildren(projectFrame(project, "test-scripts", content, { trail: [{ label: s.key }], actions: h("div", { class: "toolbar-right" }, execute, regenerate, download, trash) }));
    batch.resume();
  }
  load();
  return root;
}
