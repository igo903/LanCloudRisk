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
