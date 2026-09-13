const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../static/js/chat/main.js'),'utf8');
const fn=source.match(/    const normalizeParcelGridState = \(value\) => \{[^]*?\n    };/)[0];
const normalize=vm.runInNewContext(`${fn}\nnormalizeParcelGridState`);

test('parent bridge preserves deep, collapsed and facade guides including their active key',()=>{
  const guides=Array.from({length:100},(_,i)=>({parcelId:i===99?'building:existing':`parcel-${i}`,
    startLonLat:[13,52],endLonLat:[13.1,52],depthMeters:i}));
  const state={schemaVersion:'vectoplan-parcel-grid-state.v1',influenceMeters:15,
    activeParcelId:'building:existing',activeGuideKey:'facade-key',guides};
  assert.deepEqual(JSON.parse(JSON.stringify(normalize(state))),{...state,mode:'boundary',setbackMeters:0});
});
