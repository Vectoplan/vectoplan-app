const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../static/js/chat/main.js'), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/^void boot\(\);\s*$/m, '');
const project = 'prj_test_12345678';
const base = `/project=${project}`;
const workspaces = [
  ['project', 'einstellungen'], ['map', 'map'], ['3d', '3d'], ['2d', '2d'],
  ['lv', 'lv'], ['files', 'file'], ['structural_calculation', 'statik'],
  ['energy_calculation', 'energie'], ['sound_protection_calculation', 'schallschutz'],
];

function fixture(path = `${base}/2d`, embedded = false) {
  const changes = [], handlers = {}, sent = [];
  let reloads = 0;
  const window = {
    location: new URL(path, 'http://localhost:5103'),
    history: {}, addEventListener: (name, handler) => { handlers[name] = handler; },
  };
  window.location.reload = () => { reloads++; };
  window.parent = embedded ? { postMessage: (...args) => sent.push(args) } : window;
  for (const action of ['pushState', 'replaceState']) {
    window.history[action] = (state, _, url) => {
      changes.push({ action, url, state });
      window.location = new URL(url, window.location.href);
      window.location.reload = () => { reloads++; };
    };
  }
  const uiState = { workspaceMode: '2d' };
  const context = vm.createContext({ window, uiState, URL, console });
  vm.runInContext(source, context);
  Object.assign(context, {
    platformParentOrigin: () => embedded ? 'http://localhost:5200' : '',
    projectPublicId: () => project,
    isWorkspaceModeAllowed: () => true,
    setUiMode: mode => { uiState.workspaceMode = mode; return mode; },
    activateWorkspaceFrame() {}, setRawOpenUrl() {}, prepareEditorPreloadFrame() {},
    cacheBustLocalUrl: target => target, hardSwapIframe() {}, versionsClose() {},
    showStatus() {}, persistWorkspaceMode: async () => {},
    filesUrl: () => '/files', resolve2dUrl: async () => '/2d', editorUrl: () => '/3d',
  });
  const navigate = (workspace, source = 'workspace_mode') => context.relayProjectNavigation(
    { projectPublicId: project, workspace }, source,
  );
  return { context, window, uiState, changes, handlers, sent, navigate, reloads: () => reloads };
}

test('every workspace has a canonical URL and accepts its suffix as a mode', () => {
  const f = fixture();
  for (const [mode, suffix] of workspaces) {
    assert.equal(f.context.normalizeMode(suffix), mode);
    f.navigate(mode);
    assert.equal(f.window.location.pathname, `${base}/${suffix}`);
    const count = f.changes.length;
    f.navigate(mode);
    assert.equal(f.changes.length, count);
  }
});

test('initial navigation replaces legacy mode parameters while retaining embed options', () => {
  const f = fixture(`${base}?mode=files&allow_embed=1&client_source=desktop`);
  f.navigate('files', 'initial-workspace');
  assert.equal(f.changes[0].action, 'replaceState');
  assert.equal(f.window.location.pathname, `${base}/file`);
  assert.equal(f.window.location.searchParams.has('mode'), false);
  assert.equal(f.window.location.searchParams.get('allow_embed'), '1');
  assert.equal(f.window.location.searchParams.get('client_source'), 'desktop');
  f.context.wireWorkspaceHistory();
  f.handlers.popstate();
  assert.equal(f.reloads(), 1);
  assert.equal(f.changes.length, 1);
});

test('project updates preserve the current workspace and the URL prefix', () => {
  const f = fixture(`/app${base}/file`);
  f.uiState.workspaceMode = 'files';
  f.navigate('');
  assert.equal(f.window.location.pathname, `/app${base}/file`);
  assert.equal(f.changes.length, 0);
});

test('embedded shells relay all workspace paths and leave history to the platform', () => {
  const f = fixture(undefined, true);
  f.context.wireWorkspaceHistory();
  assert.equal(f.handlers.popstate, undefined);
  for (const [mode, suffix] of workspaces) {
    f.navigate(mode);
    const [message, origin] = f.sent.at(-1);
    assert.equal(message.detail.workspacePath, `${base}/${suffix}`);
    assert.equal(message.detail.workspace, mode);
    assert.equal(origin, 'http://localhost:5200');
  }
  assert.equal(f.changes.length, 0);
});

test('the URL switches immediately even while persistence is still pending', async () => {
  const f = fixture();
  let finishPersistence;
  f.context.persistWorkspaceMode = () => new Promise(resolve => { finishPersistence = resolve; });
  const pending = f.context.setWorkspaceMode('files');
  assert.equal(f.window.location.pathname, `${base}/file`);
  finishPersistence();
  assert.equal(await pending, true);
});

test('a slow 2D URL lookup cannot overwrite a more recent workspace selection', async () => {
  const f = fixture();
  const frames = [];
  let resolveCad;
  f.context.hardSwapIframe = target => frames.push(target);
  f.context.resolve2dUrl = () => new Promise(resolve => { resolveCad = resolve; });
  const pending = f.context.setWorkspaceMode('2d');
  await f.context.setWorkspaceMode('files');
  resolveCad('/2d');
  assert.equal(await pending, false);
  assert.deepEqual(frames, ['/files']);
  assert.equal(f.window.location.pathname, `${base}/file`);
});

test('3D navigation is reflected before editor readiness', async () => {
  const f = fixture();
  assert.equal(await f.context.setWorkspaceMode('3d'), true);
  assert.equal(f.window.location.pathname, `${base}/3d`);
  assert.ok(f.uiState.pendingEditorWorkspace);
});
