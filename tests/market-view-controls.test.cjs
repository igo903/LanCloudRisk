const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=name=>fs.readFileSync(path.join(__dirname,'../dist',name),'utf8');
function harness(){
 const d={dataset:{},open:false,innerHTML:'',attributes:{},setAttribute(k,v){this.attributes[k]=v},showModal(){this.open=true},close(){this.open=false}};
 const state={},c=vm.createContext({state,window:{scrollBy(){},scrollTo(){},scrollX:0,scrollY:0},document:{getElementById:id=>id==='dialog'?d:null,querySelector:()=>null},render(){},esc:s=>String(s??'').replaceAll('<','&lt;'),icon:()=>'',num:(n,p=2)=>Number.isFinite(n)?n.toFixed(p):'—',change:n=>Number.isFinite(n)?n+'%':'—',cls:()=>'',pill:t=>t,button:(t,action)=>`<button onclick="${action}">${t}</button>`,panel:(t,b,a='',klass='')=>`<section class="${klass}">${t}${a}${b}</section>`,trend:m=>!m?.ma60?{text:'样本不足',type:'neutral'}:m.price>m.ma20&&m.price>m.ma60?{text:'趋势偏强',type:'good'}:m.price<m.ma20&&m.price<m.ma60?{text:'趋势偏弱',type:'warn'}:{text:'趋势分歧',type:'neutral'}});
 vm.runInContext(source('data.js'),c);vm.runInContext(source('engine.js'),c);vm.runInContext('E=RiskEngine',c);c.RiskModel=require('../dist/risk-engine.js');
 vm.runInContext(source('risk-views.js'),c);vm.runInContext(source('market-cards.js'),c);
 return {c,state,d,run:code=>vm.runInContext(code,c)};
}
test('market and risk layouts are independent and invalid view requests do not mutate state',()=>{
 const h=harness();h.run("setRiskLayout('domestic','table');setMarketLayout('table')");assert.equal(h.state.marketLayout,'table');h.run("setMarketLayout('cards')");assert.equal(h.state.riskLayouts.domestic,'table');const before=JSON.stringify(h.state);h.run("setMarketLayout('bogus')");assert.equal(JSON.stringify(h.state),before);
 h.run("toggleRiskSort('marketBench','ret60');toggleRiskSort('researchPool','cash');toggleRiskSort('researchPool','cash')");assert.equal(h.state.riskSorts.marketBench.direction,'asc');assert.equal(h.state.riskSorts.researchPool.direction,'desc');
});
test('trend sort keeps insufficient samples missing and ranks weak, mixed, strong',()=>{
 const {c}=harness(),rank=vm.runInContext('marketTrendRank',c);assert.equal(rank(null),null);assert.equal(rank({price:3,ma20:2,ma60:null}),null);assert.equal(rank({price:1,ma20:2,ma60:3}),0);assert.equal(rank({price:2,ma20:1,ma60:3}),1);assert.equal(rank({price:4,ma20:2,ma60:3}),2);
});
test('research pool sorting uses raw cash amounts, retains zero, and places missing reports last',()=>{
 const h=harness(),start=source('evidence-views.js').indexOf('function researchPoolTable()'),end=source('evidence-views.js').indexOf('\nfunction marketFlowV4',start);
 vm.runInContext(source('evidence-views.js').slice(start,end),h.c);
 h.run("WATCH_CODES.splice(0,WATCH_CODES.length,'large','negative','zero','missing')");h.c.ctx=code=>({a:{code,name:code,currency:'CNY'},m:{ret20:0},f:code==='missing'?{}:{operatingCash:({large:1e10,negative:-2e8,zero:0})[code],cashRatio:0,period:'2026-09-30'},etf:{}});h.c.billions=n=>Number.isFinite(n)?String(n/1e8)+'亿':'—';
 h.run("toggleRiskSort('researchPool','cash')");const html=h.run('researchPoolTable()');assert.ok(html.indexOf('>negative</button>')<html.indexOf('>zero</button>'));assert.ok(html.indexOf('>zero</button>')<html.indexOf('>large</button>'));assert.ok(html.indexOf('>large</button>')<html.indexOf('>missing</button>'));assert.match(html,/0.00×/);assert.match(html,/经营现金按各自报告币种/);
 h.run("toggleRiskSort('researchPool','cash')");const desc=h.run('researchPoolTable()');assert.ok(desc.indexOf('>large</button>')<desc.indexOf('>negative</button>'));assert.ok(desc.indexOf('>negative</button>')<desc.indexOf('>missing</button>'));
});
test('risk detail dialog opens only the requested current security and preserves missing evidence',()=>{
 const h=harness(),model={score:0,coverage:1,low:0,high:0,parts:[{name:'估值',weight:10,score:null,reason:'缺少可比披露'}]};
 h.c.currentWatch=[{code:'x',asset:{name:'证券X',industry:'科技'},local:model,parentRisk:model,parentLabel:'A股',...model}];h.c.ctx=()=>({});h.c.provenance=()=>'<p>行情来源与日期</p>';h.run('riskSnapshot=()=>({watch:currentWatch})');h.run("openWatchRiskDetails('unknown')");assert.equal(h.d.open,false);h.run("openWatchRiskDetails('x')");assert.equal(h.d.open,true);assert.equal(h.d.dataset.kind,'risk-details');assert.match(h.d.innerHTML,/关闭七维评分弹窗/);assert.match(h.d.innerHTML,/缺少可比披露/);assert.match(h.d.innerHTML,/document.getElementById\('dialog'\).close\(\)/);assert.match(h.d.innerHTML,/行情来源与日期/);
 h.d.close();h.c.currentWatch[0].asset.name='更新后名称';h.run("openWatchRiskDetails('x')");assert.match(h.d.attributes['aria-label'],/更新后名称/);
});
test('external risk dialog keeps the selected scoring evidence, source and missing coverage',()=>{
 const h=harness(),model={score:null,coverage:0,low:0,high:100,parts:[{name:'趋势',weight:100,score:null,reason:'历史不足'}]};
 h.c.externalRows=[{symbol:'SOX',ticker:'^SOX',name:'费城半导体指数',weight:12,model,history_as_of:'2026-11-04',url:'https://example.com/quote',history_url:'https://example.com/history'}];h.c.sourceLink=(url,label)=>`<a href="${url}">${label}</a>`;h.run("riskSnapshot=()=>({ext:{assets:externalRows}});externalRead=x=>'<p>所选资产产业解读</p>'");h.run("openExternalRiskDetails('missing')");assert.equal(h.d.open,false);h.run("openExternalRiskDetails('SOX')");assert.equal(h.d.open,true);assert.match(h.d.attributes['aria-label'],/费城半导体指数/);assert.match(h.d.innerHTML,/历史不足/);assert.match(h.d.innerHTML,/Nasdaq指数说明/);assert.match(h.d.innerHTML,/https:\/\/example.com\/history/);assert.match(h.d.innerHTML,/关闭解读与评分依据弹窗/);
});
test('watch risk table uses security links and removes the redundant research button',()=>{
 const h=harness(),model={score:50,coverage:1,low:50,high:50};h.c.researchHref=code=>'#research/'+encodeURIComponent(code);h.c.rows=[{code:'HK.00700',asset:{name:'腾讯控股',industry:'港股'},parentLabel:'港股',parentRisk:model,local:model,price:{date:'2026-11-04',ret20:0},...model}];h.state.riskLayouts={watch:'table'};const html=h.run('riskWatchDisplay(rows)');assert.match(html,/href="#research\/HK.00700"/);assert.match(html,/class="risk-watch-asset"/);assert.match(html,/七维评分、缺失项与依据/);assert.doesNotMatch(html,/>七维研究<|risk-study-link/);
});
test('domestic table links each ETF to its backtest and exposes only the scoring dialog action',()=>{
 const h=harness(),model={score:50,coverage:1,low:50,high:50,parts:[]};h.run('byCode=code=>CATALOG.find(a=>a.code===code)');h.c.series=()=>[];h.c.backtestHref=code=>'#backtest/'+code;h.c.riskRows=Array.from(h.run('BENCH_CODES'),code=>({code,model}));h.state.riskLayouts={domestic:'table'};const html=h.run('riskDomesticDisplay(riskRows)');for(const x of h.c.riskRows)assert.ok(html.includes('href="#backtest/'+x.code+'"'));assert.doesNotMatch(html,/查看K线|<details/);assert.match(html,/risk-benchmark-asset/);assert.match(html,/aria-haspopup="dialog"/);
});
test('ETF score dialog reads the current selected benchmark model and keeps missing evidence visible',()=>{
 const h=harness(),model={score:null,coverage:0,low:0,high:100,parts:[{name:'趋势',weight:30,score:null,reason:'历史不足60条'}]};h.run('byCode=code=>CATALOG.find(a=>a.code===code)');h.c.series=()=>[{date:'2026-11-04',completed:false}];h.c.sourceLine=code=>'所选ETF行情来源 '+code;h.c.domesticRows=[{code:'510300',model}];h.run('riskSnapshot=()=>({dom:{rows:domesticRows}})');h.run("openDomesticRiskDetails('002281')");assert.equal(h.d.open,false);h.run("openDomesticRiskDetails('510300')");assert.equal(h.d.open,true);assert.match(h.d.attributes['aria-label'],/沪深300ETF/);assert.match(h.d.innerHTML,/历史不足60条/);assert.match(h.d.innerHTML,/盘中未确认/);assert.match(h.d.innerHTML,/所选ETF行情来源 510300/);assert.match(h.d.innerHTML,/关闭ETF评分依据弹窗/);assert.doesNotMatch(h.d.innerHTML,/查看K线/);
});
