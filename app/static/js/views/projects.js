import { api, get, post } from "../api.js";
import { confirmDialog, dirtyTracker, errorText, fieldError, formField, modal, pagination, toast } from "../components.js";
import { h, icon } from "../dom.js";
import { navigate } from "../store.js";
import { remember, savedRows, savedSort, emptyState, skeletonTable, tableBlock } from "../table.js";
import { refreshSidebarProjects } from "./shell.js";

function formatDate(iso) {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" });
}

/** Create or edit dialog. `project` null means create. */
export async function projectModal(project, onSaved) {
  let people = [];
  try {
    people = (await get("/api/projects/assignable-users")).users;
  } catch {
    /* the list stays empty; the project can still be saved */
  }
  const name = formField({ name: "name", label: "Project name", placeholder: "e.g. Web App Project", value: project ? project.name : "" });
  const description = h("textarea", { class: "input", id: "m-description", rows: "3", maxlength: "2000", placeholder: "What is this project about? (optional)" });
  description.value = project ? project.description : "";
  const assigned = new Set((project ? project.assigned_users : []).map((u) => u.id));
  const list = h(
    "div",
    { class: "check-list", role: "group", "aria-label": "Assigned users" },
    ...(people.length
      ? people.map((p) => {
          const box = h("input", { type: "checkbox", value: p.id, checked: assigned.has(p.id) });
          return h("label", { class: "check-row" }, box, p.name);
        })
      : [h("div", { class: "muted-text small" }, "No other users yet.")]),
  );
  const banner = h("div", { class: "banner-error", role: "alert", hidden: true });
  const submit = h("button", { class: "btn btn-primary btn-md", type: "submit" }, project ? "Save Changes" : "Create Project");
  const form = h(
    "form",
    { class: "form-stack tight", novalidate: true },
    name.field,
    h("div", { class: "field" }, h("label", { class: "label", for: "m-description" }, "Description"), description),
    h("div", { class: "field" }, h("div", { class: "label" }, "Assigned users"), list),
    banner,
    h("div", { class: "modal-actions" }, h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => m.close() }, "Cancel"), submit),
  );
  const m = modal({ title: project ? "Edit Project" : "Create New Project", content: form, width: 520 });
  m.setDirty(dirtyTracker(form));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    banner.hidden = true;
    form.querySelectorAll(".field-error").forEach((e) => e.remove());
    const body = {
      name: name.input.value.trim(),
      description: description.value.trim(),
      member_ids: [...list.querySelectorAll("input:checked")].map((i) => i.value),
    };
    if (!body.name) return fieldError(name.field, "Enter a project name.");
    submit.disabled = true;
    try {
      const saved = project ? await api("PATCH", `/api/projects/${project.id}`, body) : await post("/api/projects", body);
      m.close();
      toast(project ? "Project updated." : "Project created.");
      refreshSidebarProjects();
      onSaved(saved);
    } catch (err) {
      submit.disabled = false;
      if (err.code === "project_exists") fieldError(name.field, err.message);
      else {
        banner.textContent = errorText(err);
        banner.hidden = false;
      }
    }
  });
}

export async function deleteProject(project, onDone) {
  const ok = await confirmDialog({
    title: "Delete project",
    message: `Delete "${project.name}"? Everything in it will be removed. This cannot be undone.`,
    confirmLabel: "Delete",
    danger: true,
  });
  if (!ok) return;
  try {
    await api("DELETE", `/api/projects/${project.id}`);
    toast("Project deleted.");
    refreshSidebarProjects();
    onDone();
  } catch (err) {
    toast(errorText(err), "error");
  }
}

export function projectsView() {
  const q = { search: "", ...(() => { const s = savedSort("projects", { sort: "updated", dir: "desc" }, ["name", "updated"]); return { sort: s.sort, order: s.dir }; })(), page: 1, pageSize: savedRows(10) };
  let timer = null;
  const tableHost = h("div", { class: "table-host fill" });
  const search = h("input", { class: "input search-input", type: "search", placeholder: "Search by title or description…", "aria-label": "Search projects" });
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      q.search = search.value;
      q.page = 1;
      load();
    }, 250);
  });

  async function load() {
    const params = new URLSearchParams({ q: q.search, sort: q.sort, order: q.order, page: String(q.page), page_size: String(q.pageSize) });
    try {
      const data = await get(`/api/projects?${params}`);
      if (data.projects.length === 0 && data.total > 0 && q.page > 1) {
        q.page = Math.max(1, Math.ceil(data.total / q.pageSize));
        return load();
      }
      render(data);
    } catch (err) {
      tableHost.replaceChildren(h("div", { class: "banner-error" }, errorText(err, "Could not load projects.")));
    }
  }

  function sortHeader() {
    const active = q.sort === "updated";
    const btn = h("button", { class: "sort-btn", type: "button", "aria-label": "Sort by last updated" }, "Last Updated", h("span", { class: "sort-arrow" }, active ? (q.order === "desc" ? "↓" : "↑") : ""));
    btn.addEventListener("click", () => {
      if (q.sort === "updated") q.order = q.order === "desc" ? "asc" : "desc";
      else {
        q.sort = "updated";
        q.order = "desc";
      }
      remember("sort.projects", { sort: q.sort, dir: q.order });
      load();
    });
    return h("th", { scope: "col" }, btn);
  }

  function nameHeader() {
    const active = q.sort === "name";
    const btn = h("button", { class: "sort-btn", type: "button", "aria-label": "Sort by name" }, "Project Name", h("span", { class: "sort-arrow" }, active ? (q.order === "desc" ? "↓" : "↑") : ""));
    btn.addEventListener("click", () => {
      if (q.sort === "name") q.order = q.order === "asc" ? "desc" : "asc";
      else {
        q.sort = "name";
        q.order = "asc";
      }
      remember("sort.projects", { sort: q.sort, dir: q.order });
      load();
    });
    return h("th", { scope: "col" }, btn);
  }

  function rowActions(p) {
    if (!p.can_edit) return h("span", { class: "muted-text" }, "—");
    return h(
      "div",
      { class: "row-actions" },
      h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => projectModal(p, load) }, "Edit"),
      h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => deleteProject(p, load) }, "Delete"),
    );
  }

  function render(data) {
    const rows = data.projects.map((p) =>
      h(
        "tr",
        {},
        h("td", { class: "cell-strong" }, h("a", { class: "row-link", href: `/projects/${p.id}`, "data-link": "" }, p.name)),
        h("td", { class: "cell-desc" }, p.description || "—"),
        h("td", {}, p.assigned_users.length ? p.assigned_users.map((u) => u.name).join(", ") : "—"),
        h("td", {}, formatDate(p.updated_at)),
        h("td", { class: "cell-actions" }, rowActions(p)),
      ),
    );
    tableHost.replaceChildren(
      rows.length
        ? tableBlock(h("tr", {}, nameHeader(), h("th", { scope: "col" }, "Description"), h("th", { scope: "col" }, "Assigned Users"), sortHeader(), h("th", { scope: "col" }, "")), rows)
        : q.search
          ? emptyState({ iconName: "folder", title: "No projects match", text: "Try a different search.", actions: [h("button", { class: "btn btn-secondary btn-md", type: "button", onclick: () => { search.value = ""; q.search = ""; q.page = 1; load(); } }, "Clear search")] })
          : emptyState({ iconName: "folder", title: "No projects yet", text: "Create your first project to get started.", actions: [h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => projectModal(null, (p) => navigate(`/projects/${p.id}`)) }, icon("plus"), "Create New Project")] }),
      data.total > 0
        ? pagination({
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
          })
        : null,
    );
  }

  const root = h(
    "div",
    { class: "fill" },
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h1", { class: "page-title" }, "Projects"), h("p", { class: "page-subtitle" }, "View and manage all projects in your organization")),
      h("button", { class: "btn btn-primary btn-md", type: "button", onclick: () => projectModal(null, (p) => navigate(`/projects/${p.id}`)) }, icon("plus"), "Create New Project"),
    ),
    h("div", { class: "list-toolbar" }, h("div", { class: "search-wrap" }, icon("search"), search)),
    tableHost,
  );
  tableHost.replaceChildren(skeletonTable(["Project Name", "Description", "Assigned Users", "Last Updated", ""], 5));
  load();
  return root;
}
