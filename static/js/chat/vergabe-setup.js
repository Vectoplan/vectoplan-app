// The project shell remains mounted; only its Settings workspace shows setup.
// Intent tickets are kept in memory and relayed only across verified frame origins.
export function createVergabeSetup({baseUrl, projectId, frame, parentOrigin, finish}) {
  const page = new URL(window.location.href);
  const tender = page.searchParams.get('vergabe') || '';
  if (!['setup', 'partial'].includes(page.searchParams.get('vergabe_import')) ||
      !/^prj_[A-Za-z0-9_-]{8,160}$/.test(projectId) ||
      !/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(tender)) return null;
  const base = new URL(baseUrl);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) return null;
  const loopback = new Set(['localhost', '127.0.0.1', '[::1]']);
  if (loopback.has(base.hostname) && loopback.has(page.hostname)) base.hostname = page.hostname;
  const target = new URL(base.pathname.replace(/\/$/, '') + '/' + tender + '/einrichtung', base);
  target.searchParams.set('project', projectId);
  target.searchParams.set('workspace', '1');
  let ticket = new URLSearchParams(page.hash.slice(1)).get('setup') || '';
  if (!/^[A-Za-z0-9_.-]{1,4096}$/.test(ticket)) ticket = '';
  if (page.hash) {page.hash = ''; window.history.replaceState(window.history.state, '', page.href);}
  let active = true, childReady = false, intentReceived = Boolean(ticket);
  const message = (type, extra = {}) => ({type:'vectoplan:vergabe-setup:' + type, projectId, tenderId:tender, ...extra});
  function sendParent(type) {
    const origin = parentOrigin();
    if (origin) window.parent.postMessage(message(type), origin);
  }
  function initializeChild() {
    if (!active || !childReady) return;
    frame()?.contentWindow?.postMessage(message('init', {setupTicket:ticket}), target.origin);
    ticket = '';
  }
  function onMessage(event) {
    if (!active || event.data?.projectId !== projectId || event.data?.tenderId !== tender) return;
    if (event.source === window.parent && event.origin === parentOrigin() && event.data.type === 'vectoplan:vergabe-setup:init') {
      const value = event.data.setupTicket;
      if (!intentReceived && typeof value === 'string' && /^[A-Za-z0-9_.-]{1,4096}$/.test(value)) {
        ticket = value; intentReceived = true;
      }
      if (intentReceived) sendParent('accepted');
      initializeChild();
      return;
    }
    if (event.source !== frame()?.contentWindow || event.origin !== target.origin) return;
    if (event.data.type === 'vectoplan:vergabe-setup:ready') {
      childReady = true;
      initializeChild();
      sendParent('ready');
    }
    if (event.data.type === 'vectoplan:vergabe-setup:finish') {
      active = false; ticket = '';
      window.removeEventListener('message', onMessage);
      const current = new URL(window.location.href);
      current.searchParams.delete('vergabe_import'); current.searchParams.delete('vergabe');
      window.history.replaceState(window.history.state, '', current.href);
      sendParent('finish');
      finish();
    }
  }
  window.addEventListener('message', onMessage);
  sendParent('ready');
  return {url:target.href, get active() {return active;}};
}
