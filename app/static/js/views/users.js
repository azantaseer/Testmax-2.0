import { api, get, post } from "../api.js";
import { confirmDialog, dirtyTracker, errorText, fieldError, formField, modal, pagination, toast } from "../components.js";
import { h, icon } from "../dom.js";
import { PROVIDER_LABEL, state } from "../store.js";
import { savedRows, emptyState, skeletonTable, tableBlock } from "../table.js";

const ROLE_LABEL = { super_admin: "Super Admin", site_admin: "Site Admin", user: "User" };
const STATUS_LABEL = { active: "Active", pending: "Pending approval", disabled: "Disabled" };
const SOURCE_LABEL = { system: "system default", tenant: "organization", user: "personal" };
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MIN_PASSWORD = 10;

export function usersView() {
  const me = state.me.user;
  const isSuper = me.role === "super_admin";
  const q = { search: "", status: "", page: 1, pageSize: savedRows(10) };
  let timer = null;

  const tableHost = h("div", { class: "table-host fill" });
  const pendingHost = h("div", {});
  const search = h("input", { class: "input search-input", type: "search", placeholder: "Search by name or email…", "aria-label": "Search users" });
  const STATUS_TABS = [["", "All"], ["active", "Active"], ["pending", "Pending approval"], ["disabled", "Disabled"]];
  const statusFilter = (() => {
    const el = h("div", { class: "tabbar", role: "group", "aria-label": "Show users with this status" });
    const buttons = STATUS_TABS.map(([value, label]) =>
      h("button", { class: "tab", type: "button", onclick: () => { api.value = value; q.page = 1; load(); } }, label),
    );
    const api = {
      el,
      get value() { return q.status; },
      set value(v) { q.status = v; paint(); },
    };
    function paint() {
      buttons.forEach((b, i) => b.setAttribute("aria-current", STATUS_TABS[i][0] === q.status ? "page" : "false"));
    }
    el.append(...buttons);
    paint();
    return api;
  })();
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      q.search = search.value;
      q.page = 1;
      load();
    }, 250);
  });
  async function load() {
    const params = new URLSearchParams({ q: q.search, status: q.status, page: String(q.page), page_size: String(q.pageSize) });
    try {
      const data = await get(`/api/users?${params}`);
      if (data.users.length === 0 && data.total > 0 && q.page > 1) {
        q.page = Math.max(1, Math.ceil(data.total / q.pageSize));
        return load();
      }
      renderPending(data.pending_total);
      renderTable(data);
    } catch (err) {
      tableHost.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load users.")));
    }
  }

  function renderPending(count) {
    if (!count) return pendingHost.replaceChildren();
    pendingHost.replaceChildren(
      h(
        "div",
        { class: "notice" },
        `${count} ${count === 1 ? "person is" : "people are"} waiting for approval.`,
        h(
          "button",
          {
            class: "link",
            type: "button",
            onclick: () => {
              statusFilter.value = "pending";
              q.status = "pending";
              q.page = 1;
              load();
            },
          },
          "Show requests",
        ),
      ),
    );
  }

  async function act(work, okMessage) {
    try {
      await work();
      if (okMessage) toast(okMessage);
      await load();
    } catch (err) {
      toast(errorText(err), "error");
      await load();
    }
  }

  function rowMenu(u) {
    const panel = h("div", { class: "menu-panel", role: "menu", hidden: true });
    const trigger = h("button", { class: "icon-btn", type: "button", "aria-label": `Actions for ${u.name}`, "aria-haspopup": "menu", "aria-expanded": "false" }, "⋯");
    const wrap = h("div", { class: "user-menu" }, trigger, panel);
    const items = [];
    if (u.status === "active") {
      items.push(["Disable user", async () => {
        if (await confirmDialog({ title: "Disable user", message: `${u.name} will be signed out and unable to sign in until you enable the account again.`, confirmLabel: "Disable", danger: true }))
          act(() => api("PATCH", `/api/users/${u.id}`, { status: "disabled" }), "User disabled.");
      }]);
    }
    if (u.status === "disabled") items.push(["Enable user", () => act(() => api("PATCH", `/api/users/${u.id}`, { status: "active" }), "User enabled.")]);
    items.push(["Reset password", () => resetPasswordModal(u)]);
    items.push([
      "Delete user",
      async () => {
        if (await confirmDialog({ title: "Delete user", message: `Delete ${u.name} (${u.email})? This cannot be undone.`, confirmLabel: "Delete", danger: true }))
          act(() => api("DELETE", `/api/users/${u.id}`), "User deleted.");
      },
      true,
    ]);
    for (const [label, fn, danger] of items) {
      panel.append(h("button", { class: `menu-item${danger ? " danger" : ""}`, role: "menuitem", type: "button", onclick: () => { close(); fn(); } }, label));
    }
    function close() {
      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    }
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const opening = panel.hidden;
      document.dispatchEvent(new Event("click"));
      panel.hidden = !opening;
      trigger.setAttribute("aria-expanded", String(opening));
    });
    const onDoc = (event) => {
      if (!wrap.isConnected) return document.removeEventListener("click", onDoc);
      if (!event.target || !wrap.contains(event.target)) close();
    };
    document.addEventListener("click", onDoc);
    wrap.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        close();
        trigger.focus();
      }
    });
    return wrap;
  }

  function roleCell(u) {
    if (!isSuper || u.id === me.id) return ROLE_LABEL[u.role];
    const select = h("select", { class: "input inline-select", "aria-label": `Role for ${u.name}` }, ...Object.entries(ROLE_LABEL).map(([value, label]) => h("option", { value, selected: value === u.role }, label)));
    select.addEventListener("change", () => act(() => api("PATCH", `/api/users/${u.id}`, { role: select.value }), "Role updated."));
    return select;
  }

  function actionsCell(u) {
    if (u.id === me.id) return h("span", { class: "muted-text" }, "You");
    if (u.status === "pending") {
      return h(
        "div",
        { class: "row-actions" },
        h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => act(() => api("PATCH", `/api/users/${u.id}`, { status: "active" }), `${u.name} approved.`) }, "Approve"),
        h(
          "button",
          {
            class: "btn btn-secondary btn-sm",
            type: "button",
            onclick: async () => {
              if (await confirmDialog({ title: "Reject request", message: `Reject ${u.name}'s request to join? Their request will be removed.`, confirmLabel: "Reject", danger: true }))
                act(() => api("DELETE", `/api/users/${u.id}`), "Request rejected.");
            },
          },
          "Reject",
        ),
      );
    }
    // Site admins may only manage plain users.
    if (!isSuper && u.role !== "user") return h("span", { class: "muted-text" }, "—");
    return rowMenu(u);
  }

  function renderTable(data) {
    const head = h("tr", {}, ...["Name", "Email", "Role", "Status", "AI Model", ""].map((t) => h("th", { scope: "col" }, t)));
    const rows = data.users.map((u) =>
      h(
        "tr",
        {},
        h("td", { class: "cell-strong" }, u.name),
        h("td", {}, u.email),
        h("td", {}, roleCell(u)),
        h("td", {}, h("span", { class: `chip ${u.status}` }, STATUS_LABEL[u.status])),
        h("td", {}, u.ai.provider ? [`${PROVIDER_LABEL[u.ai.provider]} \u00b7 ${u.ai.model}`, h("span", { class: "muted-text small" }, ` (${SOURCE_LABEL[u.ai.source]})`)] : h("span", { class: "muted-text" }, "Not selected")),
        h("td", { class: "cell-actions" }, actionsCell(u)),
      ),
    );
    tableHost.replaceChildren(
      rows.length
        ? tableBlock(head, rows)
        : emptyState({
            iconName: "users",
            title: q.search || q.status ? "No users match" : "No users yet",
            text: q.search || q.status ? "Try a different search or status." : "Create a user to give someone access to your organization.",
            actions: q.search || q.status ? [h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => { search.value = ""; statusFilter.value = ""; q.search = ""; q.status = ""; q.page = 1; load(); } }, "Clear search and filter")] : [],
          }),
      data.total > 0 ? pagination({
        page: data.page,
        pageSize: data.page_size,
        total: data.total,
        onPage: (p) => {
          q.page = p;
          load();
        },
        onPageSize: (n) => {
          q.pageSize = n;
          q.page = 1;
          load();
        },
      }) : null,
    );
  }

  function createModal() {
    const name = formField({ name: "name", label: "Name", placeholder: "Full name", autocomplete: "off" });
    const email = formField({ name: "email", label: "Email", type: "email", placeholder: "name@company.com" });
    const password = formField({ name: "password", label: "Initial password", type: "password", placeholder: "At least 10 characters", autocomplete: "new-password", hint: "Share this with the user. They can change it in My Settings." });
    const roleSelect = h("select", { class: "input", id: "m-role" }, h("option", { value: "user" }, "User"), isSuper ? h("option", { value: "site_admin" }, "Site Admin") : null, isSuper ? h("option", { value: "super_admin" }, "Super Admin") : null);
    const roleField = h("div", { class: "field" }, h("label", { class: "label", for: "m-role" }, "Role"), roleSelect);
    const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
    const submit = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Create User");
    const form = h("form", { class: "form-stack tight", novalidate: true }, name.field, email.field, password.field, isSuper ? roleField : null, banner, h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), submit));
    const m = modal({ title: "Create New User", content: form });
    m.setDirty(dirtyTracker(form));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      banner.hidden = true;
      form.querySelectorAll(".field-error").forEach((e) => e.remove());
      const values = { name: name.input.value.trim(), email: email.input.value.trim(), password: password.input.value, role: roleSelect.value };
      if (!values.name) return fieldError(name.field, "Enter a name.");
      if (!EMAIL_RE.test(values.email)) return fieldError(email.field, "Enter a valid email address.");
      if (values.password.length < MIN_PASSWORD) return fieldError(password.field, `Password must be at least ${MIN_PASSWORD} characters.`);
      submit.disabled = true;
      try {
        await post("/api/users", values);
        m.close();
        toast(`${values.name} was added.`);
        q.page = 1;
        load();
      } catch (err) {
        submit.disabled = false;
        if (err.code === "email_taken") fieldError(email.field, err.message);
        else {
          banner.textContent = errorText(err);
          banner.hidden = false;
        }
      }
    });
  }

  function resetPasswordModal(u) {
    const password = formField({ name: "password", label: "New password", type: "password", placeholder: "At least 10 characters", autocomplete: "new-password", hint: `${u.name} will be signed out everywhere and must use this password.` });
    const submit = h("button", { class: "btn btn-primary btn-md", type: "submit" }, "Reset Password");
    const form = h("form", { class: "form-stack tight", novalidate: true }, password.field, h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), submit));
    const m = modal({ title: "Reset password", content: form });
    m.setDirty(dirtyTracker(form));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      form.querySelectorAll(".field-error").forEach((e) => e.remove());
      if (password.input.value.length < MIN_PASSWORD) return fieldError(password.field, `Password must be at least ${MIN_PASSWORD} characters.`);
      submit.disabled = true;
      try {
        await post(`/api/users/${u.id}/reset-password`, { password: password.input.value });
        m.close();
        toast("Password reset.");
      } catch (err) {
        submit.disabled = false;
        fieldError(password.field, errorText(err));
      }
    });
  }

  const root = h(
    "div",
    { class: "fill" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", { class: "page-title" }, "Users"), h("p", { class: "page-subtitle" }, "Manage who can access your organization"))),
    h("div", { class: "tabbar-row", style: "margin-top:20px" }, statusFilter.el, h("button", { class: "btn btn-primary btn-md", type: "button", onclick: createModal }, icon("plus"), "Create New User")),
    pendingHost,
    h("div", { class: "list-toolbar", style: "margin-top:16px" }, h("div", { class: "search-wrap" }, icon("search"), search)),
    tableHost,
  );
  tableHost.replaceChildren(skeletonTable(["Name", "Email", "Role", "Status", "AI Model", ""]));
  load();
  return root;
}
