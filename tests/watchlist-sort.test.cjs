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
test('header cycles ascending, descending and default with accessible next actions',()=>{
 context.document={getElementById:()=>null};context.render=()=>{};
 vm.runInContext("toggleWatchSort('ret5')",context);assert.equal(vm.runInContext('state.watchSort.direction',context),'asc');
 let html=vm.runInContext("watchSortHeader('ret5','5日表现')",context);assert.match(html,/aria-sort="ascending"/);assert.match(html,/点击降序/);
 vm.runInContext("toggleWatchSort('ret5')",context);assert.equal(vm.runInContext('state.watchSort.direction',context),'desc');
 html=vm.runInContext("watchSortHeader('ret5','5日表现')",context);assert.match(html,/点击恢复默认排序/);
 vm.runInContext("toggleWatchSort('ret5')",context);assert.equal(vm.runInContext('state.watchSort',context),null);
 html=vm.runInContext("watchSortHeader('ret5','5日表现')",context);assert.match(html,/aria-sort="none"/);assert.match(html,/点击升序/);assert.match(html,/↕/);
 const rows=[{id:'missing',value:null},{id:'high',value:8},{id:'low',value:-2}];
 assert.deepEqual(codes(sort(rows,vm.runInContext('state.watchSort',context),byValue)),['missing','high','low']);
 vm.runInContext("toggleWatchSort('name')",context);assert.equal(vm.runInContext('state.watchSort.direction',context),'asc');
 vm.runInContext("toggleWatchSort('asOf')",context);html=vm.runInContext("watchSortHeader('samples','样本 / 数据时间')",context);assert.match(html,/aria-sort="ascending"/);assert.match(html,/data-watch-sort="asOf"/);
});

test('option underlyings share the single category row and restore screenshot order after sorting',()=>{
 const optionCodes=['510050','510300','159919','510500','588000','588080','159901','159915','159922'];
 const stocks=[{code:'US.510050',market:'US',type:'etf',name:'foreign'},...optionCodes.slice().reverse().map((code,i)=>({code,name:'ETF '+code,type:'etf',value:i}))];
 Object.assign(context,{WATCH_CODES:stocks.map(a=>a.code),byCode:code=>stocks.find(a=>a.code===code),hasLocalServer:true,mode:'local',localStatus:null,render(){},title:()=>'',button:()=>'',icon:()=>'',filteredStocks:context.window.filteredStocks,watchTable:()=>'',state:{filter:'options',query:''}});
 const listed=()=>Array.from(vm.runInContext('managedFilteredStocks()',context),a=>a.code);
 assert.deepEqual(listed(),optionCodes);
 const html=vm.runInContext('managedWatchlist()',context);assert.equal((html.match(/class="filter-tabs/g)||[]).length,1);assert.match(html,/>期权标的 9<\/button>/);assert.match(html,/>ETF 10<\/button>/);
 context.state.query='1599';assert.deepEqual(listed(),['159919','159901','159915','159922']);context.state.query='';
 const rows=vm.runInContext('managedFilteredStocks()',context);
 vm.runInContext("toggleWatchSort('ret5');toggleWatchSort('ret5');toggleWatchSort('ret5')",context);
 assert.deepEqual(Array.from(sort(rows,context.state.watchSort,byValue),a=>a.code),optionCodes);
 context.WATCH_CODES=context.WATCH_CODES.filter(code=>code!=='510050');assert.deepEqual(listed(),optionCodes.slice(1));
 assert.match(vm.runInContext('managedWatchlist()',context),/>期权标的 8<\/button>/);
 assert.equal(vm.runInContext("watchMatchesCategory({code:'510050',type:'stock'},'options')",context),false);
});

test('all six watch fund columns sort numeric amounts, retain zero and keep missing last',()=>{
 context.watchFlowSnapshot=a=>({values:{flow1:a.value,flow3:a.value,flow5:a.value,flow10:a.value,flow20:a.value,flow60:a.value}});
 const rows=[{id:'missing',value:null},{id:'in',value:1e8},{id:'out',value:-3e8},{id:'zero',value:0}];
 const valueOf=vm.runInContext('watchSortValue',context);for(const key of ['flow1','flow3','flow5','flow10','flow20','flow60']){
 assert.deepEqual(codes(sort(rows,{key,direction:'asc'},valueOf)),['out','zero','in','missing']);assert.deepEqual(codes(sort(rows,{key,direction:'desc'},valueOf)),['in','zero','out','missing']);
 }
});
test('one category row includes one All tab and counts ETFs by their market',()=>{
 const stocks=[
  {code:'600519',type:'stock'},{code:'US.AAPL',market:'US',type:'stock'},
  {code:'US.QQQ',type:'etf'},{code:'HK.00700',market:'HK',type:'stock'},{code:'HK.02800',market:'HK',type:'etf'}
 ];
 Object.assign(context,{
  WATCH_CODES:stocks.map(stock=>stock.code),byCode:code=>stocks.find(stock=>stock.code===code),
  hasLocalServer:true,mode:'local',localStatus:null,render(){},title:()=>'',button:()=>'',icon:()=>'',
  filteredStocks:context.window.filteredStocks,watchTable:()=>'',state:{filter:'all',query:''}
 });
 const listed=()=>Array.from(vm.runInContext('managedFilteredStocks()',context),asset=>asset.code);
 const html=vm.runInContext('managedWatchlist()',context);
 assert.equal((html.match(/class="filter-tabs/g)||[]).length,1);
 assert.equal((html.match(/>全部 5<\/button>/g)||[]).length,1);
 assert.match(html,/>港股 2<\/button>.*>美股 2<\/button>/s);
 vm.runInContext("state.filter='US'",context);
 assert.deepEqual(listed(),['US.AAPL','US.QQQ']);
 vm.runInContext("state.filter='etf'",context);
 assert.deepEqual(listed(),['US.QQQ','HK.02800']);
 vm.runInContext("state.filter='HK'",context);
 assert.deepEqual(listed(),['HK.00700','HK.02800']);
});
