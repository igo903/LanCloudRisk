const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const W=require('../dist/wyckoff-engine.js');
const date=i=>new Date(Date.UTC(2026,0,1+i)).toISOString().slice(0,10);
const bar=(i,values={})=>({date:date(i),open:100,high:102,low:98,close:100,vol:100,completed:true,...values});
const base=()=>Array.from({length:80},(_,i)=>bar(i));
const spring=()=>bar(80,{open:99,high:101,low:97,close:100.5,vol:70});
const sos=()=>bar(80,{open:101,high:106,low:101,close:105.5,vol:200});
test('short, empty and invalid histories do not manufacture a conclusion',()=>{
 assert.equal(W.analyze([]).ready,false);assert.equal(W.analyze(base().slice(0,20)).ready,false);
 assert.equal(W.analyze(base().slice(0,21)).ready,true);assert.equal(W.analyze(base().slice(0,21)).phase,'样本不足');
 for(const invalid of [bar(80,{high:99}),bar(0),bar(80,{close:NaN})])assert.equal(W.analyze([...base(),invalid]).ready,false);
 const b=base();b[10].vol=null;assert.equal(W.analyze(b).ready,true);
});
test('short-history support absorption needs repeated support, a lower wick and usable volume',()=>{
 const prefix=base().slice(0,20),values={open:100,high:102,low:98.1,close:101,vol:114};
 const signal=W.analyze([...prefix,bar(20,values)]).events.at(-1);
 assert.equal(signal.type,'Support');assert.equal(signal.window,20);assert.ok(signal.touches>=2);
 for(const vol of [0,90])assert.equal(W.analyze([...prefix,bar(20,{...values,vol})]).events.length,0);
});
test('a repeatedly tested local range can identify a Spring inside the wider 40-day range',()=>{
 const input=base();input[45]=bar(45,{low:90,high:112});
 const event=W.analyze([...input,spring()]).events.find(e=>e.date===date(80));
 assert.equal(event.type,'Spring');assert.equal(event.window,20);assert.equal(event.level,98);
 input[65]=bar(65,{low:94});
 assert.ok(!W.analyze([...input,bar(80,{open:95,high:101,low:93,close:99,vol:90})]).events.some(e=>e.date===date(80)));
});
test('moderately higher volume and wider spread can qualify for SOS with directional price confirmation',()=>{
 const values={open:101,high:105.5,low:101,close:105,vol:128};
 assert.equal(W.analyze([...base(),bar(80,values)]).events.at(-1).type,'SOS');
 assert.equal(W.analyze([...base(),bar(80,{...values,vol:100})]).events.length,0);
});
test('rolling levels and volume baselines exclude the event bar itself',()=>{
 const input=[...base(),sos()],before=JSON.stringify(input),m=W.analyze(input);
 assert.equal(m.latest.support,98);assert.equal(m.latest.resistance,102);assert.equal(m.latest.volumeRatio,2);assert.equal(m.latest.spreadRatio,1.25);assert.equal(m.events.at(-1).type,'SOS');assert.equal(m.events.at(-1).status,'pending');assert.equal(JSON.stringify(input),before);
});
test('Spring and UT proxies require a reclaim or rejection rather than a plain breakout',()=>{
 assert.equal(W.analyze([...base(),spring()]).events.at(-1).type,'Spring');
 assert.equal(W.analyze([...base(),bar(80,{open:101,high:103,low:99,close:99.5,vol:70})]).events.at(-1).type,'UT');
 assert.equal(W.analyze([...base(),bar(80,{open:99,high:99,low:96,close:96.5,vol:100})]).events.length,0);
});
test('wide high-volume downside breaks identify SOW and missing volume blocks events',()=>{
 const sow=bar(80,{open:99,high:99,low:94,close:94.5,vol:200});assert.equal(W.analyze([...base(),sow]).events.at(-1).type,'SOW');
 for(const value of [null,0,NaN]){const bars=base();bars.at(-1).vol=value;const m=W.analyze([...bars,sow]);assert.equal(m.latest.volumeRatio,null);assert.equal(m.events.length,0);assert.match(m.warnings.join(''),/成交量/)}
});
test('Test and LPS require their prior Spring or SOS event chain',()=>{
 const testBar=bar(81,{open:100,high:100,low:98,close:99.8,vol:40});
 assert.equal(W.analyze([...base(),spring(),testBar]).events.at(-1).type,'Test');
 assert.ok(!W.analyze([...base(),bar(80),testBar]).events.some(e=>e.type==='Test'));
 const lps=bar(81,{open:103,high:103.5,low:101,close:102.5,vol:50});assert.equal(W.analyze([...base(),sos(),lps]).events.at(-1).type,'LPS');
});
test('LPS cannot validate before its parent SOS, even when its own trigger was crossed',()=>{
 const lps=bar(81,{open:103,high:103.5,low:101,close:102.5,vol:50});
 const b=bar(82,{open:103,high:104.5,low:102.5,close:104,vol:90});
 const input=[...base(),sos(),lps,b,continuation(83)];
 const at82=W.analyze(input,{end:date(82)}).events.find(e=>e.type==='LPS');
 const at83=W.analyze(input).events.find(e=>e.type==='LPS');
 assert.ok(b.close>at82.trigger);assert.equal(at82.status,'pending');
 assert.equal(at83.status,'validated');assert.equal(at83.confirmAt,date(83));
});
test('confirmation carries its own knowledge date and is unavailable before that date',()=>{
 const input=[...base(),spring(),bar(81,{open:101,high:103,low:100,close:102.5})];
 const atEvent=W.analyze(input,{end:date(80)}).events.at(-1),after=W.analyze(input).events.find(e=>e.type==='Spring');
 assert.equal(atEvent.status,'pending');assert.equal(atEvent.knownAt,date(80));assert.equal(atEvent.confirmAt,null);
 assert.equal(after.status,'validated');assert.equal(after.date,date(80));assert.equal(after.confirmAt,date(81));assert.equal(after.knownAt,date(81));
 const prefix=W.analyze(input.slice(0,-1));assert.deepEqual(prefix.events,W.analyze(input,{end:date(80)}).events);
});
test('failure and the three-bar timeout are explicit and later data cannot extend that window',()=>{
 let m=W.analyze([...base(),spring(),bar(81,{open:98,high:99,low:95,close:96})]);assert.equal(m.events.find(e=>e.type==='Spring').status,'failed');
 const input=[...base(),spring(),bar(81),bar(82),bar(83),bar(84,{open:102,high:106,low:101,close:105})];
 m=W.analyze(input);const event=m.events.find(e=>e.type==='Spring');assert.equal(event.status,'unconfirmed');assert.equal(event.knownAt,date(83));assert.equal(event.confirmAt,null);
});
test('extreme intraday values cannot change closed structure, event validation or volume statistics',()=>{
 const input=[...base(),spring()],closed=W.analyze(input),live=W.analyze([...input,bar(81,{open:100,high:999,low:1,close:900,vol:1e12,completed:false})]);
 assert.deepEqual(live.events,closed.events);assert.deepEqual(live.points,closed.points);assert.equal(live.asOf,closed.asOf);assert.equal(live.phase,closed.phase);assert.equal(live.upDownRatio,closed.upDownRatio);assert.equal(live.live.length,1);assert.match(live.warnings.join(''),/盘中/);
});
test('as-of analysis excludes future rows and threshold changes have deterministic results',()=>{
 const input=[...base(),spring()];const a=W.analyze(input,{range:20}),b=W.analyze(input,{range:60});assert.equal(a.range,20);assert.equal(b.range,60);assert.equal(a.latest.support,98);assert.equal(W.analyze(input,{range:1}).range,40);
 input.push(bar(81,{open:101,high:500,low:1,close:300}));assert.equal(W.analyze(input,{end:date(80)}).latest.date,date(80));
});
test('price trend states do not claim accumulation, distribution or fabricated probabilities',()=>{
 const up=Array.from({length:100},(_,i)=>bar(i,{open:100+i,high:102+i,low:99+i,close:101+i}));const m=W.analyze(up);assert.equal(m.ready,true);assert.match(m.phase,/上涨/);assert.equal(m.upDownRatio,null);assert.ok(!('confidence' in m));assert.equal(W.analyze(base()).phase,'区间震荡 · 方向待辨');assert.equal(W.analyze(base().slice(0,64)).slope,null);assert.equal(W.analyze(base().slice(0,65)).slope,0);
});
test('old Wyckoff links resolve to the same security in backtest without enabling the factor',()=>{
 const location={hash:'#wyckoff/688981'},c=vm.createContext({location,localStorage:{getItem:()=>null},history:{replaceState(_s,_t,url){location.hash=url}},document:{body:{classList:{remove(){}}}},window:{scrollTo(){}},render(){}});
 for(const name of ['data.js','engine.js','views.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',name),'utf8'),c);
 const run=s=>vm.runInContext(s,c);run('applyHashRoute()');assert.equal(run('state.code'),'688981');assert.equal(run('state.page'),'backtest');assert.equal(location.hash,'#backtest/688981');assert.equal(run('bt.wyckoff'),false);
 assert.ok(!run('NAV.some(n=>n[0]==="wyckoff")'));
});
const continuation=(i,values={})=>bar(i,{open:105,high:108,low:103,close:106.5,vol:120,...values});
const weak=()=>bar(80,{open:99,high:99,low:94,close:94.5,vol:200});
const weakNext=i=>bar(i,{open:94,high:95,low:92,close:93,vol:120});
const baseSignal=(i,type='buy',extra={})=>({i,date:date(i),type,grade:type==='buy'?'B':'normal',reason:'原信号',filtered:false,price:100,...extra});
test('new buy exists on confirmation date only, with no future information in a truncated run',()=>{
 const input=[...base(),sos(),continuation(81),continuation(82)],full=W.integrate(input,[]),before=W.integrate(input.slice(0,81),[]);
 assert.equal(before.signals.length,0);assert.equal(full.signals.length,1);
 assert.equal(full.signals[0].date,date(81));assert.equal(full.signals[0].wyckoff.event.date,date(80));assert.equal(full.signals[0].grade,'B');assert.equal(full.rows[0].action,'added');
});
test('bearish confirmation replaces a conflicting buy and blocks new opposite buys for only two further closes',()=>{
 const input=[...base(),weak(),...Array.from({length:5},(_,j)=>weakNext(81+j))],baseline=[baseSignal(81),baseSignal(82),baseSignal(83),baseSignal(84)],saved=JSON.stringify(baseline),r=W.integrate(input,baseline);
 assert.equal(r.rows[0].action,'replaced');assert.equal(r.signals[0].type,'sell');assert.equal(r.signals[0].grade,'strong');
 assert.equal(r.rows[1].action,'filtered');assert.equal(r.signals[1].filtered,true);assert.equal(r.signals[2].filtered,true);assert.equal(r.signals[3].filtered,false);assert.equal(JSON.stringify(baseline),saved);
});
test('support preserves original grade, original exits and the three-factor veto',()=>{
 const input=[...base(),sos(),continuation(81),continuation(82)];
 const same=W.integrate(input,[baseSignal(81,'buy',{grade:'C'})]);assert.equal(same.rows[0].action,'supported');assert.equal(same.signals[0].grade,'C');assert.equal(same.signals[0].wyckoff.event.type,'SOS');
 const sell=W.integrate(input,[baseSignal(81,'sell')]);assert.equal(sell.signals[0].type,'sell');assert.equal(sell.rows[0].action,'unchanged');assert.equal(sell.signals[0].wyckoff,undefined);
 const veto=W.integrate(input,[baseSignal(81,'buy',{filtered:true})]);assert.equal(veto.signals[0].filtered,true);assert.equal(veto.signals[0].wyckoff,undefined);
});
test('provisional confirmations and missing daily volume cannot generate trades',()=>{
 const live=W.integrate([...base(),sos(),continuation(81,{completed:false})],[]);assert.equal(live.rows.length,0);assert.equal(live.signals.length,0);
 const missing=W.integrate([...base(),sos(),continuation(81,{vol:0})],[]);assert.equal(missing.signals.length,0);assert.equal(missing.rows[0].action,'unchanged');
});
test('integrated execution uses next session, and chart-ready signals follow the same plan and market gate',()=>{
 const c=vm.createContext({WyckoffEngine:W});
 for(const name of ['data.js','engine.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',name),'utf8'),c);
 const E=vm.runInContext('RiskEngine',c),bars=[...base(),sos(),continuation(81),continuation(82)];
 const data={instruments:{'002281':{bars,completed:true}}},p={capital:1000000,position:50,fee:8,slip:5,lot:100,execution:'open',start:date(81),end:date(82)};
 const original=E.backtest(data,'002281',p);assert.equal(original.orders.length,0);
 const on=E.backtest(data,'002281',{...p,wyckoff:true});assert.equal(on.signals[0].date,date(81));assert.equal(on.orders[0].signal,date(81));assert.equal(on.orders[0].date,date(82));assert.equal(on.orders[0].status,'模拟成交');
 assert.equal(E.backtest(data,'002281',{...p,wyckoff:true,marketGate:true}).orders[0].status,'未成交/跳过');
 const end=E.backtest(data,'002281',{...p,wyckoff:true,end:date(81)});assert.equal(end.signals.length,1);assert.equal(end.orders.length,0);
 bars.push(continuation(83,{close:500,high:510,completed:false}));
 const live=E.backtest(data,'002281',{...p,wyckoff:true,end:date(83)});
 assert.deepEqual(live.openPosition,on.openPosition);assert.equal(live.openPosition.date,date(82));
});
test('research signals explain both factor adjustments and unconfirmed shapes when the factor is off',()=>{
 const input=[...base(),sos(),continuation(81)],model=W.integrate(input,[]);
 const c=vm.createContext({WyckoffEngine:W,E:{series:()=>input},data:()=>({}),asset:()=>({code:'002281'}),
  bt:{wyckoff:false,start:date(80),end:date(80)},esc:x=>String(x??''),num:(x,n=2)=>x==null?'—':x.toFixed(n),face:()=>'',pill:s=>s});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist','wyckoff-views.js'),'utf8'),c);
 const research=vm.runInContext('wyckoffResearchTable({wyckoff:null})',c);
 assert.match(research,/因子已关闭/);assert.match(research,/SOS · 强势信号/);assert.match(research,/待验证/);
 assert.match(research,/阻力 102\.000/);assert.match(research,/量比 2\.00×/);assert.match(research,/收盘高于 105\.500/);
 c.result={signals:model.signals,wyckoff:model};
 const active=vm.runInContext('backtestSignalTable(result)',c);
 assert.match(active,/威科夫新增/);assert.match(active,/收盘 106\.500 高于验证价 105\.500/);
});
test('blue triangles stack below supported faces, excluding provisional and filtered signals',()=>{
 const c=vm.createContext({});vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist','interactive-chart.js'),'utf8'),c);
 const markers=vm.runInContext('backtestMarkers',c)([baseSignal(81,'sell',{wyckoff:{}}),baseSignal(82,'buy',{wyckoff:{},filtered:true}),baseSignal(83,'buy',{wyckoff:{},provisional:true})],'gray','orange','green','red');
 assert.equal(markers.length,4);assert.equal(markers[0].text,'☹');assert.equal(markers[0].position,'belowBar');assert.equal(markers[1].text,'▲');assert.equal(markers[1].position,'belowBar');assert.equal(markers[1].time,date(81));assert.equal(markers[1].color,'#397eff');
});
