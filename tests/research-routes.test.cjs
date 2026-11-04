const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=name=>fs.readFileSync(path.join(__dirname,'../dist',name),'utf8');
function setup(){
 const location={hash:'#overview'},entries=[],context=vm.createContext({location,localStorage:{getItem(){return null}},history:{pushState(_s,_t,hash){entries.push(hash);location.hash=hash},replaceState(_s,_t,hash){location.hash=hash}},document:{body:{classList:{remove(){}}}},window:{scrollTo(){}},render(){}});
 for(const name of ['data.js','engine.js','views.js'])vm.runInContext(source(name),context);
 const run=s=>vm.runInContext(s,context);return {run,location,entries,state:run('state'),bt:run('bt')};
}
test('research routes identify A shares, Hong Kong and US securities and reject malformed paths',()=>{
 const h=setup();for(const code of ['002281','HK.00700','US.NVDA','US.BRK-B']){const hash=h.run(`researchHref('${code}')`),route=h.run(`parseAppRoute('${hash}')`);assert.equal(route.page,'research');assert.equal(route.code,code)}
 for(const hash of ['#research/%E0%A4%A','#research/%3Cscript%3E','#research/002281/extra'])assert.equal(h.run(`parseAppRoute('${hash}')`).error,true);
 assert.equal(h.run("parseAppRoute('#market')").page,'market');assert.equal(h.run("parseAppRoute('#research')").code,null);
});
test('opening and switching research securities creates distinct URLs without duplicate entries',()=>{
 const h=setup();h.run("setAsset('002281','research')");assert.equal(h.location.hash,'#research/002281');h.run("setAsset('601869')");assert.equal(h.location.hash,'#research/601869');h.run("setAsset('601869')");assert.equal(h.entries.length,2);h.run("go('overview')");assert.equal(h.location.hash,'#overview');h.run("go('research')");assert.equal(h.location.hash,'#research/601869');
});
test('direct route restores asset and browser history can restore the preceding research page',()=>{
 const h=setup();h.location.hash='#research/688981';h.run('applyHashRoute()');assert.equal(h.state.page,'research');assert.equal(h.state.code,'688981');h.run("setAsset('300033')");h.location.hash='#research/688981';h.run('applyHashRoute()');assert.equal(h.state.code,'688981');assert.equal(h.state.dimension,0);assert.equal(h.run('researchRouteUnavailable()'),false);
});
test('foreign direct links wait for catalog instead of rendering a different company, then resolve after load',()=>{
 const h=setup();h.location.hash='#research/US.NVDA';h.run('applyHashRoute()');assert.equal(h.run('researchRouteUnavailable()'),true);assert.equal(h.state.code,'002281');
 h.run("CATALOG.push({code:'US.NVDA',market:'US',currency:'USD'});bt.marketGate=true;resolveResearchRoute()");assert.equal(h.state.code,'US.NVDA');assert.equal(h.bt.lot,1);assert.equal(h.bt.marketGate,false);assert.equal(h.run('researchRouteUnavailable()'),false);
 h.run("CATALOG.splice(CATALOG.findIndex(a=>a.code==='US.NVDA'),1);state.code='601869';resolveResearchRoute()");assert.equal(h.run('researchRouteUnavailable()'),true);assert.equal(h.state.researchRouteCode,'US.NVDA');
});
