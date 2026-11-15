import { get } from "../api.js";
import { errorText, moreMenu } from "../components.js";
import { h } from "../dom.js";
import { navigate } from "../store.js";
import { requirementsSection } from "./requirements.js";
import { projectFrame } from "./projectFrame.js";
import { deleteProject, projectModal } from "./projects.js";

export function projectDetailView(projectId) {
  const root = h("div", { class: "fill" }, h("div", {}, "Loading…"));
  load();
  async function load() {
    let project;
    try {
      project = await get(`/api/projects/${projectId}`);
    } catch (err) {
      root.replaceChildren(
        h("a", { class: "back-link", href: "/projects", "data-link": "" }, "← Projects"),
        h("div", { class: "banner-error", style: "margin-top:16px" }, err.status === 404 ? "This project was not found." : errorText(err)),
      );
      return;
    }
    document.title = `${project.name} — TestMax`;
    const requirements = requirementsSection(project);
    const more = project.can_edit
      ? moreMenu(
          [
            { label: "Edit project", icon: "pencil", onclick: () => projectModal(project, () => load()) },
            { label: "Delete project", danger: true, onclick: () => deleteProject(project, () => navigate("/projects")) },
          ],
          "Project actions",
        )
      : null;
    const actions = h("div", { class: "toolbar-right" }, requirements.action, more);
    const assigned = project.assigned_users.length ? `Assigned: ${project.assigned_users.map((u) => u.name).join(", ")}` : "No users assigned";
    const intro = h(
      "div",
      { class: "fill" },
      h("p", { class: "project-meta" }, project.description ? [project.description, h("span", { class: "dot-sep", "aria-hidden": "true" }, "·")] : null, assigned),
      requirements.el,
    );
    root.replaceChildren(projectFrame(project, "requirements", intro, { actions }));
  }
  return root;
}
