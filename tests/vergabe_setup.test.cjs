const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../static/js/chat/vergabe-setup.js'),'utf8').replace('export function','function');
const project = 'prj_test_12345678', tender = '27394fd6-070f-5619-9686-82a7a36427af';
function fixture({embedded=true,query='?vergabe_import=setup&vergabe='+tender,hash=''}={}) {
  const sent=[], handlers={}, changes=[];
  let finishes=0;
  const parent={postMessage:(data,origin)=>sent.push({to:'parent',data,origin})};
  const frame={contentWindow:{postMessage:(data,origin)=>sent.push({to:'child',data,origin})}};
  const window={parent,location:new URL('http://localhost:5103/project='+project+'/einstellungen'+query+hash),addEventListener:(name,fn)=>handlers[name]=fn,removeEventListener:(name)=>delete handlers[name]};
  if(!embedded)window.parent=window;
  window.history={state:null,replaceState:(state,_,url)=>{changes.push(url);window.location=new URL(url,window.location);}};
  const ctx=vm.createContext({window,URL,URLSearchParams});vm.runInContext(source,ctx);
  const setup=ctx.createVergabeSetup({baseUrl:'http://127.0.0.1:5203/vergabe',projectId:project,frame:()=>frame,parentOrigin:()=>embedded?'http://localhost:5200':'',finish:()=>finishes++});
  function message(type,{from='child',origin=from==='child'?'http://localhost:5203':'http://localhost:5200',data={},sender}={}) {
    handlers.message?.({source:sender||(from==='child'?frame.contentWindow:parent),origin,data:{type:'vectoplan:vergabe-setup:'+type,projectId:project,tenderId:tender,...data}});
  }
  return {setup,window,sent,changes,message,finishes:()=>finishes,frame};
}
test('normal/invalid links do not enable setup',()=>{
  for(const query of ['', '?vergabe_import=setup&vergabe=bad', '?vergabe_import=complete&vergabe='+tender]) assert.equal(fixture({query}).setup,null);
});
test('setup uses the authenticated host and relays confirmed intent across both frame boundaries',()=>{
  const f=fixture();assert.equal(new URL(f.setup.url).origin,'http://localhost:5203');
  assert.equal(new URL(f.setup.url).searchParams.get('workspace'),'1');
  f.message('init',{from:'parent',data:{setupTicket:'signed.intent'}});
  assert.equal(f.sent.filter(x=>x.to==='child').length,0);
  f.message('ready');
  assert.equal(f.sent.filter(x=>x.to==='child').at(-1).data.setupTicket,'signed.intent');
  f.message('init',{from:'parent',data:{setupTicket:'signed.intent'}});
  assert.equal(f.sent.filter(x=>x.to==='child'&&x.data.setupTicket).length,1);
});
test('late parent intent is delivered after child readiness',()=>{
  const f=fixture();f.message('ready');f.message('init',{from:'parent',data:{setupTicket:'late.intent'}});
  assert.equal(f.sent.filter(x=>x.to==='child').at(-1).data.setupTicket,'late.intent');
});
test('untrusted, stale and mismatched completion cannot unlock the workspace',()=>{
  const f=fixture();
  f.message('finish',{origin:'https://evil.test'});f.message('finish',{sender:{}});
  f.message('finish',{data:{projectId:'prj_other_12345678'}});
  assert.equal(f.finishes(),0);assert.equal(f.setup.active,true);
  f.message('finish');f.message('finish');
  assert.equal(f.finishes(),1);assert.equal(f.setup.active,false);
  assert.equal(f.window.location.search,'');
  assert.equal(f.sent.filter(x=>x.data.type.endsWith(':finish')).length,1);
});
test('direct App setup keeps the ticket out of requests and exits to settings',()=>{
  const f=fixture({embedded:false,hash:'#setup=direct.intent'});
  assert.equal(f.window.location.hash,'');assert.equal(new URL(f.setup.url).hash,'');
  f.message('ready');assert.equal(f.sent.at(-1).data.setupTicket,'direct.intent');
  f.message('finish');assert.equal(f.finishes(),1);assert.equal(f.window.location.search,'');
});
