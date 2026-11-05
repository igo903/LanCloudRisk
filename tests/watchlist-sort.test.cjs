const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context=vm.createContext({CATALOG:[],window:{},uploaded:null,state:{},document:{getElementById:()=>({addEventListener(){}})},esc:s=>String(s)});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist/watchlist-manager.js'),'utf8'),context);
const sort=vm.runInContext('sortWatchRows',context),codes=r=>Array.from(r,x=>x.id),byValue=x=>x.value;
test('numeric sorting handles negatives and zero without mutating the watchlist',()=>{
 const rows=[{id:'a',value:10},{id:'b',value:-2},{id:'c',value:0},{id:'d',value:3}];
 assert.deepEqual(codes(sort(rows,{key:'ret5',direction:'asc'},byValue)),['b','c','d','a']);
 assert.deepEqual(codes(sort(rows,{key:'ret5',direction:'desc'},byValue)),['a','d','c','b']);assert.deepEqual(codes(rows),['a','b','c','d']);
});
test('missing data stays last in both directions and tied values stay stable',()=>{
 const rows=[{id:'null',value:null},{id:'a',value:2},{id:'nan',value:NaN},{id:'b',value:2},{id:'zero',value:0},{id:'missing'}];
 assert.deepEqual(codes(sort(rows,{key:'ret220',direction:'asc'},byValue)),['zero','a','b','null','nan','missing']);
 assert.deepEqual(codes(sort(rows,{key:'ret220',direction:'desc'},byValue)),['a','b','zero','null','nan','missing']);
});
test('names sort naturally and dates sort chronologically',()=>{
 assert.deepEqual(codes(sort([{id:'10',value:'ETF10'},{id:'2',value:'ETF2'}],{key:'name',direction:'asc'},byValue)),['2','10']);
 assert.deepEqual(codes(sort([{id:'new',value:'2026-11-04'},{id:'old',value:'2026-10-30'}],{key:'asOf',direction:'asc'},byValue)),['old','new']);
});
test('unknown sort columns preserve the original order',()=>{
 const rows=[{id:'a',value:1},{id:'b',value:2}];assert.deepEqual(codes(sort(rows,{key:'manage',direction:'desc'},byValue)),['a','b']);assert.deepEqual(codes(sort(rows,null,byValue)),['a','b']);
});
test('header toggles ascending and descending with accessible direction and keyboard focus',()=>{
 context.document={getElementById:()=>null};context.render=()=>{};
 vm.runInContext("toggleWatchSort('ret5')",context);assert.equal(vm.runInContext('state.watchSort.direction',context),'asc');
 let html=vm.runInContext("watchSortHeader('ret5','5日表现')",context);assert.match(html,/aria-sort="ascending"/);assert.match(html,/点击降序/);
 vm.runInContext("toggleWatchSort('ret5')",context);assert.equal(vm.runInContext('state.watchSort.direction',context),'desc');
 vm.runInContext("toggleWatchSort('name')",context);assert.equal(vm.runInContext('state.watchSort.direction',context),'asc');
 vm.runInContext("toggleWatchSort('asOf')",context);html=vm.runInContext("watchSortHeader('samples','样本 / 数据时间')",context);assert.match(html,/aria-sort="ascending"/);assert.match(html,/data-watch-sort="asOf"/);
});

test('all six watch fund columns sort numeric amounts, retain zero and keep missing last',()=>{
 context.watchFlowSnapshot=a=>({values:{flow1:a.value,flow3:a.value,flow5:a.value,flow10:a.value,flow20:a.value,flow60:a.value}});
 const rows=[{id:'missing',value:null},{id:'in',value:1e8},{id:'out',value:-3e8},{id:'zero',value:0}];
 const valueOf=vm.runInContext('watchSortValue',context);for(const key of ['flow1','flow3','flow5','flow10','flow20','flow60']){
 assert.deepEqual(codes(sort(rows,{key,direction:'asc'},valueOf)),['out','zero','in','missing']);assert.deepEqual(codes(sort(rows,{key,direction:'desc'},valueOf)),['in','zero','out','missing']);
 }
});
