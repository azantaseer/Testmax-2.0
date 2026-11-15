import { del, get, post, put } from "../api.js";
import { errorText } from "../components.js";
import { h } from "../dom.js";
import { state } from "../store.js";

const SOURCE_TEXT = { system: "system default", tenant: "set by your organization", user: "your own setting" };

/**
 * mode "tenant": the organization's model (Super Admin), base /api/tenant
 * mode "me":     the signed-in user's own model, which beats the organization's, base /api/me
 */
export function aiSettingsPanel(mode) {
  const body = h("div", { class: "settings-card" }, h("div", {}, "Loading…"));
  load(body, mode);
  return body;
}

export function aiSettingsView() {
  return h(
    "div",
    { class: "settings-page" },
    h("h1", { class: "page-title" }, "Organization AI Model"),
    h("p", { class: "page-subtitle" }, "The default AI provider and model for everyone in your organization. Users can override it in My Settings."),
    aiSettingsPanel("tenant"),
  );
}

async function load(body, mode) {
  const base = mode === "me" ? "/api/me" : "/api/tenant";
  let data;
  try {
    data = await get(`${base}/ai-settings`);
  } catch (err) {
    body.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load settings.")));
    return;
  }

  let provider = data.provider || data.effective.provider || (data.providers.find((p) => p.configured) || data.providers[0] || {}).id;
  let model = data.provider ? data.model : data.effective.provider === provider ? data.effective.model || "" : "";
  let loadedModels = [];

  const effective = h("div", { class: "effective" });
  const orgLine = h("div", { class: "hint" });
  const status = h("div", { class: "status-line", role: "status", "aria-live": "polite" });
  const providerGrid = h("div", { class: "provider-grid", role: "radiogroup", "aria-label": "AI provider" });
  const select = h("select", { class: "input", "aria-label": "Model" });
  const loadBtn = h("button", { class: "btn btn-secondary btn-md", type: "button" }, "Load Models");
  const testBtn = h("button", { class: "btn btn-secondary btn-md", type: "button" }, "Test Connection");
  const saveBtn = h("button", { class: "btn btn-primary btn-md", type: "button" }, "Save");
  const clearBtn = h("button", { class: "btn btn-secondary btn-md", type: "button" }, "Use organization default");

  const labelOf = (id) => (data.providers.find((p) => p.id === id) || { label: id }).label;

  function setStatus(kind, text) {
    status.className = `status-line ${kind}`;
    status.textContent = text;
  }

  function renderEffective() {
    const e = data.effective;
    effective.textContent = e.provider ? `Currently in use: ${labelOf(e.provider)} \u00b7 ${e.model} (${SOURCE_TEXT[e.source]})` : "No model is selected yet. Choose a provider and model below, then save.";
    if (mode === "me") {
      const o = data.organization;
      orgLine.textContent = o.provider ? `Organization default: ${labelOf(o.provider)} \u00b7 ${o.model}. A model you save here is used for your own work instead.` : "The organization has not chosen a model yet. A model you save here is used for your own work.";
      clearBtn.hidden = !data.provider;
    }
  }

  function renderProviders() {
    providerGrid.replaceChildren(
      ...data.providers.map((p) => {
        const radio = h("input", { type: "radio", name: `provider-${mode}`, value: p.id, checked: provider === p.id, disabled: !p.configured });
        radio.addEventListener("change", () => {
          provider = p.id;
          loadedModels = [];
          model = data.provider === p.id ? data.model : "";
          setStatus("", "");
          renderModels();
        });
        return h(
          "label",
          { class: `provider-option${p.configured ? "" : " disabled"}` },
          radio,
          h("span", { class: "provider-name" }, p.label),
          h("span", { class: "provider-note" }, p.configured ? "Server key configured" : "No API key configured on the server"),
        );
      }),
    );
  }

  function renderModels() {
    const names = [...new Set([...(model ? [model] : []), ...loadedModels])];
    select.replaceChildren(
      h("option", { value: "" }, names.length ? "Select a model" : "Load models to choose one"),
      ...names.map((m) => h("option", { value: m, selected: m === model }, m)),
    );
    select.value = model || "";
    loadBtn.disabled = !provider;
    testBtn.disabled = !provider || !model;
    saveBtn.disabled = !provider || !model;
  }
  select.addEventListener("change", () => {
    model = select.value;
    setStatus("", "");
    renderModels();
  });

  async function busy(work) {
    [loadBtn, testBtn, saveBtn, clearBtn].forEach((b) => (b.disabled = true));
    try {
      await work();
    } finally {
      renderModels();
      clearBtn.disabled = false;
    }
  }

  loadBtn.addEventListener("click", () =>
    busy(async () => {
      setStatus("", "Loading models…");
      try {
        const res = await get(`${base}/ai-models?provider=${encodeURIComponent(provider)}`);
        loadedModels = res.models;
        setStatus("ok", `${res.models.length} models loaded.`);
      } catch (err) {
        setStatus("error", errorText(err, "Could not load models."));
      }
    }),
  );

  testBtn.addEventListener("click", () =>
    busy(async () => {
      setStatus("", "Testing connection…");
      try {
        const res = await post(`${base}/ai-test`, { provider, model });
        setStatus("ok", res.message);
      } catch (err) {
        setStatus("error", errorText(err, "Connection test failed."));
      }
    }),
  );

  function applySaved(saved, message) {
    data = saved;
    if (mode === "me") state.me.ai = saved.effective;
    renderEffective();
    setStatus("ok", message);
  }

  saveBtn.addEventListener("click", () =>
    busy(async () => {
      try {
        applySaved(await put(`${base}/ai-settings`, { provider, model }), mode === "me" ? "Saved. Your work will use this model." : "Saved. New work in your organization will use this model.");
      } catch (err) {
        setStatus("error", errorText(err, "Could not save."));
      }
    }),
  );

  clearBtn.addEventListener("click", () =>
    busy(async () => {
      try {
        const saved = await del(`${base}/ai-settings`);
        applySaved(saved, "Your own setting was removed. You now use the organization default.");
        provider = saved.effective.provider || provider;
        model = saved.effective.model || "";
        loadedModels = [];
        renderProviders();
      } catch (err) {
        setStatus("error", errorText(err, "Could not update."));
      }
    }),
  );

  renderEffective();
  renderProviders();
  renderModels();
  body.replaceChildren(
    effective,
    mode === "me" ? orgLine : null,
    h("div", {}, h("h2", { class: "settings-section-title" }, "Provider"), providerGrid),
    h("div", {}, h("h2", { class: "settings-section-title" }, "Model"), h("div", { class: "model-row" }, select, loadBtn)),
    h("div", { class: "settings-actions" }, testBtn, saveBtn, mode === "me" ? clearBtn : null),
    status,
  );
}
