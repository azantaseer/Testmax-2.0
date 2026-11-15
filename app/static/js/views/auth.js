import { ApiError, get, post } from "../api.js";
import { h, icon } from "../dom.js";
import { navigate, state } from "../store.js";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MIN_PASSWORD = 10;
const REMEMBER_KEY = "tm_remember_email";

let joinedOrganizationName = null;

// ---------- small pieces ----------
function storageGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function storageSet(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: fine */
  }
}

function clearTooltips(root) {
  root.querySelectorAll(".tooltip").forEach((t) => t.remove());
  root.querySelectorAll(".input.invalid").forEach((i) => i.classList.remove("invalid"));
}

function showTooltip(field, message) {
  field.querySelectorAll(".tooltip").forEach((t) => t.remove());
  const input = field.querySelector(".input");
  if (input) input.classList.add("invalid");
  field.append(
    h(
      "div",
      { class: "tooltip", role: "alert" },
      h("div", { class: "tooltip-box" }, h("span", { class: "tooltip-badge", "aria-hidden": "true" }, "!"), h("span", { class: "tooltip-text" }, message)),
    ),
  );
  if (input) {
    input.focus();
    input.addEventListener(
      "input",
      () => {
        field.querySelectorAll(".tooltip").forEach((t) => t.remove());
        input.classList.remove("invalid");
      },
      { once: true },
    );
  }
}

function textField({ name, label, type = "text", placeholder = "", autocomplete = "off", value = "", hint = null }) {
  const input = h("input", { class: "input", id: `f-${name}`, name, type, placeholder, autocomplete, value, maxlength: "200" });
  const field = h(
    "div",
    { class: "field" },
    h("label", { class: "label", for: `f-${name}` }, label, " ", h("span", { class: "req", "aria-hidden": "true" }, "*")),
    input,
    hint ? h("div", { class: "hint" }, hint) : null,
  );
  return { field, input };
}

function passwordField({ name = "password", label = "Password", placeholder = "Enter your password", autocomplete = "current-password", hint = null }) {
  const input = h("input", { class: "input", id: `f-${name}`, name, type: "password", placeholder, autocomplete, maxlength: "200" });
  const eye = h("button", { class: "eye", type: "button", "aria-label": "Show password", "aria-pressed": "false" }, icon("eyeOff"));
  eye.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    eye.setAttribute("aria-pressed", String(show));
    eye.setAttribute("aria-label", show ? "Hide password" : "Show password");
    eye.replaceChildren(icon(show ? "eye" : "eyeOff"));
  });
  const field = h(
    "div",
    { class: "field" },
    h("label", { class: "label", for: `f-${name}` }, label, " ", h("span", { class: "req", "aria-hidden": "true" }, "*")),
    h("div", { class: "input-wrap" }, input, eye),
    hint ? h("div", { class: "hint" }, hint) : null,
  );
  return { field, input };
}

function errorBanner() {
  const el = h("div", { class: "banner-error", role: "alert", hidden: true });
  return {
    el,
    show(message) {
      el.textContent = message;
      el.hidden = false;
    },
    hide() {
      el.hidden = true;
    },
  };
}

function header(title, subtitle, small = false) {
  return h(
    "div",
    { class: "card-header" },
    h("h1", { class: `card-title${small ? " sm" : ""}` }, title),
    h("p", { class: "card-subtitle" }, subtitle),
  );
}

async function submitting(button, label, work) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await work();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

// ---------- layout ----------
function tabs(active) {
  const tab = (view, text) =>
    h("a", { class: "auth-tab", role: "tab", href: `/authentication?view=${view}`, "data-link": "", "aria-selected": String(active === view) }, text);
  return h("div", { class: "auth-tabs", role: "tablist", "aria-label": "Authentication" }, tab("sign-in", "Sign In"), tab("sign-up", "Sign Up"));
}

function marketingPanel() {
  return h(
    "section",
    { class: "left-panel" },
    h(
      "div",
      { class: "left-content" },
      h(
        "div",
        { class: "left-brand" },
        h("img", { class: "left-logo", src: "/static/assets/testmax-logo-full.svg", alt: "TestMax" }),
        h("h2", { class: "left-heading" }, "Revenue-Critical AI for Modern QA Teams"),
      ),
      h("p", { class: "left-lead" }, "Transform your business with AI agents tailored to your industry’s unique challenges."),
      h("p", { class: "left-body" }, "Turn unstructured requirements into production-ready test cases, scripts, and reports — faster, smarter, and without ambiguity."),
    ),
  );
}

function page(activeTab, cardContent) {
  const card = h("div", { class: "auth-card" }, h("div", { class: "auth-card-inner" }, cardContent));
  return h(
    "main",
    { class: "auth-viewport" },
    h(
      "div",
      { class: "auth-main" },
      h(
        "div",
        { class: "auth-cluster" },
        marketingPanel(),
        h(
          "div",
          { class: "auth-right" },
          h(
            "div",
            { class: "auth-right-col" },
            h("img", { class: "auth-mobile-logo", src: "/static/assets/testmax-logo-full.svg", alt: "TestMax" }),
            tabs(activeTab),
            card,
          ),
        ),
      ),
    ),
    h("div", { class: "legal-footer" }, h("span", {}, "Terms & Conditions"), h("span", { "aria-hidden": "true" }, "·"), h("span", {}, "Privacy Policy")),
  );
}

async function finishLogin(me) {
  state.me = me;
  // A brand-new organization has no model yet: send its Super Admin straight to choosing one.
  const needsModel = me.ai && me.ai.source === "none" && me.user.role === "super_admin";
  navigate(needsModel ? "/settings/ai" : "/projects", { replace: true });
}

// ---------- views ----------
function signInView() {
  const email = textField({ name: "email", label: "Email", type: "email", placeholder: "Enter your email", autocomplete: "username", value: storageGet(REMEMBER_KEY) || "" });
  const password = passwordField({});
  const remember = h("input", { type: "checkbox", id: "f-remember", checked: true });
  const banner = errorBanner();
  const button = h("button", { class: "btn btn-primary btn-lg", type: "submit" }, "Sign In");
  const form = h(
    "form",
    { class: "form-stack", novalidate: true },
    h(
      "div",
      { class: "fields" },
      email.field,
      password.field,
      h("div", { class: "remember-row" }, h("label", { class: "remember", for: "f-remember" }, remember, "Remember Me")),
    ),
    h("div", { class: "auth-actions after-fields" }, banner.el, button),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearTooltips(form);
    banner.hide();
    const emailValue = email.input.value.trim();
    if (!EMAIL_RE.test(emailValue)) return showTooltip(email.field, "Enter a valid email address.");
    if (!password.input.value) return showTooltip(password.field, "Enter your password.");
    try {
      const me = await submitting(button, "Signing in…", () =>
        post("/api/auth/login", { email: emailValue, password: password.input.value, remember_me: remember.checked }),
      );
      storageSet(REMEMBER_KEY, remember.checked ? emailValue.toLowerCase() : null);
      await finishLogin(me);
    } catch (err) {
      banner.show(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    }
  });
  return page("sign-in", [header("Sign In", "Enter your credentials here to access your account."), form]);
}

function signUpChoiceView() {
  const create = h("a", { class: "btn btn-primary btn-lg", href: "/authentication?view=create-org", "data-link": "" }, "Create your organization account");
  create.style.fontSize = "18px";
  const join = h("a", { class: "btn btn-secondary btn-lg", href: "/authentication?view=join-org", "data-link": "" }, "Join any organization");
  join.style.fontSize = "18px";
  return page("sign-up", [
    header("Sign Up", "Choose how you want to get started."),
    h("div", { class: "auth-actions" }, create, join),
  ]);
}

function createOrgView() {
  const org = textField({ name: "organization_name", label: "Organization name", placeholder: "Your company or team name" });
  const name = textField({ name: "name", label: "Your name", placeholder: "Enter your full name", autocomplete: "name" });
  const email = textField({ name: "email", label: "Email", type: "email", placeholder: "Enter your email", autocomplete: "username" });
  const password = passwordField({ placeholder: "Create a password", autocomplete: "new-password", hint: `At least ${MIN_PASSWORD} characters.` });
  const banner = errorBanner();
  const button = h("button", { class: "btn btn-primary btn-lg", type: "submit" }, "Create Organization");
  const form = h(
    "form",
    { class: "form-stack", novalidate: true },
    h("div", { class: "fields roomy" }, org.field, name.field, email.field, password.field),
    h("div", { class: "auth-actions" }, banner.el, button, h("a", { class: "back-link center", href: "/authentication?view=sign-up", "data-link": "" }, "← Back")),
  );
  const byName = { organization_name: org, name, email, password };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearTooltips(form);
    banner.hide();
    const values = { organization_name: org.input.value.trim(), name: name.input.value.trim(), email: email.input.value.trim(), password: password.input.value };
    if (values.organization_name.length < 2) return showTooltip(org.field, "Enter your organization’s name.");
    if (!values.name) return showTooltip(name.field, "Enter your name.");
    if (!EMAIL_RE.test(values.email)) return showTooltip(email.field, "Enter a valid email address.");
    if (values.password.length < MIN_PASSWORD) return showTooltip(password.field, `Password must be at least ${MIN_PASSWORD} characters.`);
    try {
      const me = await submitting(button, "Creating…", () => post("/api/auth/signup-organization", { ...values, remember_me: true }));
      await finishLogin(me);
    } catch (err) {
      if (!(err instanceof ApiError)) return banner.show("Something went wrong. Please try again.");
      const target = { organization_exists: org, email_taken: email }[err.code] || byName[Object.keys(err.fields || {})[0]];
      if (target) showTooltip(target.field, err.message);
      else banner.show(err.message);
    }
  });
  return page("sign-up", [header("Create your organization account", "You will become the Super Admin of this organization.", true), form]);
}

function joinOrgView() {
  let chosen = null; // { id, name }
  let searchSeq = 0;
  let timer = null;

  const search = textField({ name: "organization_search", label: "Organization", placeholder: "Search by organization name" });
  const results = h("div", { class: "org-results", hidden: true });
  const chosenBox = h("div", { class: "org-chosen", hidden: true });
  const name = textField({ name: "name", label: "Your name", placeholder: "Enter your full name", autocomplete: "name" });
  const email = textField({ name: "email", label: "Email", type: "email", placeholder: "Enter your email", autocomplete: "username" });
  const password = passwordField({ placeholder: "Create a password", autocomplete: "new-password", hint: `At least ${MIN_PASSWORD} characters.` });
  const banner = errorBanner();
  const button = h("button", { class: "btn btn-primary btn-lg", type: "submit" }, "Request to Join");
  search.field.append(results, chosenBox);

  function choose(org) {
    chosen = org;
    search.input.hidden = true;
    results.hidden = true;
    chosenBox.hidden = false;
    chosenBox.replaceChildren(
      h("span", {}, org.name),
      h("button", { class: "link", type: "button", onclick: () => unchoose() }, "Change"),
    );
  }
  function unchoose() {
    chosen = null;
    search.input.hidden = false;
    chosenBox.hidden = true;
    search.input.focus();
    runSearch();
  }
  async function runSearch() {
    const q = search.input.value.trim();
    const seq = ++searchSeq;
    if (q.length < 2) {
      results.hidden = true;
      return;
    }
    try {
      const data = await get(`/api/public/organizations?q=${encodeURIComponent(q)}`);
      if (seq !== searchSeq || chosen) return;
      results.replaceChildren(
        ...(data.organizations.length
          ? data.organizations.map((o) => h("button", { class: "org-option", type: "button", onclick: () => choose(o) }, o.name))
          : [h("div", { class: "org-empty" }, "No organization found.")]),
      );
      results.hidden = false;
    } catch {
      results.hidden = true;
    }
  }
  search.input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(runSearch, 250);
  });

  const form = h(
    "form",
    { class: "form-stack", novalidate: true },
    h("div", { class: "fields roomy" }, search.field, name.field, email.field, password.field),
    h("div", { class: "auth-actions" }, banner.el, button, h("a", { class: "back-link center", href: "/authentication?view=sign-up", "data-link": "" }, "← Back")),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearTooltips(form);
    banner.hide();
    if (!chosen) return showTooltip(search.field, "Search for and choose an organization.");
    const values = { organization_id: chosen.id, name: name.input.value.trim(), email: email.input.value.trim(), password: password.input.value };
    if (!values.name) return showTooltip(name.field, "Enter your name.");
    if (!EMAIL_RE.test(values.email)) return showTooltip(email.field, "Enter a valid email address.");
    if (values.password.length < MIN_PASSWORD) return showTooltip(password.field, `Password must be at least ${MIN_PASSWORD} characters.`);
    try {
      const data = await submitting(button, "Sending…", () => post("/api/auth/signup-join", values));
      joinedOrganizationName = data.organization.name;
      navigate("/authentication?view=pending", { replace: true });
    } catch (err) {
      if (!(err instanceof ApiError)) return banner.show("Something went wrong. Please try again.");
      if (err.code === "email_taken") showTooltip(email.field, err.message);
      else banner.show(err.message);
    }
  });
  return page("sign-up", [header("Join an organization", "Search for your organization. Its admin must approve your request.", true), form]);
}

function pendingView() {
  if (!joinedOrganizationName) {
    navigate("/authentication?view=sign-in", { replace: true });
    return h("div");
  }
  const orgName = joinedOrganizationName;
  return h(
    "main",
    { class: "auth-viewport" },
    h(
      "div",
      { class: "auth-main" },
      h(
        "div",
        { class: "pending-card", style: "margin:auto" },
        h("div", { class: "pending-badge" }, icon("clock")),
        h("h1", { class: "pending-title" }, "Waiting for approval"),
        h("p", { class: "pending-body" }, `Your request to join ${orgName} was sent. An admin of ${orgName} must approve it before you can sign in.`),
        h("a", { class: "btn btn-secondary btn-md", href: "/authentication?view=sign-in", "data-link": "" }, "Back to Sign In"),
      ),
    ),
    h("div", { class: "legal-footer" }),
  );
}

export function authView(view) {
  switch (view) {
    case "sign-up":
      return signUpChoiceView();
    case "create-org":
      return createOrgView();
    case "join-org":
      return joinOrgView();
    case "pending":
      return pendingView();
    default:
      return signInView();
  }
}
