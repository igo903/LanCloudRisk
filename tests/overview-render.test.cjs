const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(){
 const context=vm.createContext({console,URL,Intl,localStorage:{getItem(){return null},setItem(){}},window:{},document:{querySelector(){return null}}});
 // Load the real renderers and their shared data/model dependencies in page order.
 for(const name of ['data.js','engine.js','app.js','views.js','research-views.js','strategy-views.js','evidence-views.js','risk-engine.js','risk-views.js','market-cards.js']){
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',name),'utf8'),context,{filename:name});
 }
 const run=code=>vm.runInContext(code,context);
 run("mode='uploaded';uploaded={data_kind:'uploaded',instruments:{},external:[],geopolitics:{method:'public-evidence-v2',items:[]}};");
 return {run,context};
}
test('the full overview renders all eight card/table layout combinations with real child renderers',()=>{
 const {run}=setup();
 for(const external of ['cards','table'])for(const domestic of ['cards','table'])for(const watch of ['cards','table']){
  run(`state.riskLayouts=${JSON.stringify({external,domestic,watch})}`);
  const html=run('riskOverview()');
  for(const id of ['risk-external','risk-domestic','risk-watch','risk-method'])assert.ok(html.includes(`id="${id}"`),`${external}/${domestic}/${watch}: ${id}`);
  assert.match(html,/B1 · 政治/);assert.match(html,/B2 · 战争/);
  if(watch==='cards'){
   assert.match(html,/class="risk-watch-grid"/);assert.match(html,/class="risk-stock"/);
  }else assert.match(html,/data-risk-table="watch"/);
  assert.ok(html.includes('href="#research/002281"'));
  assert.ok(html.includes("openWatchRiskDetails('002281')"));
 }
});
test('watch risk cards retain missing-data states, research links and scoring-dialog actions',()=>{
 const {run}=setup(),html=run('riskWatchDisplay(riskSnapshot().watch)');
 assert.match(html,/价格证据不足/);assert.match(html,/证据不足/);
 assert.match(html,/aria-haspopup="dialog"/);assert.match(html,/七维评分、缺失项与依据/);
 assert.doesNotMatch(html,/<details/);
});
test('an empty watchlist and a legacy geopolitical snapshot still render the complete overview',()=>{
 const {run}=setup();run("WATCH_CODES.splice(0,WATCH_CODES.length);uploaded.geopolitics={as_of:'2026-11-01',items:[]}");
 const html=run('riskOverview()');assert.match(html,/id="risk-watch"/);assert.match(html,/请刷新行情/);
 assert.match(html,/class="risk-watch-grid"><\/div>/);
});
