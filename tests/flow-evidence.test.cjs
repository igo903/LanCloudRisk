const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=n=>fs.readFileSync(path.join(__dirname,'../dist',n),'utf8');
function setup(){
 const state={},c=vm.createContext({state,RiskModel:require('../dist/risk-engine.js'),E:{valid:Number.isFinite},window:{scrollX:0,scrollY:0,scrollTo(){}},document:{querySelector:()=>null},render(){},esc:String,num:(n,p=2)=>Number.isFinite(n)?n.toFixed(p):'—',cls:n=>n<0?'negative':'positive',billions:n=>Number.isFinite(n)?String(n):'—'});
 vm.runInContext(source('risk-views.js'),c);
 const evidence=source('evidence-views.js');vm.runInContext(evidence.slice(evidence.indexOf('function flowHistory'),evidence.indexOf('function ctx')),c);vm.runInContext(evidence.slice(evidence.indexOf('function flowTable(c)'),evidence.indexOf('\nfunction coverage')),c);
 return {state,c,run:s=>vm.runInContext(s,c),rows:rows=>{c.input=rows;return vm.runInContext('flowTableRows({flows:input})',c)}};
}
const history=()=>Array.from({length:30},(_,i)=>({date:'2026-10-'+String(i+1).padStart(2,'0'),netFlow:i+1,totalNetFlow:-(i+1)}));
test('daily windows include the row date and earlier history before latest-ten display slicing',()=>{
 const h=setup(),input=history().reverse(),before=JSON.stringify(input),rows=h.rows(input);
 assert.equal(rows.length,10);assert.equal(rows[0].date,'2026-10-30');assert.equal(rows[0].flow3,87);assert.equal(rows[0].flow5,140);assert.equal(rows[0].flow10,255);assert.equal(rows[0].flow20,410);
 assert.equal(rows[9].date,'2026-10-21');assert.equal(rows[9].flow3,60);assert.equal(rows[9].flow20,230);assert.equal(JSON.stringify(input),before);
});
test('insufficient and invalid observations stay missing while zero and outflows are summed',()=>{
 const h=setup(),rows=history().slice(0,6);rows[0].netFlow=-5;rows[1].netFlow=0;rows[2].netFlow=5;rows[3].netFlow=null;
 const result=h.rows(rows);assert.equal(result[3].flow3,0);assert.equal(result[4].flow3,null);assert.equal(result[0].flow3,null);assert.equal(result[0].flow5,null);assert.equal(result[0].flow10,null);
 rows[3].netFlow=NaN;assert.equal(h.rows(rows)[0].flow3,null);
});
test('dates are deduplicated before counting windows and future observations cannot change an earlier result',()=>{
 const h=setup(),rows=history().slice(0,5),duplicate={...rows[1],netFlow:100};
 let result=h.rows([...rows,duplicate,{date:'bad',netFlow:999}]);assert.equal(result.length,5);assert.equal(result[2].flow3,104);
 const prior=result[0].flow5;result=h.rows([...rows,duplicate,{date:'2026-10-06',netFlow:1e9}]);assert.equal(result.find(r=>r.date==='2026-10-05').flow5,prior);
});
test('all eight evidence headers sort by raw values with missing last and preserve calculated windows',()=>{
 const h=setup();h.rows(history());h.run("sample={a:{currency:'CNY'},flows:input}");
 const initial=h.run('flowTable(sample)');for(const key of ['date','flow1','netFlow','totalNetFlow','flow3','flow5','flow10','flow20'])assert.ok(initial.includes('data-risk-sort="flows-'+key+'"'));
 assert.ok(initial.indexOf('2026-10-30')<initial.indexOf('2026-10-21'));
 h.run("toggleRiskSort('flows','flow20')");let html=h.run('flowTable(sample)');assert.ok(html.indexOf('2026-10-21')<html.indexOf('2026-10-30'));assert.match(html,/aria-sort="ascending"/);
 h.run("toggleRiskSort('flows','flow20')");html=h.run('flowTable(sample)');assert.ok(html.indexOf('2026-10-30')<html.indexOf('2026-10-21'));assert.match(html,/aria-sort="descending"/);assert.match(html,/截至各行日期并包含当日/);
 h.run("sample.flows=[{date:'2026-10-01',netFlow:null},{date:'2026-10-02',netFlow:0},{date:'2026-10-03',netFlow:-5}];toggleRiskSort('flows','netFlow')");html=h.run('flowTable(sample)');assert.ok(html.indexOf('2026-10-03')<html.indexOf('2026-10-02'));assert.ok(html.indexOf('2026-10-02')<html.indexOf('2026-10-01'));
});

test('watch funds match each security price date, ignore future records and preserve zero plus native currency',()=>{
 const h=setup(),rows=history();h.c.obs=()=>({flows:[...rows,{date:'2026-10-31',netFlow:1e9}]});h.c.series=()=>[{date:'2026-10-30'}];h.run("asset={code:'test',currency:'USD'}");
 const snapshot=h.run('watchFlowSnapshot(asset)');assert.equal(snapshot.date,'2026-10-30');assert.equal(snapshot.values.flow1,30);assert.equal(snapshot.values.flow3,87);assert.equal(snapshot.values.flow5,140);assert.equal(snapshot.values.flow10,255);assert.equal(snapshot.values.flow20,410);
 h.c.billions=(value,currency)=>Number.isFinite(value)?value+' '+currency:'—';const html=h.run('watchFlowCells(asset)');assert.match(html,/30 USD/);assert.match(html,/资金截至 2026-10-30/);assert.equal((html.match(/<td /g)||[]).length,6);
 rows.at(-1).netFlow=0;assert.equal(h.run('watchFlowSnapshot(asset).values.flow1'),0);
});
test('watch funds never label an older flow as the current day or bridge missing amounts',()=>{
 const h=setup(),rows=history();h.c.obs=()=>({flows:rows});h.c.series=()=>[{date:'2026-10-31'}];h.run("asset={code:'test'}");
 const snapshot=h.run('watchFlowSnapshot(asset)');assert.deepEqual({...snapshot.values},{flow1:null,flow3:87,flow5:140,flow10:255,flow20:410,flow60:null});assert.equal(snapshot.sourceDate,'2026-10-30');const html=h.run('watchFlowCells(asset)');assert.match(html,/行情日资金未返回/);assert.match(html,/该日净额 30/);assert.match(html,/资金截至 2026-10-30/);
 h.c.series=()=>[{date:'2026-10-30'}];rows.at(-2).netFlow=null;const missing=h.run('watchFlowSnapshot(asset).values');assert.equal(missing.flow1,30);assert.equal(missing.flow3,null);assert.equal(missing.flow20,null);
 rows.at(-1).netFlow=null;const trailing=h.run('watchFlowSnapshot(asset)');assert.equal(trailing.sourceDate,'2026-10-28');assert.equal(trailing.values.flow3,81);assert.equal(trailing.values.flow1,null);
 h.c.obs=()=>({});for(const value of Object.values(h.run('watchFlowSnapshot(asset).values')))assert.equal(value,null);
});
test('the new daily column uses each row daily amount and supports ascending and descending sorting',()=>{
 const h=setup();h.rows(history());h.run("sample={a:{currency:'CNY'},flows:input};toggleRiskSort('flows','flow1')");let html=h.run('flowTable(sample)');assert.ok(html.indexOf('2026-10-21')<html.indexOf('2026-10-30'));assert.match(html,/当日净流入/);
 h.run("toggleRiskSort('flows','flow1')");html=h.run('flowTable(sample)');assert.ok(html.indexOf('2026-10-30')<html.indexOf('2026-10-21'));
});
test('ETF uses its own dated subscriptions series, never stock flow, and exposes all six periods',()=>{
 const h=setup(),rows=Array.from({length:60},(_,i)=>({date:new Date(Date.UTC(2026,7,1+i)).toISOString().slice(0,10),netFlow:i===0?-10:10}));
 h.c.obs=()=>({flows:[{date:'2026-10-01',netFlow:999}],etf_flow:{rows,source:'交易所份额×前日净值',currency:'CNY'}});
 h.c.series=()=>[{date:'2026-10-01'}];
 h.run("asset={code:'515980',name:'人工智能ETF',type:'etf'}");
 const snap=h.run('watchFlowSnapshot(asset)');
 assert.deepEqual({...snap.values},{flow1:null,flow3:30,flow5:50,flow10:100,flow20:200,flow60:580});
 const html=h.run('watchFlowCells(asset)');assert.match(html,/估算 · 逐日明细/);assert.match(html,/该日净额 10/);
 h.c.byCode=()=>h.run('asset');h.c.$=()=>({dataset:{}});h.c.modal=(title,body)=>{assert.match(title,/ETF逐日/);assert.equal((body.match(/<tbody><tr>|<\/tr><tr>/g)||[]).length,60);assert.match(body,/前一交易日单位净值/);};
 h.run("openEtfFlow('515980')");
});
