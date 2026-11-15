import { h } from "../dom.js";

const REMEMBER = "testmax.lastRunner";

function remembered() {
  try {
    return localStorage.getItem(REMEMBER) || "";
  } catch {
    return "";
  }
}

function remember(id) {
  try {
    if (id) localStorage.setItem(REMEMBER, id);
    else localStorage.removeItem(REMEMBER);
  } catch {
    /* a private window: the choice simply is not remembered */
  }
}

/**
 * The "Run on" choice for a scan or an execution.
 * `status` is the answer of GET /api/runners/status. Offline runners (and, for scripts, runners too old to run them) are
 * listed but cannot be picked, so the reason is visible. With fewer than two runners there is nothing to choose.
 * Returns { el, value(), remember(), choosable } where value() is the runner id or null for "any online runner".
 */
export function runnerChoice(status, { forScripts = false } = {}) {
  const runners = status?.runners || [];
  const usable = (r) => r.online && (!forScripts || r.can_run_scripts);
  const describe = (r) => {
    const state = !r.online ? "offline" : forScripts && !r.can_run_scripts ? "online, too old to run scripts" : "online";
    return `${r.name} — ${state} · ${r.capacity} slot${r.capacity === 1 ? "" : "s"}`;
  };
  const select = h(
    "select",
    { class: "input", id: "m-runner", "aria-label": "Run on" },
    h("option", { value: "" }, "Any online runner (the first free one)"),
    runners.map((r) => h("option", { value: r.id, disabled: !usable(r) }, describe(r))),
  );
  const last = remembered();
  if (runners.some((r) => r.id === last && usable(r))) select.value = last;
  const choosable = runners.length >= 2;
  const el = h(
    "div",
    { class: "field", hidden: !choosable },
    h("label", { class: "label", for: "m-runner" }, "Run on"),
    select,
    h("div", { class: "field-hint" }, "Pick one runner to make sure the job runs there. Leave it on Any runner to let the first free one take it."),
  );
  return { el, choosable, value: () => select.value || null, remember: () => remember(select.value) };
}
