import { get, post } from "../api.js";
import { h, icon } from "../dom.js";
import { navigate, state } from "../store.js";

function initials(name) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : (parts[0] || "?").slice(0, 2);
  return letters.toUpperCase();
}

async function signOut() {
  try {
    await post("/api/auth/logout");
  } catch {
    /* even if the call fails, leave the app */
  }
  state.me = null;
  navigate("/authentication?view=sign-in", { replace: true });
}

function userMenu() {
  const { user } = state.me;
  const panel = h("div", { class: "menu-panel", role: "menu", hidden: true });
  const chip = h(
    "button",
    { class: "user-chip", type: "button", "aria-haspopup": "menu", "aria-expanded": "false" },
    h("span", { class: "avatar" }, initials(user.name)),
    h("span", { class: "user-text" }, h("span", { class: "user-name" }, user.name), h("span", { class: "user-email" }, user.email)),
    icon("chevronDown"),
  );
  panel.append(h("a", { class: "menu-item", role: "menuitem", href: "/settings/me", "data-link": "" }, icon("user"), "My Settings"));
  if (user.role === "super_admin") {
    panel.append(
      h("a", { class: "menu-item", role: "menuitem", href: "/settings/ai", "data-link": "" }, icon("sparkle"), "Organization AI Model"),
    );
  }
  panel.append(h("button", { class: "menu-item", role: "menuitem", type: "button", onclick: signOut }, icon("logout"), "Sign out"));

  const wrap = h("div", { class: "user-menu" }, chip, panel);
  function close() {
    panel.hidden = true;
    chip.setAttribute("aria-expanded", "false");
  }
  chip.addEventListener("click", (event) => {
    event.stopPropagation();
    panel.hidden = !panel.hidden;
    chip.setAttribute("aria-expanded", String(!panel.hidden));
  });
  panel.addEventListener("click", close);
  const onDocClick = (event) => {
    if (!wrap.isConnected) return document.removeEventListener("click", onDocClick);
    if (!wrap.contains(event.target)) close();
  };
  document.addEventListener("click", onDocClick);
  wrap.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      close();
      chip.focus();
    }
  });
  return wrap;
}

// ---------- breadcrumb (lives in the top bar) ----------
let crumbHost = null;
const CRUMB_ICON = { org: "building", folder: "folder", page: "file" };

/** items: [{ label, href?, kind?: "org" | "folder" | "page" }]; the last item is the current page. */
export function setCrumbs(items) {
  if (!crumbHost) return;
  crumbHost.replaceChildren();
  items.forEach((item, i) => {
    const last = i === items.length - 1;
    if (i) crumbHost.append(h("span", { class: "sep", "aria-hidden": "true" }, "/"));
    const inner = [item.kind ? icon(CRUMB_ICON[item.kind]) : null, h("span", { class: "crumb-text" }, item.label)];
    crumbHost.append(
      item.href && !last
        ? h("a", { class: "crumb", href: item.href, "data-link": "" }, inner)
        : h("span", { class: last ? "crumb current" : "crumb", "aria-current": last ? "page" : null }, inner),
    );
  });
}

const SECTION_CRUMB = { projects: ["Projects", "folder"], users: ["Users", "page"], runners: ["Runners", "page"], ai: ["Organization AI Model", "page"], me: ["My Settings", "page"] };

let sidebarProjects = null; // the container currently on screen
let currentProjectId = null;

async function fillSidebarProjects() {
  const host = sidebarProjects;
  if (!host) return;
  try {
    const data = await get("/api/projects?page_size=100&sort=name&order=asc");
    if (!host.isConnected) return;
    host.replaceChildren(
      ...data.projects.map((p) =>
        h("a", { class: "nav-sub", href: `/projects/${p.id}`, "data-link": "", title: p.name, "aria-current": p.id === currentProjectId ? "page" : null }, icon("doc"), h("span", { class: "nav-sub-text" }, p.name)),
      ),
    );
  } catch {
    host.replaceChildren();
  }
}

export function refreshSidebarProjects() {
  fillSidebarProjects();
}

export function shell(active, content, projectId = null) {
  currentProjectId = projectId;
  const sidebar = h("aside", { class: "sidebar", "aria-label": "Sidebar" });
  const handle = h("button", { class: "collapse-handle", type: "button", "aria-label": "Collapse sidebar", "aria-expanded": "true" }, icon("chevronLeft"));
  handle.addEventListener("click", () => setCollapsed(!sidebar.classList.contains("collapsed")));
  sidebar.append(
    h(
      "div",
      { class: "sidebar-inner" },
      h("div", { class: "org-box", title: state.me.tenant.name }, state.me.tenant.name),
      h("div", { class: "sidebar-divider" }),
      h("a", { class: "nav-item", href: "/projects", "data-link": "", "aria-current": active === "projects" ? "page" : null }, icon("doc"), "Projects"),
      (sidebarProjects = h("div", { class: "nav-sub-list" })),
      state.me.user.role !== "user"
        ? h("a", { class: "nav-item", href: "/users", "data-link": "", "aria-current": active === "users" ? "page" : null }, icon("users"), "Users")
        : null,
      state.me.user.role !== "user"
        ? h("a", { class: "nav-item", href: "/runners", "data-link": "", "aria-current": active === "runners" ? "page" : null }, icon("runner"), "Runners")
        : null,
    ),
    handle,
  );
  fillSidebarProjects();
  // Narrow windows: the sidebar starts closed, opens over the page and closes again after a choice or a click outside.
  const narrow = () => window.matchMedia("(max-width: 900px)").matches;
  const scrim = h("div", { class: "sidebar-scrim", "aria-hidden": "true" });
  const setCollapsed = (collapsed) => {
    sidebar.classList.toggle("collapsed", collapsed);
    handle.setAttribute("aria-expanded", String(!collapsed));
    handle.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    handle.replaceChildren(icon(collapsed ? "chevronRight" : "chevronLeft"));
  };
  if (narrow()) setCollapsed(true);
  scrim.addEventListener("click", () => setCollapsed(true));
  sidebar.addEventListener("click", (event) => {
    if (narrow() && event.target.closest("a[data-link]")) setCollapsed(true);
  });
  sidebar.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && narrow()) {
      setCollapsed(true);
      handle.focus();
    }
  });
  const noModel = state.me.ai && state.me.ai.source === "none";
  const banner = noModel
    ? h(
        "div",
        { class: "ai-banner", role: "alert" },
        "No AI model is selected yet, so requirement generation and scans are paused. ",
        state.me.user.role === "super_admin"
          ? h("a", { href: "/settings/ai", "data-link": "" }, "Choose the organization's model")
          : h("span", {}, "Ask your Super Admin to choose one, or "),
        state.me.user.role === "super_admin" ? null : h("a", { href: "/settings/me", "data-link": "" }, "choose your own"),
      )
    : null;
  const skip = h("a", { class: "skip-link", href: "#main" }, "Skip to main content");
  skip.addEventListener("click", (event) => {
    event.preventDefault(); // a #hash change would make the router draw the page again
    document.getElementById("main")?.focus();
  });
  const page = h(
    "div",
    { class: noModel ? "shell has-banner" : "shell" },
    skip,
    banner,
    h(
      "header",
      { class: "topbar" },
      h("a", { class: "topbar-brand", href: "/projects", "data-link": "", "aria-label": "TestMax home" }, h("img", { class: "topbar-logo", src: "/static/assets/testmax-logo-full.svg", alt: "" })),
      (crumbHost = h("nav", { class: "crumbs", "aria-label": "Breadcrumb" })),
      userMenu(),
    ),
    h("div", { class: "shell-body" }, sidebar, scrim, h("main", { class: "main", id: "main", tabindex: "-1" }, content)),
  );
  const [label, kind] = SECTION_CRUMB[active] || ["Projects", "folder"];
  setCrumbs([{ label: state.me.tenant.name, kind: "org" }, { label, kind }]);
  return page;
}
