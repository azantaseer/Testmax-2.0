// Building blocks shared by every list page: search box, Filter popover, skeleton rows, empty state, table.
// Text is always inserted as text (see dom.js); nothing here builds HTML from data.
import { h, icon } from "./dom.js";

/** Small per-browser memory (rows per page, sort order). Storage may be blocked: then the default is used and nothing breaks. */
export function remembered(key, fallback) {
  try {
    const raw = localStorage.getItem(`tm.${key}`);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}
export function remember(key, value) {
  try {
    localStorage.setItem(`tm.${key}`, JSON.stringify(value));
  } catch {
    /* private window or blocked storage */
  }
}
const ROW_CHOICES = [10, 25, 50];
export function savedRows(fallback) {
  const n = remembered("rows", fallback);
  return ROW_CHOICES.includes(n) ? n : fallback;
}
/** { sort, dir } for one list, checked against the columns that can be sorted. */
export function savedSort(list, fallback, allowed) {
  const v = remembered(`sort.${list}`, null);
  return v && allowed.includes(v.sort) && (v.dir === "asc" || v.dir === "desc") ? v : fallback;
}

/** A 36px search field with a magnifier. Calls onSearch(text) 250 ms after typing stops. */
export function searchBox({ placeholder, label, onSearch, value = "" }) {
  const input = h("input", { class: "input search-input", type: "search", placeholder, "aria-label": label, value });
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => onSearch(input.value), 250);
  });
  return { el: h("div", { class: "search-wrap" }, icon("search"), input), input };
}

/** The search + Filter row that sits under the tab bar. */
export function listToolbar(...children) {
  return h("div", { class: "list-toolbar" }, ...children);
}

/** Grey placeholder rows with the real header, shown while a list loads. */
export function skeletonTable(headers, rows = 8) {
  const widths = [38, 70, 52, 64, 46, 58];
  return h(
    "div",
    { class: "table-wrap", role: "status", "aria-label": "Loading", "aria-busy": "true" },
    h(
      "table",
      { class: "data-table skeleton" },
      h("thead", {}, h("tr", {}, headers.map((t) => h("th", { scope: "col" }, t)))),
      h(
        "tbody",
        {},
        Array.from({ length: rows }, (_, r) =>
          h("tr", {}, headers.map((t, c) => h("td", {}, t === "" ? null : h("span", { class: "sk-bar", style: `width:${widths[(r + c * 2) % widths.length]}%` })))),
        ),
      ),
    ),
  );
}

/** Icon in a green circle, a title, one line and the actions (the UAT's empty state). */
export function emptyState({ title, text, actions = [], iconName = "file" }) {
  return h(
    "div",
    { class: "empty-block" },
    h("span", { class: "empty-icon", "aria-hidden": "true" }, icon(iconName)),
    h("h3", { class: "empty-title" }, title),
    text ? h("p", { class: "empty-text" }, text) : null,
    actions.length ? h("div", { class: "empty-actions" }, ...actions) : null,
  );
}

/** table > thead/tbody in the scrolling bordered frame. `headRow` is a <tr>, `rows` an array of <tr>. */
export function tableBlock(headRow, rows) {
  return h("div", { class: "table-wrap" }, h("table", { class: "data-table" }, h("thead", {}, headRow), h("tbody", {}, rows)));
}

/**
 * Two-pane Filter popover. fields: [{ key, label, type: "options" | "date", options?: [{ value, label }] }].
 * value: { [key]: string[] } for option fields and { [key]: { from, to } } (YYYY-MM-DD) for date fields.
 * onApply(value) is called with a fresh object. Returns { el, setOptions(key, options), setValue(value) }.
 */
export function filterPopover({ fields, value = {}, onApply }) {
  let applied = clone(value);
  let draft = clone(value);
  let current = fields[0].key;
  let optionQuery = "";
  const optionsOf = Object.fromEntries(fields.map((f) => [f.key, f.options || []]));

  const badge = h("span", { class: "filter-count", hidden: true });
  const button = h("button", { class: "btn btn-primary btn-md filter-btn", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false" }, icon("filter"), "Filter", badge);
  const left = h("div", { class: "fp-fields", role: "tablist", "aria-label": "Filter by", "aria-orientation": "vertical" });
  const right = h("div", { class: "fp-values", role: "tabpanel" });
  const count = h("span", { class: "fp-count" });
  const clear = h("button", { class: "link", type: "button" }, "Clear all");
  const apply = h("button", { class: "btn btn-primary btn-sm", type: "button" }, "Apply");
  const panel = h("div", { class: "fp", role: "dialog", "aria-label": "Filter", hidden: true }, h("div", { class: "fp-body" }, left, right), h("div", { class: "fp-foot" }, clear, count, apply));
  const el = h("div", { class: "filter-wrap" }, button, panel);

  const isActive = (v, f) => (f.type === "date" ? !!(v[f.key] && (v[f.key].from || v[f.key].to)) : !!(v[f.key] && v[f.key].length));
  const activeCount = (v) => fields.filter((f) => isActive(v, f)).length;

  function paintBadge() {
    const n = activeCount(applied);
    badge.textContent = String(n);
    badge.hidden = n === 0;
    button.setAttribute("aria-label", n ? `Filter, ${n} active` : "Filter");
  }

  function paintFields() {
    left.replaceChildren(
      ...fields.map((f) =>
        h(
          "button",
          {
            class: "fp-field",
            type: "button",
            role: "tab",
            id: `fp-tab-${f.key}`,
            "aria-selected": String(f.key === current),
            tabindex: f.key === current ? "0" : "-1",
            onclick: () => {
              current = f.key;
              optionQuery = "";
              paint();
            },
          },
          f.label,
          isActive(draft, f) ? h("span", { class: "fp-dot", title: "In use" }) : null,
        ),
      ),
    );
    right.setAttribute("aria-labelledby", `fp-tab-${current}`);
  }

  function paintValues() {
    const f = fields.find((x) => x.key === current);
    if (f.type === "date") {
      const range = draft[f.key] || { from: "", to: "" };
      const set = (part, v) => {
        draft[f.key] = { ...(draft[f.key] || { from: "", to: "" }), [part]: v };
        paintFooter();
        paintFieldsOnly();
      };
      right.replaceChildren(
        h(
          "div",
          { class: "fp-dates" },
          h("label", { class: "label", for: "fp-from" }, `${f.label}: from`),
          h("input", { class: "input fp-date", id: "fp-from", type: "date", value: range.from || "", max: range.to || null, onchange: (e) => set("from", e.target.value) }),
          h("label", { class: "label", for: "fp-to" }, "to (inclusive)"),
          h("input", { class: "input fp-date", id: "fp-to", type: "date", value: range.to || "", min: range.from || null, onchange: (e) => set("to", e.target.value) }),
        ),
      );
      return;
    }
    const all = optionsOf[f.key];
    const selected = new Set(draft[f.key] || []);
    const shown = all.filter((o) => o.label.toLowerCase().includes(optionQuery.toLowerCase()));
    const search = h("input", { class: "input fp-search", type: "search", placeholder: `Search ${f.label}`, "aria-label": `Search ${f.label}`, value: optionQuery });
    const list = h("div", { class: "fp-options" });
    const setSelected = (next) => {
      draft[f.key] = [...next];
      paintFooter();
      paintFieldsOnly();
    };
    const row = (label, checked, onChange, extra = {}) => {
      const cb = h("input", { class: "cb", type: "checkbox", checked, onchange: (e) => onChange(e.target.checked), ...extra });
      return h("label", { class: "fp-option" }, cb, h("span", {}, label));
    };
    function paintList() {
      if (!all.length) {
        list.replaceChildren(h("p", { class: "fp-none" }, "Nothing to choose from yet."));
        return;
      }
      const allOn = shown.length > 0 && shown.every((o) => selected.has(o.value));
      list.replaceChildren(
        row("All", allOn, (on) => {
          for (const o of shown) on ? selected.add(o.value) : selected.delete(o.value);
          setSelected(selected);
          paintList();
        }),
        ...shown.map((o) =>
          row(o.label, selected.has(o.value), (on) => {
            on ? selected.add(o.value) : selected.delete(o.value);
            setSelected(selected);
            paintList();
          }),
        ),
        ...(shown.length ? [] : [h("p", { class: "fp-none" }, "No match.")]),
      );
    }
    search.addEventListener("input", () => {
      optionQuery = search.value;
      shown.length = 0;
      shown.push(...all.filter((o) => o.label.toLowerCase().includes(optionQuery.toLowerCase())));
      paintList();
    });
    paintList();
    right.replaceChildren(search, list);
  }

  function paintFooter() {
    const f = fields.find((x) => x.key === current);
    count.textContent = f.type === "date" ? "" : `${(draft[f.key] || []).length} of ${optionsOf[f.key].length}`;
  }
  function paintFieldsOnly() {
    const focused = document.activeElement?.id;
    paintFields();
    if (focused) document.getElementById(focused)?.focus();
  }
  function paint() {
    paintFields();
    paintValues();
    paintFooter();
  }

  function open() {
    draft = clone(applied);
    optionQuery = "";
    paint();
    panel.hidden = false;
    button.setAttribute("aria-expanded", "true");
    right.querySelector("input")?.focus();
  }
  function close(refocus = true) {
    panel.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (refocus) button.focus();
  }
  function commit(next) {
    applied = clean(next, fields);
    paintBadge();
    close();
    onApply(clone(applied));
  }
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    panel.hidden ? open() : close();
  });
  apply.addEventListener("click", () => commit(draft));
  clear.addEventListener("click", () => commit({}));
  el.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      event.stopPropagation();
      close();
    }
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && event.target.classList.contains("fp-field")) {
      event.preventDefault();
      const i = fields.findIndex((f) => f.key === current) + (event.key === "ArrowDown" ? 1 : -1);
      const next = fields[(i + fields.length) % fields.length];
      current = next.key;
      optionQuery = "";
      paint();
      document.getElementById(`fp-tab-${current}`)?.focus();
    }
  });
  const onDocClick = (event) => {
    if (!el.isConnected) return document.removeEventListener("click", onDocClick);
    if (!panel.hidden && !el.contains(event.target)) close(false);
  };
  document.addEventListener("click", onDocClick);
  paintBadge();

  return {
    el,
    setOptions(key, options) {
      optionsOf[key] = options;
      if (!panel.hidden && current === key) paintValues();
    },
    setValue(next) {
      applied = clean(next, fields);
      paintBadge();
    },
    count: () => activeCount(applied),
  };
}

function clone(v) {
  return JSON.parse(JSON.stringify(v));
}

/** Drops empty entries so "nothing chosen" and "no filter" are the same thing. */
function clean(v, fields) {
  const out = {};
  for (const f of fields) {
    const x = v[f.key];
    if (f.type === "date") {
      if (x && (x.from || x.to)) out[f.key] = { from: x.from || "", to: x.to || "" };
    } else if (x && x.length) out[f.key] = [...x];
  }
  return out;
}
