import { h } from "../dom.js";
import { state } from "../store.js";
import { setCrumbs } from "./shell.js";

const TABS = [
  { key: "requirements", label: "Requirements", path: "" },
  { key: "test-cases", label: "Test Cases", path: "/test-cases" },
  { key: "test-scripts", label: "Test Scripts", path: "/test-scripts" },
];

/**
 * The prod-style page frame of everything inside a project: breadcrumb, pill tab bar, then the page.
 * `trail` is the part after the section, e.g. [{ label: "REQ-001" }] (the last item is the current page).
 */
export function projectFrame(project, active, content, { trail = [], actions = null } = {}) {
  const section = TABS.find((t) => t.key === active);
  const sectionHref = `/projects/${project.id}${section.path}`;
  const crumbs = [
    { label: state.me.tenant.name, kind: "org" },
    { label: "Projects", href: "/projects", kind: "folder" },
    { label: project.name, href: `/projects/${project.id}`, kind: "folder" },
  ];
  if (active !== "requirements" || trail.length) crumbs.push({ label: section.label, href: trail.length ? sectionHref : null, kind: "page" });
  trail.forEach((item) => crumbs.push({ label: item.label, href: item.href }));
  setCrumbs(crumbs);
  const tabs = h(
    "div",
    { class: "tabbar", role: "navigation", "aria-label": "Project sections" },
    ...TABS.map((t) => h("a", { class: "tab", href: `/projects/${project.id}${t.path}`, "data-link": "", "aria-current": t.key === active ? "page" : null }, t.label)),
  );
  return h("div", { class: "fill" }, h("div", { class: trail.length ? "tabbar-row detail" : "tabbar-row" }, tabs, actions), h("div", { class: "frame-body fill" }, content));
}

export function formatDate(iso) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "2-digit", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export function formatDay(iso) {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" });
}

export function typePill(type) {
  const label = type ? type.charAt(0) + type.slice(1).toLowerCase() : "";
  return h("span", { class: `tpill ${String(type || "").toLowerCase()}` }, label);
}
