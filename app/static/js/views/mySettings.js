import { post } from "../api.js";
import { errorText, fieldError, formField, toast, underlineTabs } from "../components.js";
import { h } from "../dom.js";
import { state } from "../store.js";
import { aiSettingsPanel } from "./aiSettings.js";

const MIN_PASSWORD = 10;

function passwordPanel() {
  const current = formField({ name: "current", label: "Current password", type: "password", autocomplete: "current-password" });
  const next = formField({ name: "next", label: "New password", type: "password", autocomplete: "new-password", hint: `At least ${MIN_PASSWORD} characters. Your other sessions will be signed out.` });
  const submit = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Change Password");
  const form = h("form", { class: "form-stack tight", novalidate: true }, current.field, next.field, h("div", { class: "settings-actions" }, submit));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    form.querySelectorAll(".field-error").forEach((e) => e.remove());
    if (!current.input.value) return fieldError(current.field, "Enter your current password.");
    if (next.input.value.length < MIN_PASSWORD) return fieldError(next.field, `Password must be at least ${MIN_PASSWORD} characters.`);
    submit.disabled = true;
    try {
      await post("/api/me/password", { current_password: current.input.value, new_password: next.input.value });
      current.input.value = "";
      next.input.value = "";
      toast("Password changed.");
    } catch (err) {
      fieldError(err.code === "wrong_password" ? current.field : next.field, errorText(err));
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}

export function mySettingsView() {
  const user = state.me.user;
  const name = formField({ name: "acct-name", label: "Name", value: user.name, required: false });
  const email = formField({ name: "acct-email", label: "Email", value: user.email, required: false, hint: "Name and email cannot be changed here. Ask an administrator." });
  name.input.readOnly = true;
  email.input.readOnly = true;
  const account = h("div", { class: "settings-page" }, h("div", { class: "form-stack tight" }, name.field, email.field), h("section", { class: "settings-card" }, h("h2", { class: "settings-card-title" }, "Password"), passwordPanel()));
  const ai = h("div", { class: "settings-page" }, aiSettingsPanel("me"));
  const tabs = underlineTabs([
    { key: "account", label: "Account", panel: account },
    { key: "ai", label: "AI Model", panel: ai },
  ]);
  return h("div", {}, h("h1", { class: "page-title" }, "My Settings"), h("p", { class: "page-subtitle" }, "Manage your account and the AI model you use."), tabs.list, tabs.body);
}
