import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";


const testDir = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(testDir, "..");
const formSource = fs.readFileSync(
  path.join(appRoot, "static", "js", "project", "project_form.js"),
  "utf8"
);
const shellSource = fs.readFileSync(
  path.join(appRoot, "static", "js", "chat", "main.js"),
  "utf8"
);


function extractFunction(source, name) {
  const syncMarker = `function ${name}(`;
  const asyncMarker = `async function ${name}(`;
  let start = source.indexOf(asyncMarker);
  if (start < 0) start = source.indexOf(syncMarker);
  assert.notEqual(start, -1, `${name} must exist`);

  const signatureEnd = source.indexOf(") {", start);
  assert.notEqual(signatureEnd, -1, `${name} signature must be complete`);
  const bodyStart = signatureEnd + 2;
  let depth = 0;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`Could not extract ${name}`);
}


function loadFunction(source, name, bindings) {
  const context = vm.createContext({ ...bindings });
  vm.runInContext(`${extractFunction(source, name)}; this.result = ${name};`, context);
  return context.result;
}


test("create completion canonicalizes history without a full-page reload", () => {
  const replacements = [];
  let parentAssigns = 0;
  let ownAssigns = 0;
  const fakeWindow = {
    history: {
      replaceState(state, title, target) {
        replacements.push({ state, title, target });
      },
    },
    location: { assign() { ownAssigns += 1; } },
    parent: { location: { assign() { parentAssigns += 1; } } },
  };
  const finishCreateNavigation = loadFunction(
    formSource,
    "finishCreateNavigation",
    {
      state: { currentProject: {} },
      normalizeSafeRedirectUrl: () => "/project=prj_12345678",
      getProjectPublicId: () => "prj_12345678",
      getWindow: () => fakeWindow,
      DEFAULT_PROJECT_NEW_URL: "/project=new",
    }
  );

  assert.equal(
    finishCreateNavigation({
      project: { public_id: "prj_12345678" },
      redirectUrl: "/project=prj_12345678",
    }),
    true
  );
  assert.equal(parentAssigns, 0);
  assert.equal(ownAssigns, 0);
  assert.deepEqual(JSON.parse(JSON.stringify(replacements)), [
    {
      state: {
        vectoplanProjectId: "prj_12345678",
        vectoplanWorkspace: "project",
      },
      title: "",
      target: "/project=prj_12345678",
    },
  ]);
});


test("created event requests the project workspace in the App shell", async () => {
  const workspaceCalls = [];
  const navigationCalls = [];
  const handleProjectSaved = loadFunction(shellSource, "handleProjectSaved", {
    extractProjectFromDetail: (detail) => detail.project,
    updateAppConfigProject() {},
    setProjectDataset() {},
    syncWorkspaceGating() {},
    refreshProjectSidebar() {},
    refreshVersionsUI: async () => {},
    uiState: {},
    relayProjectNavigation(detail, eventType) {
      navigationCalls.push({ detail, eventType });
    },
    setWorkspaceMode(mode, options) {
      workspaceCalls.push({ mode, options });
      return Promise.resolve(true);
    },
    showStatus() {},
    console,
    Date,
  });
  const detail = { project: { public_id: "prj_12345678" } };

  handleProjectSaved(detail, "vectoplan:project:created");
  await Promise.resolve();

  assert.deepEqual(JSON.parse(JSON.stringify(workspaceCalls)), [
    {
      mode: "project",
      options: { persist: false, reason: "vectoplan:project:created" },
    },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(navigationCalls)), [
    { detail, eventType: "vectoplan:project:created" },
  ]);
});


test("project workspace mode swaps only the viewer frame to projectUrl", async () => {
  const swaps = [];
  const activations = [];
  const relays = [];
  const setWorkspaceMode = loadFunction(shellSource, "setWorkspaceMode", {
    normalizeMode: (mode) => mode,
    isWorkspaceModeAllowed: () => true,
    showStatus() {},
    uiState: { editorReady: true, workspaceMode: "project" },
    setUiMode: (mode) => mode,
    activateWorkspaceFrame: (mode) => activations.push(mode),
    vergabeSetup: { active: false },
    projectUrl: () => "/project=prj_12345678",
    viewerFrame: () => ({ src: "/project=new" }),
    cacheBustLocalUrl: (target) => `${target}?r=test`,
    hardSwapIframe: (target, options) => {
      swaps.push({ target, options });
      return true;
    },
    versionsClose() {},
    relayProjectNavigation: (detail, reason) => relays.push({ detail, reason }),
    projectPublicId: () => "prj_12345678",
    persistWorkspaceMode: async () => {},
    console,
  });

  assert.equal(
    await setWorkspaceMode("project", {
      persist: false,
      reason: "vectoplan:project:created",
    }),
    true
  );
  assert.deepEqual(activations, ["project"]);
  assert.deepEqual(JSON.parse(JSON.stringify(swaps)), [
    {
      target: "/project=prj_12345678?r=test",
      options: { mode: "project", title: "Projekt" },
    },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(relays)), [
    {
      detail: { projectPublicId: "prj_12345678", workspace: "project" },
      reason: "vectoplan:project:created",
    },
  ]);
});
