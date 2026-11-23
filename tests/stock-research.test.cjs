const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(){
 const context=vm.createContext({console,URL,Intl,localStorage:{getItem(){return null}},window:{},document:{querySelector(){return null}}});
 for(const file of ['data.js','engine.js','app.js','views.js','local-connector.js','research-views.js','strategy-views.js','evidence-views.js','stock-research.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',file),'utf8'),context,{filename:file});
 const run=s=>vm.runInContext(s,context);
 run("mode='uploaded';uploaded={instruments:{},external:[]};state.code='002281';");
 return {run,context};
}
function inject(h){h.run(String.raw`uploaded.instruments['002281']={fundamentals:{period:'2026-09-30',pe:90,ps:10,revenueGrowth:10,operatingCash:-20,freeCashFlow:-30,roe:8},research_evidence:{version:1,symbol:'SZ.002281',checked_at:'2026-11-23T10:00:00+08:00',providers:{
 futu_news:{status:'ok',data:{items:[{title:'光迅科技产品交付增长',text:'公司披露订单交付',published_at:'2026-11-20T12:00:00+08:00',url:'https://news.futunn.com/post/test'},{title:'光迅科技股东拟减持',published_at:'2026-11-19T12:00:00+08:00',url:'https://news.futunn.com/post/test2'}]}},
 ifind:{status:'partial',data:{facts:[{key:'products',label:'主要产品',kind:'text',value:'高速光模块',period:'2026-09-30',source:'同花顺 iFinD'}],errors:{资本效率:'缺少单位'}}},
 futu_capital:{status:'retained',error:'权限不足',data_checked_at:'2026-11-20T10:00:00+08:00',data:{content:'资金分布：无异动\n资金流向：净流出\n卖空：无异动',time_range:'2026.11.14 - 2026.11.20'}},
 pingan:{status:'ok',data:{metrics:{main_net_inflow:0,large_net_inflow:10,super_large_net_inflow:-10},time_missing:true,as_of:null}}
 }}};`)}
test('all seven company dimensions render useful evidence and retain empty states',()=>{
 const h=setup();for(let i=0;i<7;i++){h.run('state.dimension='+i);const html=h.run('researchV4()');assert.match(html,/公司研究摘要/);assert.match(html,/补充 \/ 刷新公司研究/);assert.match(html,/证据/);assert.doesNotMatch(html,/NaN|undefined|\[object Object\]/)}
 inject(h);for(let i=0;i<7;i++){h.run('state.dimension='+i);const html=h.run('researchV4()');assert.match(html,/公司研究摘要/);assert.doesNotMatch(html,/NaN|undefined|\[object Object\]/)}
});
test('digest keeps plans and contradictory evidence neutral instead of asserting realised growth',()=>{
 const h=setup();inject(h);const digest=h.run("StockResearch.digest(obs('002281'))");assert.equal(digest.direction,'bullish'); // A planned sale remains an unconfirmed event.
 assert.equal(h.run("StockResearch.eventCue({title:'光迅科技否认订单增长传闻'}).direction"),'neutral');
 assert.equal(h.run("StockResearch.eventCue({title:'光迅科技利润下滑'}).direction"),'bearish');
 h.run("uploaded.instruments['002281'].research_evidence.providers.futu_news.data.items.push({title:'光迅科技利润下滑'})");
 assert.equal(h.run("StockResearch.digest(obs('002281')).direction"),'neutral');
});
test('technology checks preserve missing cash information and do not double deduct SBC',()=>{
 const h=setup();const html=h.run('stockResearchHype(ctx())');assert.match(html,/经营现金、利润及资本开支尚未形成/);assert.doesNotMatch(html,/经营现金为正/);assert.match(html,/不重复扣减/);assert.match(html,/不输出/);
 inject(h);const checks=h.run('StockResearch.checks(ctx())');assert.equal(checks.filter(x=>x.warn).length,2);assert.ok(checks[0].next.includes('不用于计算前瞻 PEG'));
});
test('capital evidence preserves three anomaly classes and does not date missing-time flow as today',()=>{
 const h=setup();inject(h);const html=h.run('stockResearchCapital(ctx())');for(const text of ['资金分布：无异动','资金流向：净流出','卖空：无异动','不计入当日或连续净流入','0.00亿元'])assert.ok(html.includes(text),text);
});
test('source trace distinguishes stale retained evidence, provider failure and incomplete fields',()=>{
 const h=setup();inject(h);const html=h.run('stockResearchOverview(ctx())');assert.match(html,/保留旧证据/);assert.match(html,/2026-11-20 10:00:00/);assert.match(html,/缺少单位/);assert.match(html,/权限不足/);assert.match(html,/4 \/ 4 类来源有返回/);
});
test('provider text and links are escaped before rendering',()=>{
 const h=setup();inject(h);h.run(`uploaded.instruments['002281'].research_evidence.providers.ifind.data.facts[0].value='<img src=x onerror=alert(1)>';uploaded.instruments['002281'].research_evidence.providers.futu_news.data.items[0].url='javascript:alert(1)';`);
 const html=h.run("stockResearchDimension(ctx(),0)");assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img src=x|href="javascript:/);
});
test('ETF research keeps constituent economics and excludes company-model refresh',()=>{
 const h=setup();h.run("state.code='588080'");const html=h.run('researchV4()');assert.match(html,/成分持仓、净值与申赎/);assert.doesNotMatch(html,/补充 \/ 刷新公司研究|GAAP/);
});
test('exported snapshots retain independent evidence without altering price bars',()=>{
 const h=setup();const value=h.run(`E.validateSnapshot({instruments:{'002281':{source:'fixture',as_of:'2026-11-20',adjustment:'none',bars:[{date:'2026-11-20',open:10,high:11,low:9,close:10,vol:100}],research_evidence:{version:1,symbol:'SZ.002281',providers:{}}}}})`);
 assert.equal(value.instruments['002281'].research_evidence.version,1);assert.equal(value.instruments['002281'].bars[0].close,10);
});

test('malformed optional research arrays degrade to missing evidence',()=>{
 const h=setup();inject(h);h.run("uploaded.instruments['002281'].research_evidence.providers.ifind.data.facts={};uploaded.instruments['002281'].research_evidence.providers.ifind.data.notices={};uploaded.instruments['002281'].research_evidence.providers.futu_news.data.items={}");
 for(let i=0;i<7;i++){h.run('state.dimension='+i);assert.doesNotThrow(()=>h.run('researchV4()'))}
});
