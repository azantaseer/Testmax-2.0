import { mount } from "./dom.js";
import { LEAVE_MESSAGE, hasUnsavedChanges, loadMe, navigate, setRenderer, state } from "./store.js";
import { authView } from "./views/auth.js";
import { shell } from "./views/shell.js";
import { projectsView } from "./views/projects.js";
import { projectDetailView } from "./views/projectDetail.js";
import { requirementDetailView } from "./views/requirements.js";
import { aiSettingsView } from "./views/aiSettings.js";
import { mySettingsView } from "./views/mySettings.js";
import { usersView } from "./views/users.js";
import { runnersView } from "./views/runners.js";
import { scanResultView } from "./views/scan.js";
import { testCaseDetailView, testCasesView } from "./views/testCases.js";
import { testScriptDetailView, testScriptsView } from "./views/testScripts.js";
import { testRunView } from "./views/testRuns.js";
import { testBatchDetailView, testBatchRunView, testBatchesView } from "./views/testBatches.js";

const root = document.getElementById("app");

function render() {
  shownPath = location.pathname + location.search;
  const url = new URL(location.href);
  const path = url.pathname.replace(/\/+$/, "") || "/";

  if (!state.me) {
    if (path !== "/authentication") return navigate("/authentication?view=sign-in", { replace: true });
    return mount(root, authView(url.searchParams.get("view") || "sign-in"));
  }

  if (path === "/" || path === "/authentication") return navigate("/projects", { replace: true });
  if (path === "/settings/ai") {
    if (state.me.user.role !== "super_admin") return navigate("/projects", { replace: true });
    return mount(root, shell("ai", aiSettingsView()));
  }
  if (path === "/settings/me") return mount(root, shell("me", mySettingsView()));
  if (path === "/users") {
    if (state.me.user.role === "user") return navigate("/projects", { replace: true });
    return mount(root, shell("users", usersView()));
  }
  if (path === "/runners") {
    if (state.me.user.role === "user") return navigate("/projects", { replace: true });
    return mount(root, shell("runners", runnersView()));
  }
  const scanDetail = path.match(/^\/projects\/([0-9a-f-]{36})\/scans\/([0-9a-f-]{36})$/);
  if (scanDetail) return mount(root, shell("projects", scanResultView(scanDetail[1], scanDetail[2]), scanDetail[1]));
  const reqDetail = path.match(/^\/projects\/([0-9a-f-]{36})\/requirements\/([0-9a-f-]{36})$/);
  if (reqDetail) return mount(root, shell("projects", requirementDetailView(reqDetail[1], reqDetail[2]), reqDetail[1]));
  const caseDetail = path.match(/^\/projects\/([0-9a-f-]{36})\/test-cases\/([0-9a-f-]{36})$/);
  if (caseDetail) return mount(root, shell("projects", testCaseDetailView(caseDetail[1], caseDetail[2]), caseDetail[1]));
  const caseList = path.match(/^\/projects\/([0-9a-f-]{36})\/test-cases$/);
  if (caseList) return mount(root, shell("projects", testCasesView(caseList[1]), caseList[1]));
  const runDetail = path.match(/^\/projects\/([0-9a-f-]{36})\/test-runs\/([0-9a-f-]{36})$/);
  if (runDetail) return mount(root, shell("projects", testRunView(runDetail[1], runDetail[2]), runDetail[1]));
  const batchRun = path.match(/^\/projects\/([0-9a-f-]{36})\/test-batch-runs\/([0-9a-f-]{36})$/);
  if (batchRun) return mount(root, shell("projects", testBatchRunView(batchRun[1], batchRun[2]), batchRun[1]));
  const batchDetail = path.match(/^\/projects\/([0-9a-f-]{36})\/test-batches\/([0-9a-f-]{36})$/);
  if (batchDetail) return mount(root, shell("projects", testBatchDetailView(batchDetail[1], batchDetail[2]), batchDetail[1]));
  const batchList = path.match(/^\/projects\/([0-9a-f-]{36})\/test-batches$/);
  if (batchList) return mount(root, shell("projects", testBatchesView(batchList[1]), batchList[1]));
  const scriptDetail = path.match(/^\/projects\/([0-9a-f-]{36})\/test-scripts\/([0-9a-f-]{36})$/);
  if (scriptDetail) return mount(root, shell("projects", testScriptDetailView(scriptDetail[1], scriptDetail[2]), scriptDetail[1]));
  const scriptList = path.match(/^\/projects\/([0-9a-f-]{36})\/test-scripts$/);
  if (scriptList) return mount(root, shell("projects", testScriptsView(scriptList[1]), scriptList[1]));
  const detail = path.match(/^\/projects\/([0-9a-f-]{36})$/);
  if (detail) return mount(root, shell("projects", projectDetailView(detail[1]), detail[1]));
  if (path !== "/projects") return navigate("/projects", { replace: true });
  mount(root, shell("projects", projectsView()));
}

// Internal links use data-link so navigation stays in the app.
document.addEventListener("click", (event) => {
  const a = event.target.closest("a[data-link]");
  if (!a || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
  event.preventDefault();
  navigate(a.getAttribute("href"));
});
let shownPath = location.pathname + location.search;
window.addEventListener("popstate", () => {
  const target = location.pathname + location.search;
  if (target !== shownPath && hasUnsavedChanges() && !window.confirm(LEAVE_MESSAGE)) {
    history.pushState({}, "", shownPath); // stay where we were
    return;
  }
  shownPath = target;
  render();
});
window.addEventListener("beforeunload", (event) => {
  if (hasUnsavedChanges()) {
    event.preventDefault();
    event.returnValue = "";
  }
});

setRenderer(render);
await loadMe();
render();
