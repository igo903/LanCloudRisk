const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const R=require('../dist/risk-engine.js');
function setup(){
 const esc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
 const c=vm.createContext({RiskModel:R,E:{valid:Number.isFinite},state:{},window:{},document:{},esc,
  num:(x,n=2)=>Number.isFinite(x)?x.toFixed(n):'—',change:x=>Number.isFinite(x)?x.toFixed(2)+'%':'—',
  pill:(label,tone)=>`<span class="${tone}">${esc(label)}</span>`,panel:(title,body)=>`<section><h2>${title}</h2>${body}</section>`,
  sourceLink:(url,label)=>`<a href="${esc(url)}">${esc(label)}</a>`});
 vm.runInContext(fs.readFileSync('dist/risk-views.js','utf8'),c);return c;
}
function fixture(){
 const date=R.day(),components=[{id:'news',name:'富途新闻',score:80,coverage:.5,as_of:date,reason:'标题线索'},
  {id:'community',name:'富途社区情绪',score:60,coverage:1,as_of:date,reason:'有效讨论'},
  {id:'gap',name:'市场情绪偏离筛查',score:40,coverage:1,as_of:date,reason:'收盘与经营'}];
 const row={kind:'politics',components,core_points:['核对出口许可的实际适用范围'],transmission:'许可 → 订单 → 经营现金流',
  news:[{title:'新闻线索<script>bad()</script>',url:'https://news.futunn.com/post/fixture',published_at:date,source:'富途新闻'}],
  community:{post_count:10,counts:{bullish:2,bearish:6,neutral:2},symbols:[{symbol:'NVDA',status:'ok',post_count:10}],
   top_opinions:[{opinion:'社区担忧出口许可，但需要核对订单兑现',published_at:date}]},
  gap:{sample_count:3,scope_count:4,items:[{code:'002281',name:'光迅科技',ret120:-30,relative20:null,classification:'价格与经营分化，问题性质待核实',date}]},
  counterpoints:['不能把下跌全部解释为政策影响']};
 return {method:'public-evidence-v2',fetched_at:date,items:[row,{...row,kind:'war'}]};
}
test('B1 and B2 show sourced views, community disagreement, fundamental checks and the three scoring inputs',()=>{
 const c=setup();c.geo=fixture();const html=vm.runInContext('geopoliticalPanel(geo)',c);
 for(const text of ['B1 · 政治','B2 · 战争','查看原文','社区观点','情绪偏离','反证','40%','25%','35%']){
  assert.ok(html.includes(text),text);
 }
 assert.ok(html.includes('https://news.futunn.com/post/fixture'));assert.ok(html.includes('核对出口许可'));
 assert.ok(html.includes('&lt;script&gt;'));assert.ok(!html.includes('<script>'));
 assert.ok(!html.includes('2026-10-15'));assert.ok(!html.includes('GPR子项'));
});
test('insufficient or expired evidence hides the B score and explains coverage rather than showing zero risk',()=>{
 const c=setup(),geo=fixture();geo.items[0].components[1].as_of='2000-01-01';geo.items[0].components[2].score=null;c.geo=geo;
 const html=vm.runInContext('geopoliticalPanel(geo)',c);
 assert.match(html,/证据覆盖不足/);assert.match(html,/<b>—<\/b>/);assert.match(html,/来源缺失、样本不足或已过期/);
});
test('legacy snapshots request new evidence and table presentation uses the same recalculated model',()=>{
 const c=setup();c.geo={as_of:R.day(),items:[{kind:'politics',score:99}]};
 assert.match(vm.runInContext('geopoliticalPanel(geo)',c),/请刷新行情/);
 c.geo=fixture();c.state.riskLayouts={external:'table'};
 const table=vm.runInContext('riskGeopoliticalDisplay(geo)',c);
 assert.match(table,/有效覆盖/);assert.match(table,/核心观点/);assert.match(table,/80%/);assert.doesNotMatch(table,/30日均值/);
});
