// Test Batches: scripts that run in a chosen order in ONE browser session.
// List, create/edit dialog (script picker, order, data variation per script, stop/continue), run + progress, batch run result page.
import { ApiError, del, get, post, put } from "../api.js";
import { confirmDialog, errorText, modal, moreMenu, pagination, toast } from "../components.js";
import { h, icon } from "../dom.js";
import { navigate } from "../store.js";
import { emptyState, listToolbar, remember, savedRows, savedSort, searchBox, skeletonTable, tableBlock } from "../table.js";
import { setLabel } from "./dataSets.js";
import { formatDate, formatDay, projectFrame } from "./projectFrame.js";
import { runnerChoice } from "./runnerPicker.js";

const RESULT_LABEL = { passed: "Passed", failed: "Failed", skipped: "Skipped", aborted: "Aborted", not_run: "Not run", running: "Running", queued: "Queued" };
const FAILURE_LABEL = { stop: "Stop the batch", continue: "Continue with the next script" };

function seconds(value) {
  return value == null ? "—" : `${Number(value).toFixed(1)}s`;
}

/** A result pill: Passed, Failed, Not run (an earlier script failed and the batch stops)... */
export function resultPill(status) {
  return h("span", { class: `tpill ${status}` }, RESULT_LABEL[status] || status);
}

function failurePill(mode) {
  return h("span", { class: `tpill ${mode === "continue" ? "boundary" : "edge"}`, title: FAILURE_LABEL[mode] }, mode === "continue" ? "Continue" : "Stop");
}

/** "3 passed · 1 failed · 1 not run" */
function countsText(counts) {
  const parts = ["passed", "failed", "aborted", "not_run"].filter((k) => counts[k]).map((k) => `${counts[k]} ${RESULT_LABEL[k].toLowerCase()}`);
  return parts.join(" · ") || "no scripts ran";
}

/** The first lines of an error, without blank or repeated lines. */
function errorLines(text) {
  const lines = String(text).split("\n").map((l) => l.trimEnd()).filter((l, i, all) => l.trim() && !all.slice(0, i).includes(l));
  return lines.slice(0, 2).join("\n");
}

function dvText(item) {
  if (item.data_set) return item.data_set.name;
  return null;
}

// ---------------------------------------------------------------- the create / edit dialog
const MAX_PICK_LIST = 100;

/**
 * batch = null creates a new one. `project` is the project object. onSaved(batch) after a successful save.
 * The dialog holds: name, description, the stop/continue setting, the ordered scripts (each with its data variation), and a script picker.
 */
export function batchEditor(project, batch, onSaved) {
  const editing = Boolean(batch);
  const maxItems = batch?.max_items || 8;
  let uid = 0;
  const items = (batch?.items || []).map((i) => ({
    uid: ++uid,
    script_id: i.script_id,
    key: i.script_key,
    title: i.title,
    data_driven: i.data_driven,
    blocked: i.blocked,
    choices: i.data_sets,
    data_set_id: i.data_set_id,
    missing: i.data_set_missing,
  }));
  const name = h("input", { class: "input", id: "bt-name", name: "name", maxlength: "120", autocomplete: "off", value: batch?.name || "", placeholder: "For example: Register, then search" });
  const description = h("textarea", { class: "input", id: "bt-desc", rows: "2", maxlength: "1000", placeholder: "Optional" });
  description.value = batch?.description || "";
  const mode = (value, label, hint) =>
    h("label", { class: "bt-radio" }, h("input", { type: "radio", name: "on_failure", value, checked: (batch?.on_failure || "stop") === value }), h("span", {}, h("strong", {}, label), h("span", { class: "field-hint" }, hint)));
  const failure = h(
    "fieldset",
    { class: "field dv-fieldset" },
    h("legend", { class: "label" }, "When a script fails"),
    h("div", { class: "bt-radios" }, mode("stop", "Stop the batch", "The scripts after it are not run."), mode("continue", "Continue with the next script", "Every script runs; the batch fails at the end.")),
  );
  const orderHost = h("div", { class: "bt-order", role: "list", "aria-label": "Scripts in this batch, in run order" });
  const orderCount = h("span", { class: "muted-text small" });
  const pickHost = h("div", { class: "bt-pick-list" });
  const pickNote = h("div", { class: "field-hint" });
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const save = h("button", { class: "btn btn-primary btn-md", type: "submit" }, editing ? "Save changes" : "Create batch");

  function current() {
    return JSON.stringify([name.value.trim(), description.value.trim(), form.querySelector("input[name=on_failure]:checked")?.value, items.map((i) => [i.script_id, i.data_set_id])]);
  }
  let initial = "";

  function renderOrder() {
    orderCount.textContent = `${items.length} of ${maxItems}`;
    if (!items.length) {
      orderHost.replaceChildren(h("div", { class: "bt-empty" }, "No scripts yet. Choose scripts from the list on the right."));
      return;
    }
    orderHost.replaceChildren(
      ...items.map((item, index) => {
        const select = item.data_driven && item.choices.length > 1
          ? h(
              "select",
              { class: "dv-select", "aria-label": `Data variation for ${item.key}` },
              item.missing ? h("option", { value: item.data_set_id, disabled: true }, "Variation no longer exists") : null,
              item.choices.map((c) => h("option", { value: c.id }, c.label)),
            )
          : null;
        if (select) {
          select.value = item.data_set_id;
          select.addEventListener("change", () => {
            item.data_set_id = select.value;
            item.missing = false;
            row.classList.remove("bt-bad");
          });
        }
        const move = (by) => {
          const j = index + by;
          if (j < 0 || j >= items.length) return;
          [items[index], items[j]] = [items[j], items[index]];
          renderOrder();
          orderHost.querySelectorAll(".bt-move")[j * 2 + (by < 0 ? 0 : 1)]?.focus();
        };
        const row = h(
          "div",
          { class: `bt-item${item.missing ? " bt-bad" : ""}`, role: "listitem" },
          h("span", { class: "bt-num" }, String(index + 1)),
          h(
            "div",
            { class: "bt-main" },
            h("div", { class: "bt-title" }, h("span", { class: "cell-key" }, item.key), " ", h("span", { class: "trunc", title: item.title }, item.title)),
            h(
              "div",
              { class: "bt-dv" },
              select || h("span", { class: "muted-text small" }, item.data_driven ? "DV-1 (Default). This test case has no other data variations." : "DV-1: this script is not data-driven, so it only uses its own values."),
              item.blocked ? h("span", { class: "tpill stale", title: "The test case has no real test data, so the batch cannot run until this is fixed" }, "Needs test data") : null,
            ),
          ),
          h("div", { class: "bt-actions" },
            h("button", { class: "icon-btn bt-move", type: "button", "aria-label": `Move ${item.key} up`, disabled: index === 0, onclick: () => move(-1) }, "↑"),
            h("button", { class: "icon-btn bt-move", type: "button", "aria-label": `Move ${item.key} down`, disabled: index === items.length - 1, onclick: () => move(1) }, "↓"),
            h("button", { class: "icon-btn", type: "button", "aria-label": `Remove ${item.key} from the batch`, onclick: () => { items.splice(index, 1); renderOrder(); renderPicker(); } }, "×"),
          ),
        );
        return row;
      }),
    );
  }

  // ---- the picker: the project's scripts, searchable
  let pickRows = [];
  let pickTotal = 0;
  let open = null; // the script whose variation is being chosen
  const search = h("input", { class: "input", type: "search", placeholder: "Search scripts by title…", "aria-label": "Search test scripts to add" });
  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(loadPicker, 250);
  });
  async function loadPicker() {
    try {
      const data = await get(`/api/projects/${project.id}/test-scripts?page_size=${MAX_PICK_LIST}&sort=key&direction=asc&q=${encodeURIComponent(search.value.trim())}`);
      pickRows = data.scripts;
      pickTotal = data.total;
      renderPicker();
    } catch (err) {
      pickHost.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load the test scripts.")));
    }
  }
  function addItem(script, choices, data_set_id) {
    items.push({ uid: ++uid, script_id: script.id, key: script.key, title: script.title, data_driven: script.data_driven, blocked: script.blocked, choices, data_set_id, missing: false });
    open = null;
    renderOrder();
    renderPicker();
  }
  async function askVariation(script) {
    // Only a data-driven script has variations to choose from. The person is asked here, at the moment of adding it.
    let sets = [];
    if (script.data_driven) {
      try {
        sets = (await get(`/api/projects/${project.id}/test-cases/${script.test_case_id}`)).data.sets;
      } catch (err) {
        toast(errorText(err), "error");
        return;
      }
    }
    const choices = [{ id: "default", label: setLabel(0) }, ...sets.map((s, i) => ({ id: s.id, label: setLabel(i + 1, s) }))];
    if (choices.length === 1) return addItem(script, choices, "default");
    open = { script, choices };
    renderPicker();
    pickHost.querySelector(".bt-choose select")?.focus();
  }
  function renderPicker() {
    pickNote.textContent = pickTotal > pickRows.length ? `Showing the first ${pickRows.length} of ${pickTotal} scripts. Search to narrow the list.` : "";
    const full = items.length >= maxItems;
    if (!pickRows.length) {
      pickHost.replaceChildren(h("div", { class: "bt-empty" }, search.value.trim() ? "No test scripts match." : "This project has no test scripts yet."));
      return;
    }
    pickHost.replaceChildren(
      ...pickRows.map((s) => {
        const times = items.filter((i) => i.script_id === s.id).length;
        const row = h(
          "div",
          { class: "bt-pick" },
          h("div", { class: "bt-main" }, h("div", { class: "bt-title" }, h("span", { class: "cell-key" }, s.key), " ", h("span", { class: "trunc", title: s.title }, s.title)), h("div", { class: "muted-text small" }, [s.data_driven ? "Data-driven" : "Own values only", times ? `in the batch ${times}×` : null, s.blocked ? "needs test data" : null].filter(Boolean).join(" · "))),
          h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: full || open?.script.id === s.id, "aria-label": `Add ${s.key} to the batch`, title: full ? `A batch holds ${maxItems} scripts at most` : "", onclick: () => askVariation(s) }, icon("plus"), "Add"),
        );
        if (open?.script.id !== s.id) return row;
        const select = h("select", { class: "dv-select", "aria-label": `Data variation for ${s.key}` }, open.choices.map((c) => h("option", { value: c.id }, c.label)));
        return h(
          "div",
          { class: "bt-pick-open" },
          row,
          h("div", { class: "bt-choose" }, h("span", { class: "label", style: "margin:0" }, "Which data variation should it use?"), select, h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => addItem(s, open.choices, select.value) }, "Add to batch"), h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => { open = null; renderPicker(); } }, "Cancel")),
        );
      }),
    );
  }

  const form = h(
    "form",
    { class: "form-stack tight bt-form", novalidate: true },
    h("div", { class: "field" }, h("label", { class: "label", for: "bt-name" }, "Name", h("span", { class: "req", "aria-hidden": "true" }, " *")), name),
    h("div", { class: "field" }, h("label", { class: "label", for: "bt-desc" }, "Description"), description),
    failure,
    h(
      "div",
      { class: "bt-cols" },
      h("section", { class: "bt-col", "aria-label": "Batch order" }, h("div", { class: "bt-col-head" }, h("strong", {}, "Run order"), orderCount), h("p", { class: "field-hint", style: "margin:0 0 8px" }, "The scripts run top to bottom in one browser, so a later script starts where the one before it ended."), orderHost),
      h("section", { class: "bt-col", "aria-label": "Add scripts" }, h("div", { class: "bt-col-head" }, h("strong", {}, "Add test scripts")), search, pickHost, pickNote),
    ),
    banner,
    h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.dialog.querySelector(".modal-x").click() }, "Cancel"), save),
  );
  const m = modal({ title: editing ? `Edit ${batch.key}` : "Create Test Batch", content: form, width: 980 });
  renderOrder();
  loadPicker();
  initial = current();
  m.setDirty(() => current() !== initial);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    banner.hidden = true;
    name.classList.toggle("invalid", !name.value.trim());
    if (!name.value.trim()) return showError("Enter a name for the batch.", name);
    if (!items.length) return showError("Add at least one test script.");
    if (items.some((i) => i.missing)) return showError("A data variation chosen for a script no longer exists. Choose another one.");
    save.disabled = true;
    save.textContent = "Saving…";
    const body = { name: name.value, description: description.value, on_failure: form.querySelector("input[name=on_failure]:checked").value, items: items.map((i) => ({ script_id: i.script_id, data_set_id: i.data_set_id })) };
    try {
      const saved = editing ? await put(`/api/projects/${project.id}/test-batches/${batch.id}`, body) : await post(`/api/projects/${project.id}/test-batches`, body);
      initial = current();
      m.close();
      toast(editing ? "Batch saved." : `Created ${saved.key}.`);
      onSaved(saved);
    } catch (err) {
      showError(errorText(err));
      save.disabled = false;
      save.textContent = editing ? "Save changes" : "Create batch";
    }
  });
  function showError(message, focus) {
    banner.textContent = message;
    banner.hidden = false;
    (focus || banner).focus?.();
  }
}

// ---------------------------------------------------------------- run: choose the runner, follow the progress
function askRunner(batch, choice) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      m.close();
      resolve(value);
    };
    const form = h(
      "form",
      { class: "form-stack tight", novalidate: true },
      h("p", { class: "modal-text", style: "margin:0" }, `${batch.key} · ${batch.name}: ${batch.item_count} script${batch.item_count === 1 ? "" : "s"} in one browser. ${batch.on_failure === "stop" ? "It stops at the first failed script." : "It continues after a failed script."}`),
      choice.el,
      h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => done(null) }, "Cancel"), h("button", { class: "btn btn-primary btn-md", type: "submit" }, "▷ Run batch")),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      choice.remember();
      done({ runnerId: choice.value() });
    });
    const m = modal({ title: "Run test batch", content: form, width: 520, onClose: () => done(null) });
  });
}

/** Starts a batch run (or, with `job`, follows one that is already running) and shows each script's state. onFinished() when it ended. */
export async function runBatch(project, batch, { job = null, onFinished = () => {} } = {}) {
  let runnerId = null;
  if (!job) {
    let status = null;
    try {
      status = await get("/api/runners/status");
    } catch {
      /* the server checks again when the run starts */
    }
    const choice = runnerChoice(status, { forScripts: true });
    if (choice.choosable) {
      const picked = await askRunner(batch, choice);
      if (!picked) return;
      runnerId = picked.runnerId;
    }
  }
  const spinner = h("span", { class: "spinner", "aria-hidden": "true" });
  const title = h("strong", {}, "Starting…");
  const elapsed = h("span", { class: "muted-text small" }, "");
  const progress = h("div", { class: "job-progress" }, "");
  const list = h("div", { class: "progress-steps bt-run-list" });
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const actions = h("div", { class: "modal-actions" });
  const body = h("div", {}, h("div", { class: "job-head" }, spinner, title, h("span", { style: "flex:1" }), elapsed), h("div", { class: "job-meta", style: "margin-top:4px" }, `${batch.key} · ${batch.name}`), progress, list, banner, actions);
  const m = modal({ title: "Run test batch", content: body, width: 620 });
  const closeBtn = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Close");
  let current = job;
  const cancelBtn = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: async () => {
    cancelBtn.disabled = true;
    cancelBtn.textContent = "Cancelling…";
    try {
      await post(`/api/jobs/${current.id}/cancel`);
    } catch (err) {
      toast(errorText(err), "error");
    }
  } }, "Cancel run");
  actions.append(cancelBtn);
  let started = null;
  if (!current) {
    try {
      started = await post(`/api/projects/${project.id}/test-batches/${batch.id}/run`, { runner_id: runnerId });
      current = started;
    } catch (err) {
      spinner.remove();
      title.textContent = "Cannot run yet";
      banner.textContent = errorText(err);
      banner.hidden = false;
      cancelBtn.remove();
      actions.append(closeBtn);
      if (err instanceof ApiError && err.code === "no_runner_online") banner.append(" ", h("a", { href: "/runners", "data-link": "", onclick: () => m.close() }, "Open Runners"));
      return;
    }
  }
  const rows = (started?.items || batch.items.map((i, n) => ({ position: n + 1, script_key: i.script_key, title: i.title, label: i.data_set_label }))).map((i) => {
    const state = h("span", { class: "muted-text small bt-state" }, "Waiting");
    const dot = h("span", { class: "dot" });
    const el = h("div", { class: "row bt-run-row" }, dot, h("span", { class: "cell-key" }, i.script_key), h("span", { class: "trunc", style: "flex:1;min-width:0", title: i.title }, i.title, i.label ? h("span", { class: "muted-text small" }, ` · ${i.label.split(" — ")[0]}`) : null), state);
    list.append(el);
    return { item: i, state, dot, el, status: "waiting" };
  });
  const setRow = (r, status, text) => {
    r.status = status;
    r.dot.className = `dot ${status === "passed" ? "passed" : status === "failed" ? "failed" : ""}`;
    r.state.replaceChildren(["passed", "failed", "not_run", "aborted"].includes(status) ? resultPill(status) : text);
    r.el.classList.toggle("bt-run-active", status === "running");
  };
  const t0 = Date.now();
  title.textContent = "Running the batch…";
  let lastMessage = "";
  let active = 0;
  let tick = 0;
  let targetOffline = false;
  while (m.dialog.isConnected) {
    elapsed.textContent = `${Math.round((Date.now() - t0) / 1000)}s`;
    const target = current.target_runner;
    if (current.status === "queued" && target && tick % 5 === 0) {
      try {
        const st = await get("/api/runners/status");
        targetOffline = !st.runners.find((r) => r.id === target.id)?.online;
      } catch {
        /* keep the last answer */
      }
    }
    tick += 1;
    if (current.status !== "queued" && current.status !== "running") break;
    if (current.status === "queued") {
      progress.textContent = current.stalled ? "Waiting for the runner…" : target ? `Waiting for "${target.name}" to pick it up…${targetOffline ? " It looks offline. Start it, or cancel and choose another runner." : ""}` : "Waiting for the runner to pick it up…";
    } else {
      progress.textContent = current.stalled ? "Waiting for the runner…" : "Each script's result is shown when the run ends.";
      if (!active && rows[0]) {
        active = 1; // the first messages can be missed between two polls
        setRow(rows[0], "running", "Running…");
      }
      const message = current.progress || "";
      if (message !== lastMessage && /^Finished: /.test(message)) {
        lastMessage = message;
        const text = message.replace("Finished: ", "");
        const mark = text.match(/^Step 0 TMX-(START|DONE|FAIL) (\d+)/);
        if (mark) {
          const n = Number(mark[2]);
          const r = rows[n - 1];
          if (r && mark[1] === "START") {
            rows.slice(0, n - 1).forEach((x) => x.status === "running" || x.status === "waiting" ? setRow(x, "passed") : null);
            active = n;
            setRow(r, "running", "Running…");
          } else if (r && mark[1] === "DONE") setRow(r, "passed");
          else if (r) setRow(r, "failed");
        } else if (active && rows[active - 1]?.status === "running") {
          rows[active - 1].state.textContent = text.replace(/^Step (\d+)[:.]?\s*/, "Step $1: ").slice(0, 70);
        }
      }
    }
    await new Promise((r) => setTimeout(r, 1000));
    if (!m.dialog.isConnected) break;
    try {
      current = await get(`/api/jobs/${current.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) break;
    }
  }
  if (!m.dialog.isConnected && (current.status === "queued" || current.status === "running")) return; // the window was closed; the run goes on
  spinner.remove();
  cancelBtn.remove();
  progress.textContent = "";
  if (current.status === "succeeded") {
    const result = current.result;
    try {
      const run = await get(`/api/projects/${project.id}/test-batch-runs/${result.batch_run_id}`);
      run.items.forEach((i) => rows[i.position - 1] && setRow(rows[i.position - 1], i.status, ""));
    } catch {
      /* the report page has everything */
    }
    title.replaceChildren(`${result.batch_run_key} `, resultPill(result.status));
    progress.textContent = countsText(result.counts);
    actions.append(closeBtn, h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => { m.close(); navigate(`/projects/${project.id}/test-batch-runs/${result.batch_run_id}`); } }, "View Report"));
  } else {
    title.textContent = current.error === "Cancelled by the user." ? "Cancelled" : "The run did not finish";
    banner.textContent = current.error || "The job failed.";
    banner.hidden = false;
    actions.append(closeBtn);
  }
  onFinished(current);
}

// ---------------------------------------------------------------- list
export function testBatchesView(projectId) {
  const root = h("div", { class: "fill" }, h("div", {}, "Loading…"));
  (async () => {
    let project;
    try {
      project = await get(`/api/projects/${projectId}`);
    } catch (err) {
      root.replaceChildren(h("div", { class: "banner-error" }, err.status === 404 ? "This project was not found." : errorText(err)));
      return;
    }
    document.title = `Test Batches — ${project.name} — TestMax`;
    const q = { page: 1, pageSize: savedRows(10), search: "", ...savedSort("batches", { sort: "key", dir: "asc" }, ["key", "name", "created", "updated"]) };
    const HEADERS = ["Batch ID", "Name", "Scripts", "When a script fails", "Last run", "Created", "Last Updated", ""];
    const tableHost = h("div", { class: "table-host fill" });
    const search = searchBox({
      placeholder: "Search by name or description…",
      label: "Search test batches",
      onSearch: (text) => {
        q.search = text;
        q.page = 1;
        load();
      },
    });
    function sortHead(label, key) {
      const active = q.sort === key;
      return h("th", { scope: "col", "aria-sort": active ? (q.dir === "asc" ? "ascending" : "descending") : "none" }, h("button", { class: "sort-btn", type: "button", onclick: () => { q.dir = active && q.dir === "asc" ? "desc" : "asc"; q.sort = key; remember("sort.batches", { sort: q.sort, dir: q.dir }); load(); } }, label, h("span", { class: "sort-arrow" }, active ? (q.dir === "asc" ? "▲" : "▼") : "")));
    }
    const create = () => batchEditor(project, null, (saved) => navigate(`/projects/${project.id}/test-batches/${saved.id}`));
    async function load() {
      try {
        const data = await get(`/api/projects/${project.id}/test-batches?${new URLSearchParams({ page: q.page, page_size: q.pageSize, q: q.search, sort: q.sort, direction: q.dir })}`);
        if (!data.batches.length && data.total > 0 && q.page > 1) {
          q.page = Math.ceil(data.total / q.pageSize);
          return load();
        }
        if (!data.batches.length) {
          tableHost.replaceChildren(
            q.search.trim()
              ? emptyState({ title: "No test batches match", text: "Try a different search.", actions: [h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => { q.search = ""; search.input.value = ""; load(); } }, "Clear search")] })
              : emptyState({ title: "No test batches yet", text: "A batch runs several test scripts in a chosen order in one browser session, so a later script can start where an earlier one ended.", actions: [h("button", { class: "btn btn-primary btn-md", type: "button", onclick: create }, icon("plus"), "Create Test Batch")] }),
          );
          return;
        }
        const rows = data.batches.map((b) =>
          h(
            "tr",
            {},
            h("td", { class: "cell-key" }, h("a", { class: "id-link", href: `/projects/${project.id}/test-batches/${b.id}`, "data-link": "" }, b.key)),
            h("td", { class: "cell-strong cell-title" }, h("a", { class: "row-link", href: `/projects/${project.id}/test-batches/${b.id}`, "data-link": "", title: b.name }, b.name), b.description ? h("div", { class: "muted-text small trunc", title: b.description }, b.description) : null),
            h("td", {}, String(b.item_count)),
            h("td", {}, failurePill(b.on_failure)),
            h("td", {}, b.last_run ? h("span", { class: "bt-last" }, h("a", { class: "row-link cell-key", href: `/projects/${project.id}/test-batch-runs/${b.last_run.id}`, "data-link": "" }, b.last_run.key), resultPill(b.last_run.status)) : h("span", { class: "muted-text" }, "Not run yet")),
            h("td", {}, formatDay(b.created_at)),
            h("td", {}, formatDay(b.updated_at)),
            h("td", { class: "cell-actions" }, h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: !b.item_count, onclick: () => runBatch(project, { ...b, items: [] }, { onFinished: () => load() }) }, "▷ Run")),
          ),
        );
        tableHost.replaceChildren(
          tableBlock(h("tr", {}, sortHead("Batch ID", "key"), sortHead("Name", "name"), h("th", { scope: "col" }, "Scripts"), h("th", { scope: "col" }, "When a script fails"), h("th", { scope: "col" }, "Last run"), sortHead("Created", "created"), sortHead("Last Updated", "updated"), h("th", { scope: "col" }, "")), rows),
          pagination({ page: data.page, pageSize: data.page_size, total: data.total, onPage: (p) => { q.page = p; load(); }, onPageSize: (n) => { q.pageSize = n; q.page = 1; load(); } }),
        );
      } catch (err) {
        tableHost.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load test batches.")));
      }
    }
    const createBtn = h("button", { class: "btn btn-ai btn-md", type: "button", onclick: create }, icon("plus"), "Create Test Batch");
    root.replaceChildren(projectFrame(project, "test-batches", h("div", { class: "fill" }, listToolbar(search.el), tableHost), { actions: h("div", { class: "toolbar-right" }, createBtn) }));
    tableHost.replaceChildren(skeletonTable(HEADERS));
    load();
  })();
  return root;
}

// ---------------------------------------------------------------- one batch
export function testBatchDetailView(projectId, batchId) {
  const root = h("div", { class: "fill" }, h("div", {}, "Loading…"));
  (async () => {
    let project;
    let batch;
    async function fetchAll() {
      [project, batch] = await Promise.all([get(`/api/projects/${projectId}`), get(`/api/projects/${projectId}/test-batches/${batchId}`)]);
    }
    try {
      await fetchAll();
    } catch (err) {
      root.replaceChildren(h("a", { class: "back-link", href: `/projects/${projectId}/test-batches`, "data-link": "" }, "← Test batches"), h("div", { class: "banner-error", style: "margin-top:16px" }, err.status === 404 ? "This test batch was not found." : errorText(err)));
      return;
    }
    async function reload() {
      try {
        await fetchAll();
        draw();
      } catch (err) {
        toast(errorText(err), "error");
      }
    }
    function draw() {
      document.title = `${batch.key} — TestMax`;
      const running = batch.running_job;
      const runBtn = h("button", { class: "btn btn-primary btn-md", type: "button", disabled: !batch.items.length, onclick: () => runBatch(project, batch, { onFinished: reload }) }, "▷ Run batch");
      const edit = h("button", { class: "btn btn-secondary btn-md", type: "button", disabled: Boolean(running), onclick: () => batchEditor(project, batch, reload) }, icon("pencil"), "Edit");
      const menu = moreMenu([{
        label: "Delete batch",
        icon: "trash",
        danger: true,
        onclick: async () => {
          const ok = await confirmDialog({ title: `Delete ${batch.key}?`, message: `This deletes the batch "${batch.name}" and its batch run results. The test scripts and their own executions stay. This cannot be undone.`, confirmLabel: "Delete", danger: true });
          if (!ok) return;
          try {
            await del(`/api/projects/${projectId}/test-batches/${batchId}`);
            toast(`Deleted ${batch.key}.`);
            navigate(`/projects/${projectId}/test-batches`);
          } catch (err) {
            toast(errorText(err), "error");
          }
        },
      }], "Batch actions");
      const stepsCard = h(
        "div",
        { class: "card" },
        h("p", { class: "card-label" }, `Scripts in run order (${batch.items.length} of ${batch.max_items})`),
        batch.items.length
          ? h("div", { class: "table-wrap" }, h("table", { class: "data-table bt-table" }, h("thead", {}, h("tr", {}, ["#", "Script", "Title", "Data variation"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", {}, batch.items.map((i) => h("tr", {}, h("td", {}, String(i.position)), h("td", { class: "cell-key" }, h("a", { class: "id-link", href: `/projects/${projectId}/test-scripts/${i.script_id}`, "data-link": "" }, i.script_key)), h("td", { class: "cell-strong" }, i.title, i.blocked ? h("div", { class: "small" }, h("span", { class: "tpill stale" }, "Needs test data")) : null), h("td", {}, i.data_set_missing ? h("span", { class: "tpill failed" }, "Variation deleted") : i.data_driven ? i.data_set_label : h("span", { class: "muted-text", title: "This script is not data-driven: it uses its own values" }, "Own values")))))))
          : h("p", { class: "muted-text" }, "Every script of this batch was deleted. Edit the batch and add scripts."),
      );
      const runsCard = h(
        "div",
        { class: "card" },
        h("p", { class: "card-label" }, "Last runs"),
        batch.recent_runs.length
          ? h("div", { class: "progress-steps" }, batch.recent_runs.map((r) => h("div", { class: "row" }, h("a", { class: "row-link cell-key", href: `/projects/${projectId}/test-batch-runs/${r.id}`, "data-link": "" }, r.key), resultPill(r.status), h("span", { class: "muted-text small" }, countsText(r.counts)), h("span", { class: "muted-text small" }, seconds(r.duration_seconds)), h("span", { class: "muted-text small", style: "margin-left:auto" }, formatDate(r.created_at)))))
          : h("p", { class: "muted-text" }, "Not run yet. Use Run batch to run it on a runner."),
      );
      const content = h(
        "div",
        {},
        h("div", { class: "detail-head" }, h("a", { class: "back", href: `/projects/${projectId}/test-batches`, "data-link": "", "aria-label": "Back to the test batches" }, "‹"), h("h1", {}, batch.name), h("div", { class: "pills" }, failurePill(batch.on_failure))),
        running ? h("div", { class: "job-card running", role: "status" }, h("div", { class: "job-head" }, h("span", { class: "spinner", "aria-hidden": "true" }), h("strong", {}, "This batch is running"), h("button", { class: "link", type: "button", onclick: () => runBatch(project, batch, { job: running, onFinished: reload }) }, "Follow it"))) : null,
        h(
          "div",
          { class: "detail-grid bt-grid" },
          h("div", {}, stepsCard, runsCard),
          h(
            "aside",
            { class: "side-card" },
            h("h3", {}, "Details"),
            h(
              "div",
              { class: "side-body" },
              h("div", { class: "kv" }, h("span", { class: "k" }, "ID"), h("span", { class: "v cell-key" }, batch.key)),
              h("div", { class: "kv" }, h("span", { class: "k" }, "When a script fails"), h("span", { class: "v" }, FAILURE_LABEL[batch.on_failure])),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Scripts"), h("span", { class: "v" }, String(batch.items.length))),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Created"), h("span", { class: "v" }, formatDate(batch.created_at))),
              h("div", { class: "kv" }, h("span", { class: "k" }, "Last updated"), h("span", { class: "v" }, formatDate(batch.updated_at))),
              batch.description ? h("div", { class: "kv-block" }, h("span", { class: "k" }, "Description"), h("span", { class: "v" }, batch.description)) : null,
            ),
          ),
        ),
      );
      root.replaceChildren(projectFrame(project, "test-batches", content, { trail: [{ label: batch.key }], actions: h("div", { class: "toolbar-right" }, runBtn, edit, menu) }));
    }
    draw();
  })();
  return root;
}

// ---------------------------------------------------------------- the batch run result
export function testBatchRunView(projectId, runId) {
  const root = h("div", { class: "fill" }, h("div", {}, "Loading…"));
  (async () => {
    let project;
    let r;
    try {
      [project, r] = await Promise.all([get(`/api/projects/${projectId}`), get(`/api/projects/${projectId}/test-batch-runs/${runId}`)]);
    } catch (err) {
      root.replaceChildren(h("a", { class: "back-link", href: `/projects/${projectId}/test-batches`, "data-link": "" }, "← Test batches"), h("div", { class: "banner-error", style: "margin-top:16px" }, err.status === 404 ? "This batch run was not found." : errorText(err)));
      return;
    }
    document.title = `${r.key} — TestMax`;
    const stopped = r.items.find((i) => i.status === "failed");
    const rows = r.items.map((i) =>
      h(
        "tr",
        { class: i.status === "failed" ? "bt-row-failed" : "" },
        h("td", {}, String(i.position)),
        h("td", { class: "cell-key" }, i.script_id ? h("a", { class: "id-link", href: `/projects/${projectId}/test-scripts/${i.script_id}`, "data-link": "" }, i.script_key) : i.script_key),
        h("td", { class: "cell-strong" }, i.title, i.status === "failed" && i.error ? h("div", { class: "bt-err" }, errorLines(i.error)) : null),
        h("td", {}, dvText(i) || h("span", { class: "muted-text", title: "Not a data-driven script: it used its own values" }, "Own values")),
        h("td", {}, resultPill(i.status), i.status === "not_run" ? null : h("div", { class: "muted-text small" }, seconds(i.duration_seconds))),
        h("td", { class: "cell-key" }, i.run_id ? h("a", { class: "row-link", href: `/projects/${projectId}/test-runs/${i.run_id}`, "data-link": "", "aria-label": `View the report of ${i.script_key}, position ${i.position}` }, i.run_key) : h("span", { class: "muted-text small" }, "—")),
      ),
    );
    const content = h(
      "div",
      {},
      h("div", { class: "detail-head" }, h("a", { class: "back", href: r.batch_id ? `/projects/${projectId}/test-batches/${r.batch_id}` : `/projects/${projectId}/test-batches`, "data-link": "", "aria-label": "Back to the batch" }, "‹"), h("h1", {}, `${r.batch_name}`), h("div", { class: "pills" }, resultPill(r.status))),
      r.status === "failed" && (stopped || r.error)
        ? h("div", { class: "banner-error bt-banner" }, stopped ? `${stopped.script_key} (position ${stopped.position}) failed.${r.on_failure === "stop" && r.counts.not_run ? ` The batch stops at a failed script, so ${r.counts.not_run} later script${r.counts.not_run === 1 ? " was" : "s were"} not run.` : r.on_failure === "continue" ? " The batch continued with the next scripts, as set." : ""}` : r.error)
        : null,
      r.status === "aborted" ? h("div", { class: "flagged", style: "margin-bottom:16px" }, "The run was stopped before it finished. The scripts that had not run are shown as not run.") : null,
      h(
        "div",
        { class: "detail-grid bt-grid" },
        h(
          "div",
          {},
          h("div", { class: "card" }, h("p", { class: "card-label" }, `Scripts (${r.total}): ${countsText(r.counts)}`), h("div", { class: "table-wrap" }, h("table", { class: "data-table bt-table" }, h("thead", {}, h("tr", {}, ["#", "Script", "Title", "Data variation", "Result", "Report"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", {}, rows)))),
          h("div", { class: "card" }, h("p", { class: "card-label" }, "Console output of the whole batch"), h("pre", { class: "log-box" }, r.log || "(no output)")),
        ),
        h(
          "aside",
          { class: "side-card" },
          h("h3", {}, "Details"),
          h(
            "div",
            { class: "side-body" },
            h("div", { class: "kv" }, h("span", { class: "k" }, "Batch run"), h("span", { class: "v cell-key" }, r.key)),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Result"), h("span", { class: "v" }, RESULT_LABEL[r.status] || r.status)),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Batch"), h("span", { class: "v cell-key" }, r.batch_id ? h("a", { class: "row-link", href: `/projects/${projectId}/test-batches/${r.batch_id}`, "data-link": "" }, r.batch_key) : `${r.batch_key} (deleted)`)),
            h("div", { class: "kv" }, h("span", { class: "k" }, "When a script fails"), h("span", { class: "v" }, FAILURE_LABEL[r.on_failure])),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Duration"), h("span", { class: "v" }, seconds(r.duration_seconds))),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Started"), h("span", { class: "v" }, r.started_at ? formatDate(r.started_at) : "—")),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Browser"), h("span", { class: "v" }, r.headed ? "Chrome (visible), one session" : "Chrome (headless), one session")),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Runner"), h("span", { class: "v" }, r.runner || "—")),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Run by"), h("span", { class: "v" }, r.executed_by || "—")),
          ),
        ),
      ),
    );
    root.replaceChildren(projectFrame(project, "test-batches", content, { trail: [r.batch_id ? { label: r.batch_key, href: `/projects/${projectId}/test-batches/${r.batch_id}` } : { label: r.batch_key }, { label: r.key }] }));
  })();
  return root;
}
