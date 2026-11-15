import { ApiError, get, post } from "../api.js";
import { errorText, modal, toast } from "../components.js";
import { h } from "../dom.js";
import { navigate } from "../store.js";
import { formatDate, formatDay, projectFrame } from "./projectFrame.js";
import { runnerChoice } from "./runnerPicker.js";
import { statusPill } from "./testScripts.js";
import { setLabel } from "./dataSets.js";

const RUN_LABEL = { passed: "Passed", failed: "Failed", skipped: "Skipped", aborted: "Aborted" };

function seconds(value) {
  return value == null ? "—" : `${Number(value).toFixed(1)}s`;
}

// ---------------------------------------------------------------- Execute: start it and follow it in a modal
/**
 * Starts an execution of a script (or follows one that is already running when `job` is given) and shows its progress.
 * onFinished(job) is called when the run ended (the run itself is in job.result when it succeeded).
 */
function askRunner(script, choice, sets = []) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      m.close();
      resolve(value);
    };
    const run = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "▷ Execute");
    const boxes = [];
    const picker = sets.length
      ? h(
          "fieldset",
          { class: "field dv-fieldset" },
          h("legend", { class: "label" }, "Data variations to run"),
          h("div", { class: "check-list" }, [{ id: "default", label: setLabel(0) }, ...sets.map((s, i) => ({ id: s.id, label: setLabel(i + 1, s) }))].map((o, i) => {
            const box = h("input", { type: "checkbox", value: o.id, checked: i === 0, id: `dv-${o.id}` });
            boxes.push(box);
            return h("label", { class: "check-row", for: `dv-${o.id}` }, box, o.label);
          })),
          h("div", { class: "field-hint" }, "Each one you tick is run as its own execution with that variation's values."),
        )
      : null;
    const warn = h("div", { class: "field-error", role: "alert", hidden: true }, "Choose at least one data variation.");
    const form = h(
      "form",
      { class: "form-stack tight", novalidate: true },
      h("p", { class: "modal-text", style: "margin:0" }, `${script.key} · ${script.title}`),
      picker,
      warn,
      choice.el,
      h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => done(null) }, "Cancel"), run),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const chosen = boxes.filter((b) => b.checked).map((b) => b.value);
      if (sets.length && !chosen.length) {
        warn.hidden = false;
        return;
      }
      choice.remember();
      done({ runnerId: choice.value(), dataSets: sets.length ? chosen : null });
    });
    const m = modal({ title: "Execute test script", content: form, width: 520, onClose: () => done(null) });
  });
}

/** Several variations: one execution each, shown in one list that updates until all have ended. */
async function executeMany(project, script, runnerId, dataSets, onFinished) {
  const list = h("div", { class: "progress-steps" });
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const actions = h("div", { class: "modal-actions" });
  const heading = h("strong", {}, "Starting…");
  const body = h("div", {}, h("div", { class: "job-head" }, h("span", { class: "spinner", "aria-hidden": "true" }), heading), h("div", { class: "job-meta", style: "margin-top:4px" }, `${script.key} · ${script.title}`), list, banner, actions);
  const m = modal({ title: "Execute test script", content: body, width: 560 });
  const closeBtn = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Close");
  let started;
  try {
    started = await post(`/api/projects/${project.id}/test-scripts/${script.id}/execute`, { runner_id: runnerId, data_sets: dataSets });
  } catch (err) {
    body.querySelector(".spinner")?.remove();
    heading.textContent = "Cannot execute yet";
    banner.textContent = errorText(err);
    banner.hidden = false;
    actions.append(closeBtn);
    return;
  }
  const rows = started.jobs.map((job) => {
    const state = h("span", { class: "muted-text small" }, "Waiting…");
    const link = h("span", {});
    const el = h("div", { class: "row" }, h("span", { class: "dot" }), h("span", { style: "flex:1" }, job.label || "Run"), state, link);
    list.append(el);
    return { job, state, link, el, done: false };
  });
  const cancelAll = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: async () => {
    cancelAll.disabled = true;
    for (const r of rows.filter((x) => !x.done)) await post(`/api/jobs/${r.job.id}/cancel`).catch(() => {});
  } }, "Cancel the rest");
  actions.append(cancelAll);
  heading.textContent = `Running ${rows.length} data variations…`;
  while (m.dialog.isConnected && rows.some((r) => !r.done)) {
    for (const r of rows.filter((x) => !x.done)) {
      try {
        const j = await get(`/api/jobs/${r.job.id}`);
        if (j.status === "queued") r.state.textContent = "Waiting for a runner…";
        else if (j.status === "running") r.state.textContent = j.progress || "Running…";
        else {
          r.done = true;
          const dot = r.el.querySelector(".dot");
          if (j.status === "succeeded") {
            dot.classList.add(j.result.status === "passed" ? "passed" : "failed");
            r.state.replaceChildren(statusPill(j.result.status));
            r.link.append(" ", h("a", { class: "row-link", href: `/projects/${project.id}/test-runs/${j.result.run_id}`, "data-link": "", onclick: () => m.close() }, j.result.run_key));
          } else {
            dot.classList.add("failed");
            r.state.textContent = j.error || "Did not finish";
          }
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) r.done = true;
      }
    }
    if (rows.some((r) => !r.done)) await new Promise((res) => setTimeout(res, 1500));
  }
  if (!m.dialog.isConnected) return;
  body.querySelector(".spinner")?.remove();
  cancelAll.remove();
  heading.textContent = "All variations finished";
  actions.append(closeBtn);
  onFinished(started);
}

export async function executeScript(project, script, { job = null, onFinished = () => {} } = {}) {
  // With two or more runners the person may pick the one that should run it.
  let runnerId = null;
  let dataSets = null; // null = just the script's own values (DV-1)
  if (!job) {
    let status = null;
    try {
      status = await get("/api/runners/status");
    } catch {
      /* the server checks again when the run starts */
    }
    let sets = [];
    if (script.data_driven) {
      try {
        sets = (await get(`/api/projects/${project.id}/test-cases/${script.test_case_id}`)).data.sets;
      } catch {
        /* run with the default values */
      }
    }
    const choice = runnerChoice(status, { forScripts: true });
    if (choice.choosable || sets.length) {
      const picked = await askRunner(script, choice, sets);
      if (!picked) return;
      runnerId = picked.runnerId;
      dataSets = picked.dataSets;
    }
    if (dataSets && dataSets.length > 1) return executeMany(project, script, runnerId, dataSets, onFinished);
  }
  const spinner = h("span", { class: "spinner", "aria-hidden": "true" });
  const title = h("strong", {}, "Starting…");
  const progress = h("div", { class: "job-progress" }, "");
  const steps = h("div", { class: "progress-steps" });
  const elapsed = h("span", { class: "muted-text small" }, "");
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const actions = h("div", { class: "modal-actions" });
  const body = h(
    "div",
    {},
    h("div", { class: "job-head" }, spinner, title, h("span", { style: "flex:1" }), elapsed),
    h("div", { class: "job-meta", style: "margin-top:4px" }, `${script.key} · ${script.title}`),
    progress,
    steps,
    banner,
    actions,
  );
  let current = job;
  let cancelled = false;
  const m = modal({ title: "Execute test script", content: body, width: 560 });
  const closeBtn = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Close");
  const cancelBtn = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: async () => {
    cancelBtn.disabled = true;
    cancelBtn.textContent = "Cancelling…";
    cancelled = true;
    try {
      await post(`/api/jobs/${current.id}/cancel`);
    } catch (err) {
      toast(errorText(err), "error");
    }
  } }, "Cancel run");
  actions.append(cancelBtn);

  if (!current) {
    try {
      current = await post(`/api/projects/${project.id}/test-scripts/${script.id}/execute`, { runner_id: runnerId, data_sets: dataSets });
    } catch (err) {
      spinner.remove();
      title.textContent = "Cannot execute yet";
      banner.textContent = errorText(err);
      banner.hidden = false;
      cancelBtn.remove();
      actions.append(closeBtn);
      if (err instanceof ApiError && err.code === "no_runner_online") banner.append(" ", h("a", { href: "/runners", "data-link": "", onclick: () => m.close() }, "Open Runners"));
      return;
    }
  }

  const seen = [];
  const started = Date.now();
  title.textContent = "Running the script…";
  let tick = 0;
  let targetOffline = false;
  while (m.dialog.isConnected) {
    elapsed.textContent = `${Math.round((Date.now() - started) / 1000)}s`;
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
    if (current.status === "queued" || current.status === "running") {
      const waiting = target ? `Waiting for "${target.name}" to pick it up…${targetOffline ? " It looks offline. Start it, or cancel and choose another runner." : ""}` : "Waiting for the runner to pick it up…";
      progress.textContent = current.stalled ? "Waiting for the runner…" : current.status === "queued" ? waiting : current.progress || "";
      if (current.status === "running" && current.progress && /^Finished: /.test(current.progress) && !seen.includes(current.progress)) {
        seen.push(current.progress);
        steps.append(h("div", { class: "row" }, h("span", { class: "dot passed" }), current.progress.replace("Finished: ", "")));
      }
    } else {
      break;
    }
    await new Promise((r) => setTimeout(r, 1000));
    if (!m.dialog.isConnected) break;
    try {
      current = await get(`/api/jobs/${current.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) break;
    }
  }
  if (!m.dialog.isConnected && (current.status === "queued" || current.status === "running")) return; // the user closed the window; the run goes on
  spinner.remove();
  cancelBtn.remove();
  progress.textContent = "";
  if (current.status === "succeeded") {
    const status = current.result.status;
    title.replaceChildren(`${current.result.run_key} `, statusPill(status));
    progress.textContent = cancelled || status === "aborted" ? "The run was stopped." : status === "passed" ? "The script passed." : status === "skipped" ? "The script skipped itself." : "The script failed. The report shows which step.";
    actions.append(closeBtn, h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => { m.close(); navigate(`/projects/${project.id}/test-runs/${current.result.run_id}`); } }, "View Report"));
  } else {
    title.textContent = current.error === "Cancelled by the user." ? "Cancelled" : "The run did not finish";
    banner.textContent = current.error || "The job failed.";
    banner.hidden = false;
    actions.append(closeBtn);
  }
  onFinished(current);
}

// ---------------------------------------------------------------- run history card (script viewer)
export function runHistoryCard(project, stats) {
  const card = h("aside", { class: "side-card" }, h("h3", {}, "Run history"));
  const body = h("div", { class: "side-body" });
  body.append(
    h(
      "div",
      { class: "stat-row" },
      h("div", { class: "stat" }, h("div", { class: "k" }, "Total"), h("div", { class: "v" }, String(stats.total))),
      h("div", { class: "stat" }, h("div", { class: "k" }, "Pass rate"), h("div", { class: "v" }, stats.pass_rate == null ? "—" : `${stats.pass_rate}%`)),
      h("div", { class: "stat" }, h("div", { class: "k" }, "Avg time"), h("div", { class: "v" }, stats.avg_seconds == null ? "—" : `${stats.avg_seconds}s`)),
    ),
    h("div", { class: "runbar", role: "img", "aria-label": `Pass rate ${stats.pass_rate ?? 0}%` }, h("span", { style: `width:${stats.pass_rate ?? 0}%` })),
    h("div", { class: "note-line" }, stats.last_run_at ? `Last run recorded ${formatDate(stats.last_run_at)}` : "Not executed yet. Use Execute to run it on a runner."),
  );
  if (stats.recent.length) {
    body.append(
      h(
        "div",
        { class: "progress-steps" },
        stats.recent.map((r) => h("div", { class: "row" }, h("a", { class: "row-link cell-key", href: `/projects/${project.id}/test-runs/${r.id}`, "data-link": "" }, r.key), statusPill(r.status), r.data_set ? h("span", { class: "muted-text small", title: r.data_set.name }, r.data_set.name.split(" \u2014 ")[0]) : null, h("span", { class: "muted-text small" }, seconds(r.duration_seconds)), h("span", { class: "muted-text small", style: "margin-left:auto" }, formatDay(r.created_at)))),
      ),
    );
  }
  card.append(body);
  return card;
}

// ---------------------------------------------------------------- the report
export function testRunView(projectId, runId) {
  const root = h("div", {}, h("div", {}, "Loading…"));
  (async () => {
    let project;
    let r;
    try {
      [project, r] = await Promise.all([get(`/api/projects/${projectId}`), get(`/api/projects/${projectId}/test-runs/${runId}`)]);
    } catch (err) {
      root.replaceChildren(h("a", { class: "back-link", href: `/projects/${projectId}/test-scripts`, "data-link": "" }, "← Test scripts"), h("div", { class: "banner-error", style: "margin-top:16px" }, err.status === 404 ? "This execution was not found." : errorText(err)));
      return;
    }
    document.title = `${r.key} — TestMax`;
    const tc = r.test_case;
    const stepRows = r.steps.map((s, i) =>
      h("div", { class: `step-row ${s.status}` }, h("div", { class: "top" }, h("span", { class: `dot ${s.status}` }), h("span", { class: "name" }, s.title), h("span", { class: "ms" }, `${(s.duration_ms / 1000).toFixed(1)}s`), statusPill(s.status)), s.error ? h("div", { class: "err" }, s.error) : null),
    );
    const notRun = tc && r.steps.length < tc.step_count && r.status !== "passed" ? tc.step_count - r.steps.length : 0;
    const content = h(
      "div",
      {},
      h("div", { class: "detail-head" }, h("a", { class: "back", href: r.script ? `/projects/${projectId}/test-scripts/${r.script.id}` : `/projects/${projectId}/test-scripts`, "data-link": "", "aria-label": "Back to the script" }, "‹"), h("h1", {}, tc ? tc.title : r.key), h("div", { class: "pills" }, statusPill(r.status))),
      r.status === "failed" && r.error ? h("div", { class: "banner-error", style: "text-align:left;margin-bottom:16px;white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px" }, r.error.split("\n").slice(0, 8).join("\n")) : null,
      r.status === "skipped" ? h("div", { class: "flagged", style: "margin-bottom:16px" }, "The script skipped itself, so nothing was tested. This happens when the test case has no real test data (for example a login). Add the data to the requirement, scan again and regenerate.") : null,
      h(
        "div",
        { class: "detail-grid" },
        h(
          "div",
          {},
          h("div", { class: "card" }, h("p", { class: "card-label" }, `Steps (${r.steps.length}${tc ? ` of ${tc.step_count}` : ""})`), r.steps.length ? h("div", { class: "step-list" }, stepRows) : h("p", { class: "muted-text" }, "No step results were recorded."), notRun ? h("div", { class: "note-line" }, `${notRun} later step${notRun === 1 ? " was" : "s were"} not run.`) : null),
          r.artifacts.length
            ? h("div", { class: "card" }, h("p", { class: "card-label" }, "Screenshots"), h("div", { class: "shots" }, r.artifacts.map((a) => h("a", { href: `/api/projects/${projectId}/test-runs/${r.id}/artifacts/${a.id}`, target: "_blank", rel: "noopener" }, h("img", { src: `/api/projects/${projectId}/test-runs/${r.id}/artifacts/${a.id}`, alt: `${a.name} of ${r.key}`, loading: "lazy" })))))
            : null,
          r.data_set && Object.keys(r.data_set.values || {}).length
            ? h("div", { class: "card" }, h("p", { class: "card-label" }, `Data used \u2014 ${r.data_set.name}`), h("div", { class: "table-wrap" }, h("table", { class: "data-table" }, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Key"), h("th", { scope: "col" }, "Value"))), h("tbody", {}, Object.entries(r.data_set.values).map(([k, v]) => h("tr", {}, h("td", { class: "cell-key" }, k), h("td", { class: "wrap" }, v === "" ? h("span", { class: "muted-text" }, "(empty)") : v)))))))
            : null,
          h("div", { class: "card" }, h("p", { class: "card-label" }, "Console output"), h("pre", { class: "log-box" }, r.log || "(no output)")),
        ),
        h(
          "aside",
          { class: "side-card" },
          h("h3", {}, "Details"),
          h(
            "div",
            { class: "side-body" },
            h("div", { class: "kv" }, h("span", { class: "k" }, "Execution"), h("span", { class: "v cell-key" }, r.key)),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Result"), h("span", { class: "v" }, RUN_LABEL[r.status] || r.status)),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Duration"), h("span", { class: "v" }, seconds(r.duration_seconds))),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Started"), h("span", { class: "v" }, r.started_at ? formatDate(r.started_at) : "—")),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Browser"), h("span", { class: "v" }, r.headed ? "Chrome (visible)" : "Chrome (headless)")),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Runner"), h("span", { class: "v" }, r.runner || "—")),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Executed by"), h("span", { class: "v" }, r.executed_by || "—")),
            r.data_set ? h("div", { class: "kv" }, h("span", { class: "k" }, "Data variation"), h("span", { class: "v" }, r.data_set.name)) : null,
            h("div", { class: "kv" }, h("span", { class: "k" }, "Script"), h("span", { class: "v cell-key" }, r.script ? h("a", { class: "row-link", href: `/projects/${projectId}/test-scripts/${r.script.id}`, "data-link": "" }, r.script.key) : "—")),
            h("div", { class: "kv" }, h("span", { class: "k" }, "Test case"), h("span", { class: "v cell-key" }, tc ? h("a", { class: "row-link", href: `/projects/${projectId}/test-cases/${tc.id}`, "data-link": "" }, tc.key) : "—")),
          ),
        ),
      ),
    );
    root.replaceChildren(projectFrame(project, "test-scripts", content, { trail: [r.script ? { label: r.script.key, href: `/projects/${projectId}/test-scripts/${r.script.id}` } : { label: "Script" }, { label: r.key }] }));
  })();
  return root;
}
