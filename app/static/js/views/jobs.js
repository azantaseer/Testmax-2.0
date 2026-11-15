import { ApiError, get, post } from "../api.js";
import { errorText, toast } from "../components.js";
import { h } from "../dom.js";
import { PROVIDER_LABEL } from "../store.js";

/**
 * A live progress card for background jobs (polls /api/jobs/{id}). One tracker per page.
 *   titles: { running, done, failed }   label(job) -> small text under the title
 *   onFinished(job)                     called when a followed job ends (succeeded or failed)
 */
export function jobTracker({ titles, label, onFinished = () => {}, cancelable = false, doneText = null }) {
  const host = h("div", { class: "job-stack" });
  const polling = new Set();

  function card(job) {
    const active = job.status === "queued" || job.status === "running";
    const cls = job.status === "failed" ? "failed" : job.stalled ? "stalled" : active ? "running" : "done";
    const title = job.status === "failed" ? titles.failed : job.stalled ? "Waiting for a worker" : active ? titles.running : titles.done;
    const el = h("div", { class: `job-card ${cls}`, role: "status", "data-job": job.id });
    el.append(
      h("div", { class: "job-head" }, active ? h("span", { class: "spinner", "aria-hidden": "true" }) : null, h("strong", {}, title)),
      h("div", { class: "job-meta" }, `${label(job)}${job.model ? ` · ${PROVIDER_LABEL[job.provider] || job.provider} · ${job.model}` : ""}`),
      job.status === "failed"
        ? h("div", { class: "job-error" }, job.error || "The job failed.")
        : h("div", { class: "job-progress" }, job.status === "succeeded" && doneText ? doneText(job) : job.progress || ""),
    );
    if (active && cancelable) {
      el.append(
        h(
          "button",
          {
            class: "link",
            type: "button",
            onclick: async () => {
              try {
                await post(`/api/jobs/${job.id}/cancel`);
                toast("Cancelling…");
              } catch (err) {
                toast(errorText(err), "error");
              }
            },
          },
          "Cancel",
        ),
      );
    } else if (job.status === "failed") {
      el.append(h("button", { class: "link", type: "button", onclick: () => el.remove() }, "Dismiss"));
    }
    return el;
  }

  function show(job) {
    const existing = host.querySelector(`[data-job="${job.id}"]`);
    const el = card(job);
    if (existing) existing.replaceWith(el);
    else host.prepend(el);
  }

  async function follow(job) {
    show(job);
    if (polling.has(job.id)) return;
    polling.add(job.id);
    let current = job;
    while ((current.status === "queued" || current.status === "running") && host.isConnected) {
      await new Promise((r) => setTimeout(r, 1500));
      if (!host.isConnected) break;
      try {
        current = await get(`/api/jobs/${job.id}`);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) break;
        continue;
      }
      show(current);
    }
    polling.delete(job.id);
    if (current.status === "succeeded" || current.status === "failed") {
      if (current.status === "succeeded") setTimeout(() => host.querySelector(`[data-job="${job.id}"]`)?.remove(), 8000);
      onFinished(current);
    }
  }

  /** Pick up jobs that are already running (e.g. after a page reload). */
  async function resume(projectId, type, match = () => true) {
    try {
      const { jobs } = await get(`/api/projects/${projectId}/jobs?type=${type}&limit=20`);
      jobs.filter((j) => (j.status === "queued" || j.status === "running") && match(j)).forEach(follow);
    } catch {
      /* the page still works without this */
    }
  }

  return { el: host, follow, resume };
}
