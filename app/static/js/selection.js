// Select rows and delete them, shared by the Requirements, Test Cases and Test Scripts lists and the detail pages.
import { post } from "./api.js";
import { confirmDialog, errorText, modal, toast } from "./components.js";
import { h } from "./dom.js";

function preview(keys) {
  return keys.length <= 6 ? keys.join(", ") : `${keys.slice(0, 6).join(", ")} and ${keys.length - 6} more`;
}

function showKept(blocked, plural) {
  const close = h("button", { class: "btn btn-primary btn-md", type: "button" }, "OK");
  const m = modal({
    title: "Some items were kept",
    content: [
      h("p", { class: "modal-text" }, `These ${plural} are still being worked on, so they were not deleted. Try again when the work has ended.`),
      h("ul", { class: "kept-list" }, blocked.map((b) => h("li", {}, h("strong", {}, b.key), " — ", b.reason))),
      h("div", { class: "modal-actions" }, close),
    ],
    width: 520,
  });
  close.addEventListener("click", () => m.close());
}

/**
 * Asks, deletes through `url` (POST {ids}) and reports. Returns the server's answer, or null when cancelled or failed.
 * items: [{ id, key }]. `consequence` is [text for one item, text for several] saying what else disappears with it/them.
 */
export async function confirmAndDelete({ url, items, singular, plural, consequence }) {
  const many = items.length > 1;
  const subject = many ? `${items.length} ${plural}` : `${items[0].key}`;
  const ok = await confirmDialog({
    title: `Delete ${subject}?`,
    message: `${many ? `This deletes ${preview(items.map((i) => i.key))}.` : `This deletes the ${singular} ${items[0].key}.`} ${many ? consequence[1] : consequence[0]} This cannot be undone.`,
    confirmLabel: many ? `Delete ${items.length}` : "Delete",
    danger: true,
  });
  if (!ok) return null;
  let result;
  try {
    result = await post(url, { ids: items.map((i) => i.id) });
  } catch (err) {
    toast(errorText(err), "error");
    return null;
  }
  const n = result.deleted.length;
  if (n) toast(n === 1 ? `Deleted ${result.deleted[0].key}.` : `Deleted ${n} ${plural}.`);
  if (result.blocked.length) showKept(result.blocked, plural);
  else if (!n) toast("Nothing was deleted: the items no longer exist.", "error");
  return result;
}

/**
 * Row selection for one list. Call begin() before drawing a page, put box(id, key) in each row and head() in the header;
 * `bar` (hidden while nothing is selected) goes above the table. The selection survives paging and sorting; clear() it when
 * the search changes. onDone() reloads the list after a delete.
 */
export function rowSelection({ url, singular, plural, consequence, onDone, extra = [] }) {
  const picked = new Map(); // id -> key, across pages
  const visible = new Map(); // id -> { cb, key } on the page that is shown
  const count = h("strong", {});
  const head = h("input", { class: "cb", type: "checkbox", "aria-label": `Select all ${plural} on this page` });
  const del = h("button", { class: "btn btn-danger btn-sm", type: "button" }, "Delete selected");
  const clearBtn = h("button", { class: "link", type: "button" }, "Clear selection");
  const bar = h("div", { class: "bulk-bar", role: "region", "aria-label": `Selected ${plural}`, hidden: true }, count, clearBtn, h("span", { style: "flex:1" }), ...extra, del);

  function sync() {
    bar.hidden = picked.size === 0;
    count.textContent = `${picked.size} ${picked.size === 1 ? singular : plural} selected`;
    let on = 0;
    for (const [id, { cb }] of visible) {
      cb.checked = picked.has(id);
      cb.closest("tr")?.classList.toggle("picked", cb.checked);
      if (cb.checked) on += 1;
    }
    head.checked = visible.size > 0 && on === visible.size;
    head.indeterminate = on > 0 && on < visible.size;
  }
  head.addEventListener("change", () => {
    for (const [id, { key }] of visible) {
      if (head.checked) picked.set(id, key);
      else picked.delete(id);
    }
    sync();
  });
  clearBtn.addEventListener("click", () => {
    picked.clear();
    sync();
  });
  del.addEventListener("click", async () => {
    del.disabled = true;
    const result = await confirmAndDelete({ url, items: [...picked].map(([id, key]) => ({ id, key })), singular, plural, consequence });
    del.disabled = false;
    if (!result) return;
    for (const d of result.deleted) picked.delete(d.id);
    for (const id of [...picked.keys()]) if (!result.blocked.some((b) => b.id === id)) picked.delete(id); // ids that no longer exist
    onDone();
  });

  return {
    bar,
    head: () => h("th", { scope: "col", class: "col-check" }, head),
    begin: () => visible.clear(),
    box(id, key) {
      const cb = h("input", { class: "cb", type: "checkbox", "aria-label": `Select ${key}` });
      cb.addEventListener("change", () => {
        if (cb.checked) picked.set(id, key);
        else picked.delete(id);
        sync();
      });
      visible.set(id, { cb, key });
      cb.checked = picked.has(id);
      return h("td", { class: "col-check" }, cb);
    },
    done: sync, // call after the rows are in the page
    clear() {
      picked.clear();
      sync();
    },
    ids: () => [...picked.keys()],
  };
}
