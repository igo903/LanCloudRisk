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
test('all six benchmark ETFs have distinct backtest URLs and restore their selected security',()=>{
 const h=setup(),codes=Array.from(h.run('BENCH_CODES'));for(const code of codes){h.location.hash=h.run(`backtestHref('${code}')`);h.run('applyHashRoute()');assert.equal(h.state.page,'backtest');assert.equal(h.state.code,code);assert.equal(h.run('researchRouteUnavailable()'),false)}assert.equal(new Set(codes.map(code=>h.run(`backtestHref('${code}')`))).size,6);
 h.run("setAsset('510300','backtest');setAsset('588080')");assert.equal(h.location.hash,'#backtest/588080');h.run("go('research')");assert.equal(h.location.hash,'#research/588080');h.location.hash='#backtest/510300';h.run('applyHashRoute()');assert.equal(h.state.page,'backtest');assert.equal(h.state.code,'510300');
});
test('invalid or deferred backtest links cannot show another security under the requested URL',()=>{
 const h=setup();h.location.hash='#backtest/%3Cscript%3E';h.run('applyHashRoute()');assert.equal(h.run('researchRouteUnavailable()'),true);
 h.location.hash='#backtest/US.NVDA';h.run('applyHashRoute()');assert.equal(h.run('researchRouteUnavailable()'),true);h.run("CATALOG.push({code:'US.NVDA',market:'US',currency:'USD'});resolveResearchRoute()");assert.equal(h.state.page,'backtest');assert.equal(h.state.code,'US.NVDA');assert.equal(h.run('researchRouteUnavailable()'),false);
});
test('fundamentals and sentiment have separate per-security URLs and restore page plus selection',()=>{
 const h=setup();for(const page of ['valuation','sentiment']){for(const code of ['002281','588080']){h.location.hash=h.run(`assetPageHref('${page}','${code}')`);h.run('applyHashRoute()');assert.equal(h.state.page,page);assert.equal(h.state.code,code);assert.equal(h.run('researchRouteUnavailable()'),false)}h.run("setAsset('601869')");assert.equal(h.location.hash,'#'+page+'/601869');}
 h.run("go('valuation')");assert.equal(h.location.hash,'#valuation/601869');h.run("setAsset('688981')");assert.equal(h.location.hash,'#valuation/688981');h.location.hash='#sentiment/002281';h.run('applyHashRoute()');assert.equal(h.state.page,'sentiment');assert.equal(h.state.code,'002281');
});
test('new study routes reject malformed codes and wait for an international catalog before rendering',()=>{
 const h=setup();for(const page of ['valuation','sentiment']){h.location.hash='#'+page+'/%3Cscript%3E';h.run('applyHashRoute()');assert.equal(h.run('researchRouteUnavailable()'),true);h.location.hash='#'+page+'/HK.00700';h.run('applyHashRoute()');assert.equal(h.run('researchRouteUnavailable()'),true)}
 h.run("CATALOG.push({code:'HK.00700',market:'HK',currency:'HKD'});resolveResearchRoute()");assert.equal(h.state.page,'sentiment');assert.equal(h.state.code,'HK.00700');assert.equal(h.run('researchRouteUnavailable()'),false);
});
test('watch table renders both study links for stocks and ETFs, within the research cell and keeping all 19 columns aligned',()=>{
 const h=setup();h.run("icon=()=>'';watchSortHeader=(key,label)=>'<th>'+label+'</th>';sortWatchRows=list=>list;WATCH_FLOW_WINDOWS=[1,3,5,10,20,60];watchFlowCells=()=>'<td>—</td>'.repeat(6);watchJobText=()=>'';watchManagementCell=()=>'<td>管理</td>'");
 for(const code of ['002281','588080']){const html=h.run(`watchTable([byCode('${code}')])`);assert.doesNotMatch(html,/<th[^>]*>(?:基本面|情绪面)<\/th>/);const research=html.match(/<td class="watch-research-column">([\s\S]*?)<\/td>/)?.[1];assert.ok(research);assert.match(research,/>七维分析<\/a>[\s\S]*>基本面<\/a>[\s\S]*>情绪面<\/a>[\s\S]*>回测<\/a>/);assert.ok(html.includes('href="#valuation/'+code+'"'));assert.ok(html.includes('href="#sentiment/'+code+'"'));assert.equal((html.match(/<td(?:\s|>)/g)||[]).length,19)}assert.match(h.run('watchTable([])'),/colspan="19"/);
});
