const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const root=require('node:path').join(__dirname,'..','dist'),context=vm.createContext({});
for(const name of ['data.js','engine.js'])vm.runInContext(fs.readFileSync(root+'/'+name,'utf8'),context);
const E=vm.runInContext('RiskEngine',context);
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-8,`${a} != ${b}`);
const bars=n=>Array.from({length:n},(_,i)=>({date:new Date(Date.parse('2025-01-01')+i*2*864e5).toISOString().slice(0,10),close:100+i,vol:10000,open:100+i,high:101+i,low:99+i,completed:true}));
test('60 and 220 interval returns use 61 and 221 price observations exactly',()=>{
 const b=bars(221),m=E.metrics(b);near(m.ret220,220);near(m.ret60,(320/260-1)*100);
 assert.equal(E.metrics(b.slice(0,60)).ret60,null);assert.equal(E.metrics(b.slice(0,220)).ret220,null);
});
test('short history is not borrowed from another stock or interpolated to fill 220 periods',()=>{
 const m=E.metrics(bars(69));assert.ok(Number.isFinite(m.ret60));assert.equal(m.ret220,null);
 const real=E.metrics(E.demo.instruments['688825'].bars);assert.equal(real.ret220,null);
});
test('an intraday latest close updates the observation return without changing confirmed history',()=>{
 const b=bars(222),snapshot={instruments:{x:{completed:false,bars:[...b.slice(0,-1),{...b.at(-1),close:400,high:401,completed:false}]}}};
 const closed=E.metrics(E.series(snapshot,'x')),live=E.metrics(E.series(snapshot,'x',{includeLive:true}));
 near(closed.ret220,220);near(live.ret220,(400/101-1)*100);assert.notEqual(closed.ret60,live.ret60);
});
test('flat returns are a valid zero rather than missing data',()=>{
 const m=E.metrics(bars(221).map(x=>({...x,close:100})));assert.equal(m.ret60,0);assert.equal(m.ret220,0);
});
test('5 day performance uses six observations and keeps zero and short history distinct',()=>{
 near(E.metrics(bars(6)).ret5,5);assert.equal(E.metrics(bars(5)).ret5,null);
 assert.equal(E.metrics(bars(6).map(b=>({...b,close:100}))).ret5,0);
});
test('cross-market snapshots validate declared identities before accepting price data',()=>{
 const meta={code:'US.NVDA',symbol:'NVDA',name:'英伟达',market:'US',currency:'USD',type:'stock',watch:true};
 const snap={catalog:[meta],instruments:{'US.NVDA':{source:'Yahoo',as_of:'2025-01-11',adjustment:'provider',bars:bars(6)}}};
 assert.equal(E.validateSnapshot(snap).catalog[0].currency,'USD');
 for(const bad of [{...meta,code:'NVDA'},{...meta,symbol:'https://evil'},{...meta,name:'<img>'}])assert.throws(()=>E.validateCatalog([bad]));
 assert.throws(()=>E.validateCatalog([meta,meta]));
});
test('foreign backtests cannot be blocked by the A-share ETF gate',()=>{
 vm.runInContext("CATALOG.push({code:'US.TEST',market:'US',type:'stock',name:'Test'})",context);
 const record=E.demo.instruments['002281'],snap={instruments:{'US.TEST':record}},b=record.bars;
 const p={start:b[0].date,end:b.at(-1).date,capital:1e6,position:50,fee:8,slip:5,lot:1,execution:'open',limit:3,enhanced:false};
 const a=E.backtest(snap,'US.TEST',{...p,marketGate:false}),g=E.backtest(snap,'US.TEST',{...p,marketGate:true});
 assert.ok(a.orders.length>0);assert.equal(JSON.stringify(a.orders),JSON.stringify(g.orders));
});
