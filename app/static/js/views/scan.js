import { ApiError, get, post } from "../api.js";
import { errorText, modal, toast } from "../components.js";
import { h } from "../dom.js";
import { navigate, PROVIDER_LABEL, state } from "../store.js";
import { runnerChoice } from "./runnerPicker.js";

const SOURCE_LABEL = { system: "system default", tenant: "your organization's choice", user: "your own setting" };

function formatDate(iso) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "2-digit", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export function statusChip(status) {
  const cls = { COMPLETE: "active", PARTIAL: "pending", FAILED: "disabled" }[status] || "";
  const label = { COMPLETE: "Complete", PARTIAL: "Partial", FAILED: "Failed" }[status] || status;
  return h("span", { class: `chip ${cls}` }, label);
}

// ---------------------------------------------------------------- start dialog
export async function scanModal(project, requirement, onStarted) {
  const ai = state.me.ai;
  let runners = { total: 0, online: 0, runners: [] };
  try {
    runners = await get("/api/runners/status");
  } catch {
    /* the server checks again when the scan starts */
  }
  const isAdmin = state.me.user.role !== "user";
  const url = h("input", { class: "input", id: "m-url", type: "url", placeholder: "https://your-test-app.example.com", autocomplete: "off", value: "" });
  const policy = h("select", { class: "input", id: "m-policy" }, h("option", { value: "INTERACTIVE" }, "Autonomous (INTERACTIVE) — log in, fill forms, complete the flow"), h("option", { value: "READ_ONLY" }, "Look only (READ_ONLY) — no typing or data-changing clicks"));
  const auth = h("input", { type: "checkbox", id: "m-auth" });
  const authRow = h("label", { class: "check-row auth-check", for: "m-auth" }, auth, "I confirm this URL is an authorized test environment");
  const summary = h("input", { class: "input", id: "m-summary", value: requirement.summary });
  const description = h("textarea", { class: "input mono", id: "m-desc", rows: "8" });
  description.value = requirement.description;
  const criteria = h("textarea", { class: "input mono", id: "m-ac", rows: "4" });
  criteria.value = requirement.acceptance_criteria;
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const submit = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Start Scan");
  policy.addEventListener("change", () => {
    authRow.hidden = policy.value !== "INTERACTIVE";
  });

  const choice = runnerChoice(runners);
  const runnerLine = runners.online
    ? h("div", { class: "runner-line ok" }, `● ${runners.online} runner${runners.online === 1 ? "" : "s"} online`)
    : h("div", { class: "runner-line off" }, "● No runner is online. ", isAdmin ? h("a", { href: "/runners", "data-link": "", onclick: () => m.close() }, "Connect a runner") : "Ask an admin to connect one.");

  const form = h(
    "form",
    { class: "form-stack tight", novalidate: true },
    h("p", { class: "modal-text", style: "margin:0" }, "A runner opens a browser, and the AI explores your application following this requirement. It records every page, control and locator in an App Map."),
    runnerLine,
    choice.el,
    h("div", { class: "field" }, h("label", { class: "label", for: "m-url" }, "Application URL", h("span", { class: "req" }, " *")), url),
    h("div", { class: "field" }, h("label", { class: "label", for: "m-policy" }, "Scan mode"), policy),
    authRow,
    h(
      "details",
      { class: "details-box" },
      h("summary", {}, "Requirement text used by the scan (editable, e.g. add a test login)"),
      h("div", { class: "field" }, h("label", { class: "label", for: "m-summary" }, "Summary"), summary),
      h("div", { class: "field" }, h("label", { class: "label", for: "m-desc" }, "Description"), description),
      h("div", { class: "field" }, h("label", { class: "label", for: "m-ac" }, "Acceptance criteria"), criteria),
    ),
    h("div", { class: "effective" }, ai.provider ? `AI model: ${PROVIDER_LABEL[ai.provider]} \u00b7 ${ai.model} (${SOURCE_LABEL[ai.source]})` : "AI model: none selected yet. Choose one under Organization AI Model (Super Admin) or My Settings first."),
    banner,
    h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), submit),
  );
  const m = modal({ title: "Scan App", content: form, width: 640 });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    banner.hidden = true;
    const fail = (text) => {
      banner.textContent = text;
      banner.hidden = false;
    };
    if (!/^https?:\/\/\S+$/i.test(url.value.trim())) return fail("Enter the application URL, starting with http:// or https://");
    if (policy.value === "INTERACTIVE" && !auth.checked) return fail("Confirm that this URL is an authorized test environment, or choose the look-only mode.");
    submit.disabled = true;
    submit.textContent = "Starting…";
    try {
      const job = await post(`/api/projects/${project.id}/requirements/${requirement.id}/scan`, {
        application_url: url.value.trim(),
        scan_policy: policy.value,
        test_environment_authorized: auth.checked,
        summary: summary.value,
        description: description.value,
        acceptance_criteria: criteria.value,
        runner_id: choice.value(),
      });
      choice.remember();
      m.close();
      onStarted({ ...job, filename: requirement.key, provider: ai.provider, model: ai.model });
    } catch (err) {
      submit.disabled = false;
      submit.textContent = "Start Scan";
      fail(errorText(err));
    }
  });
}

// ---------------------------------------------------------------- live progress card + list of scans for one requirement
export function scansPanel(project, requirement, onChanged) {
  const jobsHost = h("div", { class: "job-stack" });
  const listHost = h("div", { class: "table-host" });
  const polling = new Set();

  function card(job) {
    const active = job.status === "queued" || job.status === "running";
    const cls = job.status === "failed" ? "failed" : job.stalled ? "stalled" : active ? "running" : "done";
    const title = job.status === "failed" ? "Scan failed" : job.stalled ? "Waiting for the runner" : active ? "Scanning your application…" : "Scan finished";
    const el = h("div", { class: `job-card ${cls}`, role: "status", "data-job": job.id });
    el.append(
      h("div", { class: "job-head" }, active ? h("span", { class: "spinner", "aria-hidden": "true" }) : null, h("strong", {}, title)),
      h("div", { class: "job-meta" }, `${job.filename || requirement.key} · ${PROVIDER_LABEL[job.provider] || job.provider} · ${job.model}`),
      job.status === "failed" ? h("div", { class: "job-error" }, job.error || "The scan failed.") : h("div", { class: "job-progress" }, job.progress || ""),
    );
    if (active) {
      el.append(
        h(
          "button",
          {
            class: "link",
            type: "button",
            onclick: async () => {
              try {
                await post(`/api/jobs/${job.id}/cancel`);
                toast("Cancelling the scan…");
              } catch (err) {
                toast(errorText(err), "error");
              }
            },
          },
          "Cancel scan",
        ),
      );
    } else if (job.status === "failed") {
      el.append(h("button", { class: "link", type: "button", onclick: () => el.remove() }, "Dismiss"));
    }
    return el;
  }

  function show(job) {
    const existing = jobsHost.querySelector(`[data-job="${job.id}"]`);
    const el = card(job);
    if (existing) existing.replaceWith(el);
    else jobsHost.prepend(el);
  }

  async function follow(job) {
    show(job);
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
        continue;
      }
      show(current);
    }
    polling.delete(job.id);
    if (current.status === "succeeded") {
      toast("Scan finished.");
      setTimeout(() => jobsHost.querySelector(`[data-job="${job.id}"]`)?.remove(), 5000);
      loadList();
      onChanged();
    } else if (current.status === "failed") {
      loadList();
      onChanged();
    }
  }

  async function loadList() {
    try {
      const { scans } = await get(`/api/projects/${project.id}/scans?requirement_id=${requirement.id}`);
      listHost.replaceChildren(
        scans.length
          ? h(
              "div",
              { class: "table-wrap" },
              h(
                "table",
                { class: "data-table" },
                h("thead", {}, h("tr", {}, ...["Scanned", "Status", "Pages", "Controls", "Tool calls", "Duration", "Model", ""].map((t) => h("th", { scope: "col" }, t)))),
                h(
                  "tbody",
                  {},
                  scans.map((s) =>
                    h(
                      "tr",
                      {},
                      h("td", {}, formatDate(s.created_at)),
                      h("td", {}, statusChip(s.status), s.status !== "COMPLETE" && s.stop_reason ? h("div", { class: "muted-text small", style: "max-width:360px;margin-top:4px", title: s.stop_reason }, s.stop_reason) : null),
                      h("td", {}, String(s.pages)),
                      h("td", {}, String(s.controls)),
                      h("td", {}, String(s.tool_call_count)),
                      h("td", {}, s.duration_seconds != null ? `${s.duration_seconds}s` : "—"),
                      h("td", {}, `${PROVIDER_LABEL[s.provider] || s.provider} · ${s.model}`),
                      h("td", { class: "cell-actions" }, h("a", { class: "btn btn-secondary btn-sm", href: `/projects/${project.id}/scans/${s.id}`, "data-link": "" }, "View App Map")),
                    ),
                  ),
                ),
              ),
            )
          : h("div", { class: "muted-text", style: "font-size:14px" }, "Not scanned yet. Use Scan App to map your application for this requirement."),
      );
    } catch (err) {
      listHost.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load scans.")));
    }
  }

  async function resume() {
    try {
      const { jobs } = await get(`/api/projects/${project.id}/jobs?type=app_scan&limit=10`);
      jobs.filter((j) => j.status === "queued" || j.status === "running").forEach((j) => follow({ ...j, filename: requirement.key }));
    } catch {
      /* the list still works */
    }
  }

  const root = h("div", {}, h("h2", { class: "section-heading" }, "Scans"), jobsHost, h("div", { style: "margin-top:12px" }, listHost));
  loadList();
  resume();
  return { el: root, follow };
}

// ---------------------------------------------------------------- scan result page
export function scanResultView(projectId, scanId) {
  const root = h("div", {}, h("div", {}, "Loading…"));
  (async () => {
    let s;
    let project;
    try {
      [project, s] = await Promise.all([get(`/api/projects/${projectId}`), get(`/api/projects/${projectId}/scans/${scanId}`)]);
    } catch (err) {
      root.replaceChildren(h("a", { class: "back-link", href: `/projects/${projectId}`, "data-link": "" }, "← Back to project"), h("div", { class: "banner-error", style: "margin-top:16px" }, err.status === 404 ? "This scan was not found." : errorText(err)));
      return;
    }
    document.title = `App Map — TestMax`;
    const back = s.requirement_id ? `/projects/${projectId}/requirements/${s.requirement_id}` : `/projects/${projectId}`;
    const am = s.app_map;
    const meta = [
      ["Application", s.application_url],
      ["Mode", s.scan_policy],
      ["AI model", `${PROVIDER_LABEL[s.provider] || s.provider} · ${s.model}`],
      ["Duration", s.duration_seconds != null ? `${s.duration_seconds}s` : "—"],
      ["Tool calls", String(s.tool_call_count)],
      ["Pages / controls", `${s.pages} / ${s.controls}`],
      ["Schema issues", String(s.issues)],
    ];
    root.replaceChildren(
      h("a", { class: "back-link", href: back, "data-link": "" }, `← ${s.requirement_key || "Requirement"}`),
      h("div", { class: "page-head", style: "margin-top:8px" }, h("div", {}, h("h1", { class: "page-title" }, `App Map${s.requirement_key ? ` — ${s.requirement_key}` : ""}`), h("p", { class: "page-subtitle" }, formatDate(s.created_at))), statusChip(s.status)),
      h("div", { class: "doc-box", style: "margin-top:16px" }, h("strong", {}, "Result: "), s.stop_reason || "\u2014"),
      (s.notes || []).length ? h("div", { class: "notice", style: "margin-top:12px" }, (s.notes || []).join(" ")) : null,
      h("dl", { class: "meta-grid" }, ...meta.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      am ? appMapSection(am) : h("div", { class: "banner-error", style: "margin-top:20px" }, "No App Map was produced. The tool-call history below shows what happened."),
      s.schema_issues.length ? listSection("Schema notes", s.schema_issues) : null,
      historySection(s.history),
      s.raw ? h("details", { class: "details-box wide" }, h("summary", {}, "Raw AI answer (JSON)"), h("pre", { class: "code-box" }, am ? JSON.stringify(am, null, 2) : s.raw)) : null,
    );
  })();
  return root;
}

function listSection(title, items) {
  return h("div", {}, h("h2", { class: "section-heading" }, title), h("ul", { class: "plain-list" }, items.map((i) => h("li", {}, typeof i === "string" ? i : JSON.stringify(i)))));
}

function appMapSection(am) {
  const pages = (am.pages || []).map((p, index) => {
    const rows = (p.controls || []).map((c) => {
      const tech = c.technical || {};
      const locators = (tech.locatorCandidates || []).map((l) => h("div", { class: "locator" }, h("span", { class: `chip ${l.verification === "VERIFIED" ? "active" : ""}` }, l.verification === "VERIFIED" ? "verified" : "unverified"), " ", h("code", {}, l.locator)));
      return h("tr", {}, h("td", { class: "cell-strong" }, c.humanName || "—"), h("td", {}, c.type || tech.role || "—"), h("td", { class: "cell-key" }, c.elementId || ""), h("td", {}, locators.length ? locators : "—"));
    });
    return h(
      "details",
      { class: "details-box wide", open: index === 0 },
      h("summary", {}, `${p.name || "Page"} — ${p.url || ""} (${(p.controls || []).length} controls)`),
      h("div", { class: "table-wrap" }, h("table", { class: "data-table" }, h("thead", {}, h("tr", {}, ...["Control", "Type", "Element ID", "Locator candidates"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: "4", class: "empty-cell" }, "No controls recorded.")))))
    );
  });
  const actions = (am.meaningfulActions || []).map((a) => `${a.sequence}. ${a.action || ""} — ${a.evidenceStatus || ""}${a.observedOutcome ? `: ${a.observedOutcome}` : ""}`);
  return h(
    "div",
    {},
    h("h2", { class: "section-heading" }, "Pages and controls"),
    h("div", { class: "stack" }, pages.length ? pages : h("div", { class: "muted-text" }, "No pages recorded.")),
    actions.length ? listSection("Actions performed", actions) : null,
    (am.observedBehaviors || []).length ? listSection("Observed behaviour", am.observedBehaviors.map((b) => (typeof b === "string" ? b : b.behavior || b.description || JSON.stringify(b)))) : null,
    (am.gaps || []).length ? listSection("Gaps (not explored)", am.gaps) : null,
  );
}

function historySection(history) {
  if (!history || !history.length) return null;
  const rows = history.map((e) => h("tr", {}, h("td", { class: "cell-key" }, String(e.sequence)), h("td", {}, e.tool + (e.allowed === false ? " (blocked)" : "")), h("td", { class: "mono-cell" }, JSON.stringify(e.arguments || {}).slice(0, 160)), h("td", { class: "mono-cell" }, String(e.result || "").slice(0, 200))));
  return h("details", { class: "details-box wide" }, h("summary", {}, `Tool-call history (${history.length}; passwords are redacted)`), h("div", { class: "table-wrap" }, h("table", { class: "data-table" }, h("thead", {}, h("tr", {}, ...["#", "Tool", "Arguments", "Result"].map((t) => h("th", { scope: "col" }, t)))), h("tbody", {}, rows))));
}

export function goToScanResult(projectId, scanId) {
  navigate(`/projects/${projectId}/scans/${scanId}`);
}
