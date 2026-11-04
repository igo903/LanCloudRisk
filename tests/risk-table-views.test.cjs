const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const R=require('../dist/risk-engine.js'),state={},context=vm.createContext({RiskModel:R,E:{valid:Number.isFinite},state,window:{scrollX:0,scrollY:0,scrollTo(){},scrollBy(){}},document:{getElementById:()=>null,querySelector:()=>null},render(){},num:(x,n=2)=>Number.isFinite(x)?x.toFixed(n):'—',esc:s=>String(s??'').replaceAll('<','&lt;').replaceAll('"','&quot;'),icon:()=>''});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist/risk-views.js'),'utf8'),context);
const sorted=vm.runInContext('sortedRiskRows',context),visible=vm.runInContext('visibleRiskValue',context),ids=rows=>Array.from(rows,x=>x.id);
test('risk blocks default to cards and change layouts independently without changing scores',()=>{
 assert.equal(vm.runInContext("riskLayout('external')",context),'cards');
 vm.runInContext("setRiskLayout('external','table')",context);
 assert.equal(vm.runInContext("riskLayout('external')",context),'table');assert.equal(vm.runInContext("riskLayout('domestic')",context),'cards');assert.equal(vm.runInContext("riskLayout('watch')",context),'cards');
 vm.runInContext("setRiskLayout('watch','table');setRiskLayout('external','cards')",context);assert.equal(vm.runInContext("riskLayout('watch')",context),'table');
});
test('risk sorts use numbers with negative and zero values, and missing values remain last',()=>{
 const rows=[{id:'missing',values:{ret60:null}},{id:'a',values:{ret60:-12}},{id:'b',values:{ret60:0}},{id:'c',values:{ret60:4}},{id:'invalid',values:{ret60:NaN}}];
 assert.deepEqual(ids(sorted(rows,'externalMarket',{key:'ret60',direction:'asc'})),['a','b','c','missing','invalid']);assert.deepEqual(ids(sorted(rows,'externalMarket',{key:'ret60',direction:'desc'})),['c','b','a','missing','invalid']);assert.deepEqual(ids(rows),['missing','a','b','c','invalid']);
});
test('hidden low-coverage risk scores are missing for numeric sorting even when internally estimated',()=>{
 assert.equal(visible({score:99,coverage:.59}),null);assert.equal(visible({score:0,coverage:.6}),0);assert.equal(visible({score:null,coverage:1}),null);
 const rows=[{id:'hidden',values:{risk:visible({score:99,coverage:.2})}},{id:'known',values:{risk:visible({score:30,coverage:1})}}];assert.deepEqual(ids(sorted(rows,'watch',{key:'risk',direction:'desc'})),['known','hidden']);
});
test('equal scores preserve source order; dates and labels sort naturally',()=>{
 const rows=[{id:'a',values:{risk:50,date:'2026-11-04',name:'ETF10'}},{id:'b',values:{risk:50,date:'2026-10-30',name:'ETF2'}}];assert.deepEqual(ids(sorted(rows,'watch',{key:'risk',direction:'desc'})),['a','b']);assert.deepEqual(ids(sorted(rows,'externalMarket',{key:'date',direction:'asc'})),['b','a']);assert.deepEqual(ids(sorted(rows,'external',{key:'name',direction:'asc'})),['b','a']);
});
test('sort state and accessible headers are independent for each table',()=>{
 vm.runInContext("toggleRiskSort('external','weight');toggleRiskSort('external','weight');toggleRiskSort('domestic','price')",context);
 assert.equal(state.riskSorts.external.direction,'desc');assert.equal(state.riskSorts.domestic.direction,'asc');
 assert.match(vm.runInContext("riskTableHeader('external','weight','权重')",context),/aria-sort="descending"/);assert.match(vm.runInContext("riskTableHeader('domestic','price','价格')",context),/点击降序/);
 const before=JSON.stringify(state);vm.runInContext("toggleRiskSort('external','__proto__');setRiskLayout('bad','table')",context);assert.equal(JSON.stringify(state),before);
});
test('all supported table headers expose sorting while interpretation columns remain plain',()=>{
 const rows=[{id:'a',values:{risk:20},cells:['样本','20']}];const html=vm.runInContext('riskDataTable',context)('watch','自选风险表',[['name','自选标的'],['risk','综合关注分']],rows);assert.match(html,/aria-label="自选风险表"/);assert.match(html,/data-risk-sort="watch-risk"/);assert.match(vm.runInContext("riskTableHeader('watch',null,'解读')",context),/^<th scope="col">解读<\/th>$/);
});
