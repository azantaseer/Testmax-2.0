import { ApiError } from "./api.js";
import { h, icon } from "./dom.js";

export function errorText(err, fallback = "Something went wrong. Please try again.") {
  return err instanceof ApiError ? err.message : fallback;
}

// ---------- toast ----------
export function toast(message, kind = "ok") {
  let host = document.getElementById("toasts");
  if (!host) {
    host = h("div", { id: "toasts", class: "toasts", "aria-live": "polite" });
    document.body.append(host);
  }
  const el = h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" }, h("span", { class: "toast-mark", "aria-hidden": "true" }, kind === "error" ? "!" : "\u2713"), h("span", {}, message));
  host.append(el);
  setTimeout(() => el.remove(), 4000);
}

// ---------- modal ----------
const openDialogs = [];

/** What is typed into a form's fields, as one string: compare two of them to know if the person changed anything. */
export function dirtyTracker(form) {
  const snap = () => JSON.stringify([...form.querySelectorAll("input, textarea, select")].map((el) => (el.type === "checkbox" || el.type === "radio" ? el.checked : el.value)));
  const initial = snap();
  return () => snap() !== initial;
}

export function modal({ title, content, width = 480, onClose = null }) {
  const previous = document.activeElement;
  let dirtyFn = null;
  let closed = false;
  const dialog = h(
    "div",
    { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title, style: `max-width:${width}px` },
    h(
      "div",
      { class: "modal-head" },
      h("h2", { class: "modal-title" }, title),
      h("button", { class: "modal-x", type: "button", "aria-label": "Close", onclick: () => requestClose() }, "\u00d7"),
    ),
    h("div", { class: "modal-body" }, content),
  );
  const backdrop = h("div", { class: "modal-backdrop" }, dialog);
  function close() {
    if (closed) return;
    closed = true;
    const i = openDialogs.indexOf(backdrop);
    if (i >= 0) openDialogs.splice(i, 1);
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
    if (previous && previous.focus) previous.focus();
    if (onClose) onClose();
  }
  /** Close from the X, Escape or a click outside: asks first when the form holds unsaved changes. */
  function requestClose() {
    if (dirtyFn && dirtyFn()) {
      confirmDialog({ title: "Discard your changes?", message: "What you typed has not been saved.", confirmLabel: "Discard", danger: true }).then((ok) => {
        if (ok) close();
      });
    } else close();
  }
  function onKey(event) {
    if (event.key === "Escape" && openDialogs[openDialogs.length - 1] === backdrop) requestClose();
  }
  backdrop.addEventListener("mousedown", (event) => {
    if (event.target === backdrop && openDialogs[openDialogs.length - 1] === backdrop) requestClose();
  });
  document.addEventListener("keydown", onKey);
  openDialogs.push(backdrop);
  document.body.append(backdrop);
  const first = dialog.querySelector("input, select, textarea, button.btn");
  if (first) first.focus();
  return { close, dialog, setDirty: (fn) => { dirtyFn = fn; } };
}

export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      m.close();
      resolve(value);
    };
    const ok = h("button", { class: `btn btn-md ${danger ? "btn-danger" : "btn-primary"}`, type: "button", onclick: () => done(true) }, confirmLabel);
    const cancel = h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => done(false) }, "Cancel");
    const m = modal({ title, content: [h("p", { class: "modal-text" }, message), h("div", { class: "modal-actions" }, cancel, ok)], onClose: () => done(false) });
    cancel.focus();
  });
}

// ---------- labelled field for forms in modals/pages ----------
export function formField({ name, label, type = "text", placeholder = "", autocomplete = "off", value = "", required = true, hint = null }) {
  const input = h("input", { class: "input", id: `m-${name}`, name, type, placeholder, autocomplete, value, maxlength: "200" });
  const field = h(
    "div",
    { class: "field" },
    h("label", { class: "label", for: `m-${name}` }, label, required ? h("span", { class: "req", "aria-hidden": "true" }, " *") : null),
    input,
    hint ? h("div", { class: "hint" }, hint) : null,
  );
  return { field, input };
}

export function fieldError(field, message) {
  field.querySelectorAll(".field-error").forEach((e) => e.remove());
  const input = field.querySelector(".input");
  if (input) input.classList.add("invalid");
  field.append(h("div", { class: "field-error", role: "alert" }, message));
  if (input) {
    input.focus();
    input.addEventListener(
      "input",
      () => {
        field.querySelectorAll(".field-error").forEach((e) => e.remove());
        input.classList.remove("invalid");
      },
      { once: true },
    );
  }
}

// ---------- pagination footer (matches the prod list pages) ----------
export function pagination({ page, pageSize, total, onPage, onPageSize }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const prev = h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: page <= 1, onclick: () => onPage(page - 1) }, "Previous");
  const next = h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: page >= pages, onclick: () => onPage(page + 1) }, "Next");
  const chips = [];
  const shown = new Set([1, pages, page - 1, page, page + 1]);
  let last = 0;
  for (let p = 1; p <= pages; p++) {
    if (!shown.has(p)) continue;
    if (p - last > 1) chips.push(h("span", { class: "page-gap", "aria-hidden": "true" }, "…"));
    chips.push(
      h("button", { class: `page-chip${p === page ? " current" : ""}`, type: "button", "aria-current": p === page ? "page" : null, onclick: () => onPage(p) }, String(p)),
    );
    last = p;
  }
  const select = h("select", { class: "input rows-select", "aria-label": "Rows per page" }, ...[10, 25, 50].map((n) => h("option", { value: n, selected: n === pageSize }, String(n))));
  select.addEventListener("change", () => {
    try {
      localStorage.setItem("tm.rows", JSON.stringify(Number(select.value))); // remembered for every list in this browser
    } catch {
      /* storage blocked: the choice still applies to this page */
    }
    onPageSize(Number(select.value));
  });
  return h(
    "div",
    { class: "pager" },
    prev,
    h("div", { class: "page-chips" }, chips),
    h("div", { class: "pager-right" }, h("span", { class: "rows-label" }, "Rows per page:"), select, next),
  );
}

/** Underline tabs (settings pages). tabs: [{ key, label, panel }]; shows one panel at a time. Arrow keys, Home and End move between tabs. */
export function underlineTabs(tabs, initial = tabs[0].key) {
  const list = h("div", { class: "utabs", role: "tablist" });
  const body = h("div", { class: "utab-panels" });
  const buttons = new Map();
  function show(key, focus = false) {
    for (const t of tabs) {
      const on = t.key === key;
      const b = buttons.get(t.key);
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      t.panel.hidden = !on;
      if (on && focus) b.focus();
    }
  }
  for (const t of tabs) {
    const b = h("button", { class: "utab", type: "button", role: "tab", id: `utab-${t.key}`, "aria-controls": `upanel-${t.key}`, onclick: () => show(t.key) }, t.label);
    buttons.set(t.key, b);
    list.append(b);
    t.panel.setAttribute("role", "tabpanel");
    t.panel.id = `upanel-${t.key}`;
    t.panel.setAttribute("aria-labelledby", b.id);
    body.append(t.panel);
  }
  list.addEventListener("keydown", (event) => {
    const keys = tabs.map((t) => t.key);
    const i = keys.findIndex((k) => buttons.get(k) === document.activeElement);
    if (i < 0) return;
    const next = event.key === "ArrowRight" ? i + 1 : event.key === "ArrowLeft" ? i - 1 : event.key === "Home" ? 0 : event.key === "End" ? keys.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    show(keys[(next + keys.length) % keys.length], true);
  });
  show(initial);
  return { list, body, show };
}

/** The right-hand "Details" card of a detail page. rows: [[label, value]] (value: text or a node; empty values are skipped). */
export function sideCard(title, rows) {
  return h(
    "section",
    { class: "side-card" },
    h("h3", {}, title),
    h("dl", { class: "side-body", style: "margin:0" }, rows.filter((r) => r[1] !== null && r[1] !== undefined && r[1] !== "").map(([k, v]) => h("div", { class: "kv" }, h("dt", {}, k), h("dd", {}, v)))),
  );
}

/** A collapsible card (open by default): <details> with a label, so long text can be folded away. */
export function collapsibleCard(label, ...children) {
  return h("details", { class: "card collapsible", open: true }, h("summary", { class: "card-label" }, h("span", {}, label), icon("chevronDown")), ...children);
}

/** A "more" (three dots) button with a menu. items: [{ label, onclick, danger?, icon? }]. */
export function moreMenu(items, label = "More actions") {
  const panel = h("div", { class: "menu-panel", role: "menu", hidden: true });
  const btn = h("button", { class: "btn btn-secondary btn-md icon-only", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": label }, icon("more"));
  const wrap = h("div", { class: "more-menu" }, btn, panel);
  const close = () => {
    panel.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  };
  for (const item of items) {
    panel.append(h("button", { class: `menu-item${item.danger ? " danger" : ""}`, role: "menuitem", type: "button", onclick: () => { close(); item.onclick(); } }, item.icon ? icon(item.icon) : null, item.label));
  }
  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    panel.hidden = !panel.hidden;
    btn.setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden) panel.querySelector("button")?.focus();
  });
  const onDoc = (event) => {
    if (!wrap.isConnected) return document.removeEventListener("click", onDoc);
    if (!wrap.contains(event.target)) close();
  };
  document.addEventListener("click", onDoc);
  wrap.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      close();
      btn.focus();
    }
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !panel.hidden) {
      const all = [...panel.querySelectorAll("button")];
      const i = all.indexOf(document.activeElement) + (event.key === "ArrowDown" ? 1 : -1);
      all[(i + all.length) % all.length].focus();
      event.preventDefault();
    }
  });
  return wrap;
}

export function chevron() {
  return icon("chevronDown");
}
