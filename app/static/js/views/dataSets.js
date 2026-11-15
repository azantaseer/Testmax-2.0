// Data variations of a test case: the picker in the steps table and the "Add data variations" dialog.
// DV-1 is the test data written in the steps (locked); every other variation holds a value per key, and an empty value means "use DV-1".
import { put } from "../api.js";
import { errorText, modal, toast } from "../components.js";
import { h, icon } from "../dom.js";

/** "DV-2 — Locked User" (number 2 for the first extra variation). */
export function setLabel(index, set) {
  if (index === 0) return "DV-1 (Default)";
  return set && set.name ? `DV-${index + 1} — ${set.name}` : `DV-${index + 1}`;
}

/** The value of a key in variation `index` (0 = default): an empty cell falls back to the default. */
export function valueFor(data, index, key) {
  const base = data.default[key] ?? "";
  if (index === 0) return base;
  const v = (data.sets[index - 1]?.values || {})[key];
  return v !== undefined && String(v).trim() !== "" ? v : base;
}

/** The <select> that switches which variation the Test Data column shows. onChange(index). */
export function variationPicker(data, current, onChange) {
  const select = h(
    "select",
    { class: "dv-select", "aria-label": "Data variation shown in the Test Data column" },
    h("option", { value: "0" }, setLabel(0)),
    data.sets.map((s, i) => h("option", { value: String(i + 1) }, setLabel(i + 1, s))),
  );
  select.value = String(current);
  select.addEventListener("change", () => onChange(Number(select.value)));
  return select;
}

/**
 * The dialog. `caseInfo` = { project_id, id, key, title, data }. onSaved(newData) after a successful save.
 */
export function dataSetsDialog(caseInfo, onSaved) {
  const data = caseInfo.data;
  let sets = data.sets.map((s) => ({ id: s.id, name: s.name || "", values: { ...(s.values || {}) } }));
  const MAX_SETS = 20;
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const count = h("span", { class: "muted-text small" });
  const addBtn = h("button", { class: "btn btn-secondary btn-sm", type: "button" }, icon("plus"), "Add variation");
  const save = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Save variations");
  const tableWrap = h("div", { class: "table-wrap dv-table-wrap" });
  const form = h(
    "form",
    { class: "form-stack tight", novalidate: true },
    h("p", { class: "modal-text", style: "margin:0" }, `${caseInfo.key} — ${caseInfo.title}`),
    h("div", { class: "notice" }, "The step and key columns come from the test case and cannot be changed here. DV-1 is the test data written in the steps. Each variation changes only the values a step types in; the steps and expected results stay the same. Leave a cell empty to use the DV-1 value."),
    h("div", {}, addBtn),
    tableWrap,
    banner,
    h("div", { class: "modal-actions" }, count, h("span", { style: "flex:1" }), h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), save),
  );
  const m = modal({ title: "Add test data variations", content: form, width: 960 });

  function draw() {
    const head = h(
      "tr",
      {},
      h("th", { scope: "col", style: "width:48px" }, "ID"),
      h("th", { scope: "col" }, "Test Step ", h("span", { class: "tpill lang" }, "Read only")),
      h("th", { scope: "col", style: "width:150px" }, "Key ", h("span", { class: "tpill lang" }, "Read only")),
      h("th", { scope: "col", class: "dv-col" }, h("div", { class: "dv-col-head" }, h("strong", {}, "DV-1"), h("span", { class: "muted-text small" }, "Default · locked"))),
      sets.map((s, i) =>
        h(
          "th",
          { scope: "col", class: "dv-col" },
          h(
            "div",
            { class: "dv-col-head" },
            h("strong", {}, `DV-${i + 2}`),
            h("input", {
              class: "input dv-name",
              type: "text",
              maxlength: "60",
              placeholder: "Name (optional)",
              "aria-label": `Name of DV-${i + 2}`,
              value: s.name,
              oninput: (e) => { s.name = e.target.value; },
            }),
            h("button", { class: "link small", type: "button", "aria-label": `Remove DV-${i + 2}`, onclick: () => { sets.splice(i, 1); draw(); } }, "Remove"),
          ),
        ),
      ),
    );
    const rows = data.keys.map((k) =>
      h(
        "tr",
        {},
        h("td", { class: "num" }, String(k.step_no)),
        h("td", {}, k.step),
        h("td", {}, h("code", { class: "key-chip" }, k.key)),
        h("td", { class: "dv-locked" }, data.default[k.key] ?? ""),
        sets.map((s, i) =>
          h("td", {}, h("input", {
            class: "input dv-input",
            type: "text",
            maxlength: "2000",
            placeholder: "Same as DV-1",
            "aria-label": `${k.key} in DV-${i + 2}`,
            value: s.values[k.key] ?? "",
            oninput: (e) => { s.values[k.key] = e.target.value; },
          })),
        ),
      ),
    );
    tableWrap.replaceChildren(h("table", { class: "data-table dv-table" }, h("thead", {}, head), h("tbody", {}, rows)));
    count.textContent = `${sets.length} variation${sets.length === 1 ? "" : "s"} added`;
    addBtn.disabled = sets.length >= MAX_SETS;
  }
  addBtn.addEventListener("click", () => {
    sets.push({ id: "", name: "", values: {} });
    draw();
    tableWrap.querySelector("table thead th:last-child input")?.focus();
    tableWrap.scrollLeft = tableWrap.scrollWidth;
  });
  draw();
  m.setDirty(() => JSON.stringify(sets) !== JSON.stringify(data.sets.map((s) => ({ id: s.id, name: s.name || "", values: { ...(s.values || {}) } }))));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    banner.hidden = true;
    save.disabled = true;
    save.textContent = "Saving…";
    try {
      const body = { data_sets: sets.map((s) => ({ id: s.id || undefined, name: s.name, values: Object.fromEntries(Object.entries(s.values).filter(([, v]) => v !== "")) })) };
      const saved = await put(`/api/projects/${caseInfo.project_id}/test-cases/${caseInfo.id}/data-sets`, body);
      m.close();
      toast(saved.sets.length ? "Data variations saved." : "Data variations removed.");
      onSaved(saved);
    } catch (err) {
      save.disabled = false;
      save.textContent = "Save variations";
      banner.textContent = errorText(err);
      banner.hidden = false;
    }
  });
}
