// Tags on requirements: coloured chips, and the small dialogs to add or remove tags on one or many requirements.
import { post } from "../api.js";
import { errorText, modal, toast } from "../components.js";
import { h } from "../dom.js";

const PALETTE = ["teal", "fuchsia", "emerald", "amber", "indigo", "slate"];
const MAX_LENGTH = 30;

/** The same tag always gets the same colour. */
export function tagColor(name) {
  let n = 0;
  for (const ch of String(name).toLowerCase()) n = (n * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[n % PALETTE.length];
}

/** One tag as a pill. With onClick it is a button (filter by it); with onRemove it also gets an x. */
export function tagChip(name, { onClick = null, onRemove = null } = {}) {
  const cls = `tag-chip tag-${tagColor(name)}`;
  const label = h("span", { class: "tag-text" }, name);
  if (onRemove) {
    return h("span", { class: cls }, label, h("button", { class: "tag-x", type: "button", "aria-label": `Remove tag ${name}`, onclick: onRemove }, "×"));
  }
  return onClick ? h("button", { class: cls, type: "button", title: `Show only requirements tagged ${name}`, onclick: onClick }, label) : h("span", { class: cls }, label);
}

/** Up to `max` chips and "+n" for the rest (its tooltip lists them). */
export function tagList(tags, onClick, max = 2) {
  if (!tags.length) return h("span", { class: "muted-text" }, "—");
  const shown = tags.slice(0, max);
  const rest = tags.slice(max);
  return h(
    "span",
    { class: "tag-list" },
    shown.map((t) => tagChip(t, { onClick: () => onClick(t) })),
    rest.length ? h("span", { class: "tag-more", title: rest.join(", ") }, `+${rest.length}`) : null,
  );
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** Add tags to the requirements `ids`. vocabulary: [{name, count}] (the tags already used in the project, offered as one-click choices). */
export function addTagsDialog({ project, ids, vocabulary, onDone }) {
  const input = h("input", { class: "input", id: "tag-input", type: "text", maxlength: "120", placeholder: "Type a tag, or several separated by commas", autocomplete: "off", "aria-describedby": "tag-hint" });
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const save = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Add tags");
  const suggest = vocabulary.length
    ? h(
        "div",
        { class: "tag-suggest" },
        h("div", { class: "label" }, "Tags already in this project"),
        h("div", { class: "tag-list wrap" }, vocabulary.map((t) => tagChip(t.name, { onClick: () => {
          const have = input.value.split(",").map((x) => x.trim().toLowerCase());
          if (!have.includes(t.name.toLowerCase())) input.value = [input.value.replace(/[,\s]+$/, ""), t.name].filter(Boolean).join(", ");
          input.focus();
        } }))),
      )
    : null;
  const form = h(
    "form",
    { class: "form-stack tight", novalidate: true },
    h("p", { class: "modal-text", style: "margin:0" }, `Tags are labels that group requirements. They are added to ${plural(ids.length, "requirement")}.`),
    h("div", { class: "field" }, h("label", { class: "label", for: "tag-input" }, "Tags"), input, h("div", { class: "field-hint", id: "tag-hint" }, `Up to ${MAX_LENGTH} characters each, at most 10 tags on a requirement. "login" and "Login" are the same tag.`)),
    suggest,
    banner,
    h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), save),
  );
  const m = modal({ title: ids.length === 1 ? "Add tags" : `Add tags to ${ids.length} requirements`, content: form, width: 520 });
  m.setDirty(() => input.value.trim() !== "");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const add = input.value.split(",").map((t) => t.trim()).filter(Boolean);
    if (!add.length) {
      banner.textContent = "Type at least one tag.";
      banner.hidden = false;
      return input.focus();
    }
    if (add.some((t) => t.length > MAX_LENGTH)) {
      banner.textContent = `A tag can have at most ${MAX_LENGTH} characters.`;
      banner.hidden = false;
      return input.focus();
    }
    save.disabled = true;
    try {
      const result = await post(`/api/projects/${project.id}/requirements/tags`, { ids, add });
      m.close();
      toast(result.updated ? `Tagged ${plural(result.updated, "requirement")}.` : "They already had those tags.");
      onDone(result);
    } catch (err) {
      save.disabled = false;
      banner.textContent = errorText(err);
      banner.hidden = false;
    }
  });
}

/** Remove tags from the requirements `ids`: pick from the tags used in the project. */
export function removeTagsDialog({ project, ids, vocabulary, onDone }) {
  const boxes = vocabulary.map((t) => ({ t, box: h("input", { type: "checkbox", value: t.name, id: `rm-${t.name}` }) }));
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const save = h("button", { class: "btn btn-danger btn-md", type: "submit", disabled: !vocabulary.length }, "Remove tags");
  const form = h(
    "form",
    { class: "form-stack tight", novalidate: true },
    h("p", { class: "modal-text", style: "margin:0" }, `Choose the tags to take off ${plural(ids.length, "requirement")}. A requirement that does not have a chosen tag is left as it is.`),
    vocabulary.length
      ? h("div", { class: "check-list" }, boxes.map(({ t, box }) => h("label", { class: "check-row", for: box.id }, box, tagChip(t.name), h("span", { class: "muted-text small" }, plural(t.count, "requirement")))))
      : h("div", { class: "notice" }, "No requirement in this project has a tag yet."),
    banner,
    h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), save),
  );
  const m = modal({ title: ids.length === 1 ? "Remove tags" : `Remove tags from ${ids.length} requirements`, content: form, width: 520 });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const remove = boxes.filter((b) => b.box.checked).map((b) => b.t.name);
    if (!remove.length) {
      banner.textContent = "Choose at least one tag.";
      banner.hidden = false;
      return;
    }
    save.disabled = true;
    try {
      const result = await post(`/api/projects/${project.id}/requirements/tags`, { ids, remove });
      m.close();
      toast(result.updated ? `Updated ${plural(result.updated, "requirement")}.` : "Nothing needed to change.");
      onDone(result);
    } catch (err) {
      save.disabled = false;
      banner.textContent = errorText(err);
      banner.hidden = false;
    }
  });
}

