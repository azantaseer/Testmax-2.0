import { del, get, post, put } from "../api.js";
import { confirmDialog, errorText, fieldError, formField, modal, toast } from "../components.js";
import { h, icon } from "../dom.js";
import { emptyState, skeletonTable, tableBlock } from "../table.js";
import { state } from "../store.js";

function ago(iso) {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

const SYSTEMS = [["windows", "Windows", "start-runner.bat"], ["macos", "macOS", "start-runner.command"], ["linux", "Linux", "start-runner.sh"]];

/** The address the runner should use, or null when it is not an http(s) origin. */
function cleanServer(value) {
  try {
    const u = new URL(value.trim());
    if (!["http:", "https:"].includes(u.protocol) || u.username || u.password || (u.pathname !== "/" && u.pathname !== "") || u.search || u.hash) return null;
    return u.origin;
  } catch {
    return null;
  }
}

/** Connect a runner: download it, start it, enter the code it shows, wait until it is online. */
function connectModal(onDone) {
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  let system = "windows";
  const server = h("input", { class: "input", id: "w-server", value: location.origin, autocomplete: "off", spellcheck: "false" });
  const serverError = h("div", { class: "field-error", hidden: true });
  const localNote = h(
    "div",
    { class: "notice", hidden: !local, style: "margin-top:10px" },
    h("span", {}, `You opened TestMax as ${location.origin}, which only works on this computer. To connect ANOTHER computer, type this computer's network address instead (for example http://192.168.1.20:8000) and make sure the server accepts connections from your network.`),
  );
  const download = h("a", { class: "btn btn-primary btn-md", id: "w-download", href: "#", download: "" }, "Download runner (.zip)");
  const startFile = h("strong", {}, "start-runner.bat");
  const seg = h(
    "div",
    { class: "seg", role: "radiogroup", "aria-label": "Operating system of the runner computer" },
    SYSTEMS.map(([key, label]) => h("button", { class: "seg-btn", type: "button", role: "radio", "aria-checked": String(key === system), "data-system": key, onclick: () => pick(key) }, label)),
  );
  function pick(key) {
    system = key;
    seg.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.system === key)));
    startFile.textContent = SYSTEMS.find((x) => x[0] === key)[2];
    updateLink();
  }
  function updateLink() {
    const origin = cleanServer(server.value);
    serverError.hidden = !!origin;
    serverError.textContent = origin ? "" : "Enter the address as http://host:port, for example http://192.168.1.20:8000.";
    download.classList.toggle("disabled", !origin);
    download.setAttribute("aria-disabled", String(!origin));
    download.href = origin ? `/api/runners/package?system=${encodeURIComponent(system)}&server=${encodeURIComponent(origin)}` : "#";
    cmd.textContent = `python -m runner pair --server ${origin || "<address>"}\npython -m runner run`;
  }
  const cmd = h("pre", { class: "code-box small" }, "");
  server.addEventListener("input", updateLink);
  download.addEventListener("click", (event) => {
    if (download.classList.contains("disabled")) event.preventDefault();
  });

  const code = formField({ name: "code", label: "Code shown on the runner's setup page", placeholder: "ABCD-EFGH", autocomplete: "off" });
  code.input.style.textTransform = "uppercase";
  const name = formField({ name: "runner-name", label: "Runner name (optional)", placeholder: "e.g. QA laptop", required: false });
  const submit = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Approve runner");
  const status = h("div", { class: "wizard-status", hidden: true, role: "status" });

  const step = (n, title, ...body) => h("section", { class: "wiz-step" }, h("div", { class: "wiz-num" }, String(n)), h("div", { class: "wiz-body" }, h("h3", {}, title), ...body));
  const form = h(
    "form",
    { class: "wizard", novalidate: true },
    step(
      1,
      "Download the runner",
      h("p", { class: "hint" }, "The computer that will open the browser needs Google Chrome, Python 3.11 or newer and Node.js 20 or newer. Everything else is installed by the start file."),
      h("div", { class: "field" }, h("label", { class: "label", for: "w-server" }, "TestMax address the runner will use"), server, serverError, localNote),
      h("div", { class: "field" }, h("span", { class: "label" }, "Operating system of that computer"), seg),
      h("div", { class: "row-actions", style: "margin-top:12px" }, download),
    ),
    step(
      2,
      "Start it",
      h("p", { class: "hint" }, "Unzip the file, then double-click ", startFile, ". The first start installs what it needs (a few minutes, internet required). A setup page then opens in that computer's browser and shows a pairing code."),
    ),
    step(
      3,
      "Enter the code",
      code.field,
      name.field,
      h("div", { class: "row-actions" }, submit),
      status,
    ),
    h("details", { class: "wiz-advanced" }, h("summary", {}, "Prefer the command line?"), h("p", { class: "hint" }, "In the unzipped folder (or any copy of the project), run:"), cmd),
    h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Close")),
  );
  const m = modal({ title: "Connect a runner", content: form, width: 640 });
  m.dialog.classList.add("wizard-modal");
  updateLink();

  let stopWaiting = false;
  m.dialog.querySelector(".modal-x").addEventListener("click", () => { stopWaiting = true; });

  async function waitUntilOnline(runner) {
    status.hidden = false;
    status.className = "wizard-status wait";
    status.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), ` "${runner.name}" is approved. Waiting for it to come online…`);
    for (let i = 0; i < 60 && m.dialog.isConnected && !stopWaiting; i += 1) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const { runners } = await get("/api/runners");
        const found = runners.find((r) => r.id === runner.id);
        if (found && found.online) {
          status.className = "wizard-status ok";
          status.replaceChildren(`✓ "${found.name}" is online and ready for scans and test runs.`);
          onDone();
          return;
        }
      } catch {
        /* a hiccup: keep waiting */
      }
    }
    if (m.dialog.isConnected) {
      status.className = "wizard-status bad";
      status.replaceChildren(`"${runner.name}" has not come online yet. Check that the runner window is still open on that computer; the list on the Runners page updates by itself.`);
      onDone();
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    form.querySelectorAll(".field-error").forEach((e) => e.remove());
    if (code.input.value.trim().length < 8) return fieldError(code.field, "Enter the 8-character code shown on the runner's setup page.");
    submit.disabled = true;
    try {
      const runner = await post("/api/runners/pair", { user_code: code.input.value.trim(), name: name.input.value.trim() });
      toast("Runner approved.");
      code.input.disabled = true;
      name.input.disabled = true;
      submit.textContent = "Approved";
      waitUntilOnline(runner);
    } catch (err) {
      submit.disabled = false;
      fieldError(code.field, errorText(err));
    }
  });
}

/** "Jobs running at the same time": the organization's limit, what runs now, and (for administrators) a way to change it. */
function limitCard() {
  const isAdmin = state.me.user.role !== "user";
  const headline = h("p", { class: "limit-headline" });
  const bar = h("div", { class: "runbar", role: "img" }, h("span", { style: "width:0%" }));
  const live = h("div", { class: "limit-live" });
  const advice = h("div", { class: "notice limit-advice", hidden: true });
  const input = h("input", { class: "input limit-input", id: "limit-input", type: "number", min: "1", step: "1", inputmode: "numeric", "aria-label": "Jobs running at the same time" });
  const save = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Save");
  const reset = h("button", { class: "link", type: "button", hidden: true }, "Use the default");
  const message = h("div", { class: "field-error", hidden: true, role: "alert" });
  let data = null;
  let dirty = false;

  function draw(next) {
    data = next;
    const { limit, running, runners_online: online, runner_slots: slots } = next;
    headline.replaceChildren("Your organization runs up to ", h("strong", {}, String(limit)), ` job${limit === 1 ? "" : "s"} at the same time. This limit is shared by all runners and by the AI jobs (generating requirements, test cases and scripts).`);
    bar.firstChild.style.width = `${Math.min(100, Math.round((running / limit) * 100))}%`;
    bar.setAttribute("aria-label", `${running} of ${limit} running`);
    live.textContent = `Running now: ${running} of ${limit}. Online runners: ${online}, with ${slots} slot${slots === 1 ? "" : "s"} in total.`;
    if (slots > limit) {
      advice.textContent = `Your online runners have ${slots} slots, but only ${limit} jobs run at the same time. ${isAdmin ? "Raise the limit to use all the slots." : "Ask an administrator to raise the limit to use all the slots."}`;
      advice.hidden = false;
    } else if (online && slots < limit) {
      advice.textContent = `Your online runners have only ${slots} slot${slots === 1 ? "" : "s"}, so at most ${slots} test${slots === 1 ? "" : "s"} run at once even though the limit is ${limit}. Add slots in the runner's setup page, or connect another runner.`;
      advice.hidden = false;
    } else {
      advice.hidden = true;
    }
    if (!dirty) input.value = String(limit);
    input.max = String(next.max);
    reset.hidden = !next.custom;
    reset.textContent = `Use the default (${next.default})`;
  }

  async function submit(value) {
    message.hidden = true;
    save.disabled = true;
    try {
      draw(await put("/api/runners/limits", { limit: value }));
      dirty = false;
      toast(value === null ? "Back to the default limit." : "Limit saved. It applies to the next job that starts.");
    } catch (err) {
      message.textContent = errorText(err);
      message.hidden = false;
    }
    save.disabled = false;
  }

  const form = h(
    "form",
    { class: "limit-form", novalidate: true },
    h("label", { class: "label", for: "limit-input" }, "Jobs at the same time"),
    h("div", { class: "row-actions" }, input, save, reset),
    message,
    h("p", { class: "field-hint" }, "A higher number runs more things at once but uses more memory on the runner computers. Jobs already running are never stopped when you lower it."),
  );
  input.addEventListener("input", () => { dirty = true; });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const n = Number(input.value);
    if (!Number.isInteger(n) || n < 1 || n > data.max) {
      message.textContent = `Enter a whole number from 1 to ${data.max}.`;
      message.hidden = false;
      return;
    }
    submit(n);
  });
  reset.addEventListener("click", () => { dirty = false; submit(null); });

  const el = h("section", { class: "card limit-card" }, h("h2", { class: "limit-title" }, "Jobs running at the same time"), headline, bar, live, advice, isAdmin ? form : null);
  return { el, draw };
}

export function runnersView() {
  const host = h("div", { class: "table-host fill" });
  const limits = limitCard();
  async function load() {
    if (!host.isConnected && host.dataset.mounted) return;
    host.dataset.mounted = "1";
    try {
      const [{ runners }, limitData] = await Promise.all([get("/api/runners"), get("/api/runners/limits")]);
      limits.draw(limitData);
      if (!runners.length) {
        host.replaceChildren(emptyState({ iconName: "runner", title: "No runners connected yet", text: "A runner is a computer that opens the browser for scans and test runs. Connect one to get started.", actions: [h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => connectModal(load) }, icon("plus"), "Connect a Runner")] }));
        return;
      }
      host.replaceChildren(
        tableBlock(
          h("tr", {}, ...["Runner", "Status", "Last seen", "Slots", "Running jobs", "Machine", ""].map((t) => h("th", { scope: "col" }, t))),
          runners.map((r) =>
            h(
              "tr",
              {},
              h("td", { class: "cell-strong" }, r.name),
              h("td", {}, h("span", { class: `chip ${r.online ? "active" : "disabled"}` }, r.online ? "Online" : "Offline")),
              h("td", {}, ago(r.last_seen_at)),
              h("td", {}, String(r.capacity)),
              h("td", {}, String(r.running_jobs)),
              h("td", { class: "muted-text" }, [r.info.os, r.info.version && `v${r.info.version}`].filter(Boolean).join(" · ") || "—"),
              h(
                "td",
                { class: "cell-actions" },
                h(
                  "button",
                  {
                    class: "btn btn-secondary btn-sm",
                    type: "button",
                    onclick: async () => {
                      if (!(await confirmDialog({ title: "Revoke runner", message: `Revoke "${r.name}"? It will stop working immediately and must be paired again.`, confirmLabel: "Revoke", danger: true }))) return;
                      try {
                        await del(`/api/runners/${r.id}`);
                        toast("Runner revoked.");
                      } catch (err) {
                        toast(errorText(err), "error");
                      }
                      load();
                    },
                  },
                  "Revoke",
                ),
              ),
            ),
          ),
        ),
      );
    } catch (err) {
      host.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load runners.")));
    }
  }
  const timer = setInterval(() => (host.isConnected ? load() : clearInterval(timer)), 5000);
  const root = h(
    "div",
    { class: "fill" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", { class: "page-title" }, "Runners"), h("p", { class: "page-subtitle" }, "Machines that run browsers for your organization. They connect outward to TestMax; nothing needs to open a port.")), h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => connectModal(load) }, icon("plus"), "Connect a Runner")),
    h("div", { style: "margin-top:24px" }, limits.el),
    h("div", { class: "fill", style: "margin-top:16px" }, host),
  );
  host.replaceChildren(skeletonTable(["Runner", "Status", "Last seen", "Slots", "Running jobs", "Machine", ""], 3));
  load();
  return root;
}
