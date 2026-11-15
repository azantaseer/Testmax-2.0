import { get } from "./api.js";

export const state = { me: null };

let renderer = () => {};
export function setRenderer(fn) {
  renderer = fn;
}

// Unsaved-changes guards: each returns true while its form holds changes that would be lost by leaving the page.
const guards = new Set();
export function addGuard(fn) {
  guards.add(fn);
  return () => guards.delete(fn);
}
export function hasUnsavedChanges() {
  return [...guards].some((g) => g());
}
export const LEAVE_MESSAGE = "You have unsaved changes. Leave this page and lose them?";

export function navigate(path, { replace = false } = {}) {
  if (!replace && hasUnsavedChanges() && !window.confirm(LEAVE_MESSAGE)) return;
  if (replace) history.replaceState({}, "", path);
  else history.pushState({}, "", path);
  renderer();
}

export async function loadMe() {
  try {
    state.me = await get("/api/auth/me");
  } catch {
    state.me = null;
  }
  return state.me;
}

/** Provider names come from the server (the registry), never from page code. Unknown ids are shown as they are. */
export const PROVIDER_LABEL = new Proxy(
  {},
  {
    get: (_, id) => ((state.me && state.me.providers) || []).find((p) => p.id === id)?.label || id,
  },
);
