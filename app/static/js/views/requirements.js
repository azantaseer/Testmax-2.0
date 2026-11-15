import { ApiError, get, patch, post } from "../api.js";
import { collapsibleCard, confirmDialog, errorText, modal, pagination, sideCard, toast } from "../components.js";
import { savedRows, emptyState, filterPopover, listToolbar, searchBox, skeletonTable, tableBlock } from "../table.js";
import { h, icon } from "../dom.js";
import { confirmAndDelete, rowSelection } from "../selection.js";
import { PROVIDER_LABEL, addGuard, navigate, state } from "../store.js";
import { scanModal, scansPanel } from "./scan.js";
import { projectFrame } from "./projectFrame.js";
import { generateCasesModal, testCasesPanel } from "./testCases.js";
import { addTagsDialog, removeTagsDialog, tagChip, tagList } from "./tags.js";

const SOURCE_LABEL = { system: "system default", tenant: "your organization's choice", user: "your own setting" };
const MAX_BYTES = 10 * 1024 * 1024;

function formatDate(iso) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "2-digit", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function modelLabel(provider, model) {
  return `${PROVIDER_LABEL[provider] || provider} · ${model}`;
}

/** Upload dialog. Calls onStarted(job) once the server queued the job. */
function generateModal(project, onStarted) {
  const ai = state.me.ai;
  const FORMATS = [["Word (.docx)", ".docx"], ["PDF (.pdf)", ".pdf"], ["Markdown (.md)", ".md"], ["CSV / TSV (.csv, .tsv)", ".csv,.tsv"], ["Text (.txt)", ".txt"]];
  const fileInput = h("input", { class: "dz-input", type: "file", id: "m-file", accept: FORMATS.map((f) => f[1]).join(","), "aria-describedby": "dz-hint" });
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const submit = h("button", { class: "btn btn-primary btn-md", type: "submit", disabled: true }, icon("sparkle"), "Generate Requirements");
  const emptyZone = h(
    "div",
    { class: "dz-empty" },
    h("span", { class: "dz-icon" }, icon("upload")),
    h("div", { class: "dz-title" }, "Click to upload"),
    h("div", { class: "dz-sub" }, "or drag and drop"),
    h("div", { class: "dz-hint", id: "dz-hint" }, "Maximum 10 MB per file."),
  );
  const chosenZone = h("div", { class: "dz-chosen", hidden: true });
  const zone = h("label", { class: "dropzone", for: "m-file", tabindex: "0" }, fileInput, emptyZone, chosenZone);
  const form = h(
    "form",
    { class: "upload-form", novalidate: true },
    h(
      "div",
      { class: "upload-scroll" },
      h("p", { class: "modal-text", style: "margin:0 0 20px" }, "Upload a requirements document for ", h("strong", { class: "upload-project" }, project.name), "."),
      h("div", { class: "field" }, h("span", { class: "label" }, "Document", h("span", { class: "req" }, "*")), zone),
      banner,
      h("div", { class: "upload-formats" }, h("strong", {}, "Supported Formats:"), h("div", { class: "format-chips" }, FORMATS.map((f) => h("span", { class: "format-chip" }, f[0])))),
      h(
        "div",
        { class: "ai-card" },
        h("strong", {}, "AI Processing"),
        h("p", {}, "We analyze tables and prose in your document to generate structured requirements (clear titles, overview, and testable acceptance criteria), one requirement ticket for each requirement it finds. Each one can then be scanned and turned into test cases."),
        h("div", { class: "effective" }, ai.provider ? `AI model: ${modelLabel(ai.provider, ai.model)} (${SOURCE_LABEL[ai.source]})` : "AI model: none selected yet. Choose one under Organization AI Model (Super Admin) or My Settings first."),
      ),
    ),
    h("div", { class: "upload-footer" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), submit),
  );
  const m = modal({ title: "Upload Document", content: form, width: 560 });
  m.dialog.classList.add("upload-modal");

  const fail = (text) => {
    banner.textContent = text;
    banner.hidden = !text;
  };
  function setFile(file) {
    fail("");
    if (file && !/\.(pdf|docx|txt|md|csv|tsv)$/i.test(file.name)) {
      fileInput.value = "";
      file = null;
      fail("Use a Word (.docx), PDF, Markdown, CSV, TSV or TXT file.");
    } else if (file && file.size > MAX_BYTES) {
      fileInput.value = "";
      file = null;
      fail("The file is larger than 10 MB.");
    }
    emptyZone.hidden = !!file;
    chosenZone.hidden = !file;
    zone.classList.toggle("has-file", !!file);
    submit.disabled = !file;
    if (file) {
      const size = file.size < 1024 * 1024 ? `${Math.max(1, Math.round(file.size / 1024))} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`;
      chosenZone.replaceChildren(h("span", { class: "dz-icon small" }, icon("doc")), h("div", { class: "dz-file" }, h("strong", {}, file.name), h("span", { class: "muted-text" }, size)), h("span", { class: "dz-change" }, "Click to change"));
    }
  }
  fileInput.addEventListener("change", () => setFile(fileInput.files[0] || null));
  zone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.add("dragging");
    });
  }
  for (const type of ["dragleave", "drop"]) zone.addEventListener(type, () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    try {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      fileInput.files = transfer.files; // keeps the input and the form in step
    } catch {
      /* an old browser: the file is still checked below, and the picker works */
    }
    setFile(file);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    fail("");
    const file = fileInput.files[0];
    if (!file) return fail("Choose a document first.");
    submit.disabled = true;
    submit.replaceChildren("Uploading…");
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(`/api/projects/${project.id}/requirements/generate`, { method: "POST", body, credentials: "same-origin" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new ApiError(response.status, data.error?.code || "error", data.error?.message || "Upload failed.");
      m.close();
      onStarted(data);
    } catch (err) {
      submit.disabled = false;
      submit.replaceChildren(icon("sparkle"), "Generate Requirements");
      fail(errorText(err));
    }
  });
}

export function requirementsSection(project) {
  const q = { page: 1, pageSize: savedRows(25), search: "", filters: {} };
  const jobsHost = h("div", { class: "job-stack" });
  const tableHost = h("div", { class: "table-host fill" });
  const polling = new Set();
  const tagAfter = () => {
    selection.clear();
    loadTable();
  };
  const addTagBtn = h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => addTagsDialog({ project, ids: selection.ids(), vocabulary, onDone: tagAfter }) }, icon("tag"), "Add tag");
  const removeTagBtn = h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => removeTagsDialog({ project, ids: selection.ids(), vocabulary, onDone: tagAfter }) }, "Remove tag");
  const selection = rowSelection({
    extra: [addTagBtn, removeTagBtn],
    url: `/api/projects/${project.id}/requirements/delete`,
    singular: "requirement",
    plural: "requirements",
    consequence: ["Its scans, test cases, test scripts and executions are deleted with it.", "Their scans, test cases, test scripts and executions are deleted with them."],
    onDone: () => loadTable(),
  });

  function jobCard(job) {
    const running = job.status === "queued" || job.status === "running";
    const failed = job.status === "failed";
    const card = h("div", { class: `job-card ${failed ? "failed" : job.stalled ? "stalled" : running ? "running" : "done"}`, role: "status" });
    const title = failed ? "Requirement generation failed" : job.stalled ? "Waiting for a worker" : running ? "Generating requirements…" : "Requirements generated";
    card.append(
      h("div", { class: "job-head" }, running ? h("span", { class: "spinner", "aria-hidden": "true" }) : null, h("strong", {}, title)),
      h("div", { class: "job-meta" }, `${job.filename || "document"} · ${modelLabel(job.provider, job.model)}`),
      failed ? h("div", { class: "job-error" }, job.error || "The job failed.") : h("div", { class: "job-progress" }, job.progress || ""),
    );
    if (failed) {
      card.append(h("button", { class: "link", type: "button", onclick: () => card.remove() }, "Dismiss"));
    }
    return card;
  }

  function showJob(job) {
    const existing = jobsHost.querySelector(`[data-job="${job.id}"]`);
    const card = jobCard(job);
    card.dataset.job = job.id;
    if (existing) existing.replaceWith(card);
    else jobsHost.prepend(card);
  }

  async function follow(job) {
    showJob(job);
    if (polling.has(job.id)) return;
    polling.add(job.id);
    let current = job;
    while ((current.status === "queued" || current.status === "running") && jobsHost.isConnected) {
      await new Promise((r) => setTimeout(r, 1500));
      if (!jobsHost.isConnected) break;
      try {
        current = await get(`/api/jobs/${job.id}`);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) break;
        continue; // a hiccup: keep following
      }
      showJob(current);
    }
    polling.delete(job.id);
    if (current.status === "succeeded") {
      toast(`Generated ${current.result.requirement_count} requirement${current.result.requirement_count === 1 ? "" : "s"}.`);
      setTimeout(() => jobsHost.querySelector(`[data-job="${job.id}"]`)?.remove(), 6000);
      q.page = 1;
      loadTable();
    }
  }

  const HEADERS = ["", "ID", "Summary", "Source", "Tags", "Scanned", "Generated by", "Created"];
  let vocabulary = []; // the tags used in this project, with counts

  function scanPill(scan) {
    if (!scan) return h("span", { class: "pill scanned" }, "Not scanned");
    if (scan.status === "COMPLETE") return h("span", { class: "pill scanned" }, "Scanned");
    if (scan.status === "PARTIAL") return h("span", { class: "tpill running" }, "Partial scan");
    return h("span", { class: "tpill failed" }, "Scan failed");
  }

  function sourceCell(r) {
    if (!r.source) return h("span", { class: "muted-text", title: "The document this requirement came from was removed" }, "—");
    return h(
      "button",
      { class: "src-chip", type: "button", title: `${r.source.filename} — show only this document`, onclick: () => filter.apply({ ...filter.value(), document: [r.source.id] }) },
      icon("file"),
      h("span", { class: "src-name" }, r.source.filename),
    );
  }

  function queryString() {
    const qs = new URLSearchParams({ page: q.page, page_size: q.pageSize });
    if (q.search.trim()) qs.set("q", q.search.trim());
    const f = q.filters;
    (f.scan || []).forEach((v) => qs.append("scan", v));
    (f.model || []).forEach((v) => qs.append("model", v));
    (f.document || []).forEach((v) => qs.append("document", v));
    (f.tag || []).forEach((v) => qs.append("tag", v));
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
    loadTable();
  }

  async function loadTable() {
    try {
      const data = await get(`/api/projects/${project.id}/requirements?${queryString()}`);
      if (!data.requirements.length && data.total > 0 && q.page > 1) {
        q.page = Math.ceil(data.total / q.pageSize); // the last page was emptied by a delete
        return loadTable();
      }
      filter.setOptions("model", data.models.map((m) => ({ value: m, label: m })));
      const nameCount = {};
      data.documents.forEach((d) => { nameCount[d.filename] = (nameCount[d.filename] || 0) + 1; });
      filter.setOptions("document", data.documents.map((d) => ({ value: d.id, label: nameCount[d.filename] > 1 ? `${d.filename} (uploaded ${formatDate(d.uploaded_at)})` : d.filename })));
      vocabulary = data.tags;
      filter.setOptions("tag", data.tags.map((t) => ({ value: t.name, label: `${t.name} (${t.count})` })));
      selection.begin();
      const narrowed = !!(q.search.trim() || filter.count());
      if (!data.requirements.length) {
        tableHost.replaceChildren(
          narrowed
            ? emptyState({ title: "No requirements match", text: "Try a different search or clear the filters.", actions: [h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: clearAll }, "Clear search and filters")] })
            : emptyState({
                title: "No requirements created yet",
                text: "Upload a business requirements document and TestMax writes the requirements for you.",
                actions: [h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => generateModal(project, follow) }, icon("plus"), "Generate Requirements")],
              }),
        );
        selection.done();
        return;
      }
      const rows = data.requirements.map((r) =>
        h(
          "tr",
          {},
          selection.box(r.id, r.key),
          h("td", { class: "cell-key" }, h("a", { class: "id-link", href: `/projects/${project.id}/requirements/${r.id}`, "data-link": "" }, r.key)),
          h("td", { class: "cell-strong cell-title" }, h("a", { class: "row-link", title: r.summary, href: `/projects/${project.id}/requirements/${r.id}`, "data-link": "" }, r.summary)),
          h("td", {}, sourceCell(r)),
          h("td", {}, tagList(r.tags, (t) => filter.apply({ ...filter.value(), tag: [t] }))),
          h("td", {}, scanPill(r.scan)),
          h("td", {}, r.model ? modelLabel(r.provider, r.model) : "—"),
          h("td", {}, formatDate(r.created_at)),
        ),
      );
      tableHost.replaceChildren(
        tableBlock(h("tr", {}, selection.head(), ...HEADERS.slice(1).map((t) => h("th", { scope: "col" }, t))), rows),
        pagination({
          page: data.page,
          pageSize: data.page_size,
          total: data.total,
          onPage: (p) => {
            q.page = p;
            loadTable();
          },
          onPageSize: (n) => {
            q.pageSize = n;
            q.page = 1;
            loadTable();
          },
        }),
      );
      selection.done();
    } catch (err) {
      tableHost.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load requirements.")));
    }
  }

  async function resume() {
    try {
      const { jobs } = await get(`/api/projects/${project.id}/jobs?limit=5`);
      const latest = jobs[0];
      jobs.filter((j) => j.status === "queued" || j.status === "running").forEach(follow);
      if (latest && latest.status === "failed") showJob(latest);
    } catch {
      /* the table still works without this */
    }
  }

  const search = searchBox({
    placeholder: "Search by summary or description…",
    label: "Search requirements",
    onSearch: (text) => {
      selection.clear(); // a changed search must not leave hidden rows selected
      q.search = text;
      q.page = 1;
      loadTable();
    },
  });
  const filter = filterPopover({
    fields: [
      { key: "scan", label: "Scanned", type: "options", options: [{ value: "scanned", label: "Scanned" }, { value: "not_scanned", label: "Not scanned" }] },
      { key: "document", label: "Source", type: "options", options: [] },
      { key: "tag", label: "Tags", type: "options", options: [] },
      { key: "model", label: "Generated by", type: "options", options: [] },
      { key: "created", label: "Created", type: "date" },
    ],
    onApply: (value) => {
      selection.clear();
      q.filters = value;
      q.page = 1;
      loadTable();
    },
  });

  const action = h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => generateModal(project, follow) }, icon("plus"), "Generate Requirements");
  const root = h("div", { class: "fill" }, jobsHost, listToolbar(search.el, filter.el), filter.chips, selection.bar, tableHost);
  tableHost.replaceChildren(skeletonTable(HEADERS));
  loadTable();
  resume();
  return { el: root, action };
}


/** Small, safe renderer for the AI's plain-text sections: "## Heading", "- bullet", "1. step", paragraphs. */
function renderDoc(text) {
  const box = h("div", { class: "doc-box rich" });
  let list = null;
  let listType = "";
  const flush = () => {
    list = null;
    listType = "";
  };
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      flush();
      continue;
    }
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    const bullet = line.match(/^\s*[-*\u2022]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (heading) {
      flush();
      box.append(h("h3", {}, heading[1]));
    } else if (bullet || numbered) {
      const type = bullet ? "ul" : "ol";
      if (!list || listType !== type) {
        list = h(type, {});
        listType = type;
        box.append(list);
      }
      list.append(h("li", {}, (bullet || numbered)[1]));
    } else {
      flush();
      box.append(h("p", {}, line));
    }
  }
  return box;
}

export function requirementDetailView(projectId, requirementId) {
  const root = h("div", { class: "fill" }, h("div", {}, "Loading…"));
  (async () => {
    let project;
    let req;
    try {
      [project, req] = await Promise.all([get(`/api/projects/${projectId}`), get(`/api/projects/${projectId}/requirements/${requirementId}`)]);
    } catch (err) {
      root.replaceChildren(
        h("a", { class: "back-link", href: `/projects/${projectId}`, "data-link": "" }, "← Back to project"),
        h("div", { class: "banner-error", style: "margin-top:16px" }, err.status === 404 ? "This requirement was not found." : errorText(err)),
      );
      return;
    }
    document.title = `${req.key} \u2014 TestMax`;
    const cases = testCasesPanel(project, req);
    const panel = scansPanel(project, req, () => {
      /* a finished scan changes what Generate Test Cases can use; the dialog asks the server each time */
    });
    let editing = false;
    const head = h("div", { class: "detail-head" });
    const actionsHost = h("div", { class: "toolbar-right" });
    const body = h("div", {});
    const side = h("div", {});

    function metaLine() {
      const made = `Generated ${formatDate(req.created_at)}${req.model ? ` by ${modelLabel(req.provider, req.model)}` : ""}`;
      return req.edited ? `${made} · Edited ${formatDate(req.updated_at)}` : made;
    }

    /** The tags of this requirement as chips with an x, and a button to add more. */
    function tagsEditor() {
      const reload = async () => {
        try {
          req = await get(`/api/projects/${project.id}/requirements/${req.id}`);
        } catch {
          /* keep what is shown */
        }
        drawHead();
      };
      const addMore = async () => {
        let vocabulary = [];
        try {
          vocabulary = (await get(`/api/projects/${project.id}/requirements?page_size=1`)).tags;
        } catch {
          /* the dialog works without suggestions */
        }
        addTagsDialog({ project, ids: [req.id], vocabulary, onDone: reload });
      };
      return h(
        "div",
        { class: "tag-row" },
        req.tags.map((t) => tagChip(t, { onRemove: async () => {
          try {
            await post(`/api/projects/${project.id}/requirements/tags`, { ids: [req.id], remove: [t] });
            reload();
          } catch (err) {
            toast(errorText(err), "error");
          }
        } })),
        h("button", { class: "tag-add", type: "button", onclick: addMore }, icon("plus"), req.tags.length ? h("span", { class: "sr-only" }, "Add tag") : "Add tag"),
      );
    }

    function drawHead() {
      head.replaceChildren(
        h("a", { class: "back", href: `/projects/${project.id}`, "data-link": "", "aria-label": "Back to requirements" }, icon("arrowLeft")),
        h("h1", {}, req.summary),
      );
      actionsHost.replaceChildren(
        ...[
          h("button", { class: "btn btn-secondary btn-md", type: "button", disabled: editing, onclick: () => { editing = true; draw(); } }, icon("pencil"), "Edit"),
          h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => scanModal(project, req, panel.follow) }, icon("search"), "Scan App"),
          h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => generateCasesModal(project, req, cases.follow) }, icon("sparkle"), "Generate Test Cases"),
          h("button", { class: "btn btn-outline-danger btn-md icon-only", type: "button", "aria-label": "Delete requirement", title: "Delete requirement", onclick: async () => {
            const result = await confirmAndDelete({ url: `/api/projects/${project.id}/requirements/delete`, items: [{ id: req.id, key: req.key }], singular: "requirement", plural: "requirements", consequence: ["Its scans, test cases, test scripts and executions are deleted with it.", "Their scans, test cases, test scripts and executions are deleted with them."] });
            if (result && result.deleted.length) navigate(`/projects/${project.id}`);
          } }, icon("trash")),
        ],
      );
      side.replaceChildren(
        sideCard("Details", [
          ["ID", req.key],
          ["Generated", formatDate(req.created_at)],
          ["Generated by", req.model ? modelLabel(req.provider, req.model) : "—"],
          ["Edited", req.edited ? formatDate(req.updated_at) : null],
          ["Latest scan", req.scan ? (req.scan.status === "COMPLETE" ? "Scanned" : req.scan.status === "PARTIAL" ? "Partial scan" : "Scan failed") : "Not scanned"],
          ["Source", req.source ? req.source.filename : null],
          ["Tags", tagsEditor()],
        ]),
      );
    }

    function editForm() {
      const summary = h("input", { class: "input", id: "e-summary", maxlength: "300", value: req.summary });
      const description = h("textarea", { class: "input edit-area", id: "e-description", rows: "16", maxlength: "60000" });
      const criteria = h("textarea", { class: "input edit-area", id: "e-criteria", rows: "10", maxlength: "60000" });
      description.value = req.description;
      criteria.value = req.acceptance_criteria;
      const banner = h("div", { class: "banner-error", role: "alert", hidden: true, style: "text-align:left" });
      const save = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Save changes");
      const dirty = () => summary.value !== req.summary || description.value !== req.description || criteria.value !== req.acceptance_criteria;
      const release = addGuard(() => {
        if (!form.isConnected) {
          release();
          return false;
        }
        return dirty();
      });
      const cancel = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: async () => {
        if (dirty() && !(await confirmDialog({ title: "Discard your changes?", message: "The changes you made to this requirement have not been saved.", confirmLabel: "Discard", danger: true }))) return;
        release();
        editing = false;
        draw();
      } }, "Cancel");
      const field = (label, id, input, hint) => h("div", { class: "field" }, h("label", { class: "label", for: id }, label), input, hint ? h("div", { class: "field-hint" }, hint) : null);
      const form = h(
        "form",
        { class: "edit-form", novalidate: true },
        field("Summary", "e-summary", summary),
        field("Description", "e-description", description, "Plain text. Start a line with ## for a heading, - for a bullet, 1. for a numbered step."),
        field("Acceptance Criteria", "e-criteria", criteria),
        h("div", { class: "notice" }, "Saving changes this requirement only. Scans and test cases already made are not changed; scan again and generate test cases again to use the new text."),
        banner,
        h("div", { class: "edit-actions" }, cancel, save),
      );
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        banner.hidden = true;
        if (!summary.value.trim()) {
          banner.textContent = "The summary cannot be empty.";
          banner.hidden = false;
          return summary.focus();
        }
        if (!description.value.trim()) {
          banner.textContent = "The description cannot be empty.";
          banner.hidden = false;
          return description.focus();
        }
        save.disabled = true;
        save.textContent = "Saving…";
        try {
          req = await patch(`/api/projects/${project.id}/requirements/${req.id}`, { version: req.version, summary: summary.value, description: description.value, acceptance_criteria: criteria.value });
          release();
          editing = false;
          toast("Requirement saved.");
          draw();
        } catch (err) {
          save.disabled = false;
          save.textContent = "Save changes";
          banner.textContent = errorText(err);
          banner.hidden = false;
        }
      });
      setTimeout(() => summary.focus(), 0);
      return form;
    }

    function draw() {
      drawHead();
      body.replaceChildren(
        ...(editing
          ? [h("section", { class: "card" }, h("p", { class: "card-label" }, "Edit requirement"), editForm())]
          : [collapsibleCard("Description", renderDoc(req.description)), collapsibleCard("Acceptance Criteria", req.acceptance_criteria.trim() ? renderDoc(req.acceptance_criteria) : h("p", { class: "muted-text" }, "None written."))]),
      );
      panel.el.hidden = editing;
      cases.el.hidden = editing;
    }

    const content = h("div", {}, head, h("div", { class: "detail-grid" }, body, side), panel.el, cases.el);
    draw();
    root.replaceChildren(projectFrame(project, "requirements", content, { trail: [{ label: req.key }], actions: actionsHost }));
  })();
  return root;
}
