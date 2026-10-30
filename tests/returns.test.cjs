const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');
const path=require('node:path'),root=path.join(__dirname,'..','dist');
const ctx=vm.createContext({console});
vm.runInContext(fs.readFileSync(path.join(root,'data.js'),'utf8'),ctx);
vm.runInContext(fs.readFileSync(path.join(root,'engine.js'),'utf8'),ctx);
const E=vm.runInContext('RiskEngine',ctx);
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} != ${b}`);
const filled=(type,qty,price,cost,date='2026-10-01')=>({type,qty,price,cost,date,signal:date,status:'模拟成交',reason:'研究成交假设'});

test('multiple buys settle using total actual cost and net sell proceeds',()=>{
 const r=E.summarizeOrders([filled('buy',100,10,1),filled('buy',200,12,2),filled('sell',300,13,3)]),o=r.orders[2];
 near(o.cycleBuyCost,3403);near(o.cycleNetProceeds,3897);near(o.cycleReturn,494/3403*100);
 assert.equal(o.cycleBuyCount,2);near(r.realizedProfit,494);assert.equal(r.openUnits,0);near(r.openCost,0);
});
test('unfilled and skipped orders never affect cost, buy count or returns',()=>{
 const r=E.summarizeOrders([{...filled('buy',100,1,0),status:'未成交/跳过'},filled('buy',100,10,1),{...filled('sell',100,99,0),status:'未成交/跳过'},filled('sell',100,11,1)]);
 assert.equal(r.fillCount,2);assert.equal(r.orders[3].cycleBuyCount,1);near(r.orders[3].cycleReturn,98/1001*100);
 assert.equal(r.orders[0].cycleReturn,null);assert.equal(r.orders[2].cycleReturn,null);
});
test('cost and buy count reset after a complete sale',()=>{
 const r=E.summarizeOrders([filled('buy',100,10,0),filled('sell',100,11,0),filled('buy',100,20,0),filled('sell',100,18,0)]);
 near(r.orders[1].cycleReturn,10);near(r.orders[3].cycleReturn,-10);assert.equal(r.orders[3].cycleBuyCount,1);near(r.realizedProfit,-100);
});
test('partial sales allocate weighted cost and preserve the remaining basis',()=>{
 const orders=[filled('buy',100,10,1),filled('buy',200,12,2),filled('sell',100,13,1),filled('sell',200,11,2)];
 const first=E.summarizeOrders(orders.slice(0,3));near(first.orders[2].cycleBuyCost,3403/3);near(first.openCost,3403*2/3);assert.equal(first.openUnits,200);
 const all=E.summarizeOrders(orders);near(all.orders[3].cycleBuyCost,3403*2/3);near(all.realizedProfit,94);near(all.openCost,0);
});
test('a losing round stays negative and an open buy has no realized round return',()=>{
 const r=E.summarizeOrders([filled('buy',100,10,1),filled('sell',100,8,1),filled('buy',100,9,1)]);
 near(r.orders[1].cycleReturn,-202/1001*100);assert.equal(r.orders[2].cycleReturn,null);near(r.openCost,901);assert.equal(r.openUnits,100);
});
test('no fills produces no first trade date or manufactured rate',()=>{
 const r=E.summarizeOrders([{...filled('buy',0,10,0),status:'未成交/跳过'}]);
 assert.equal(r.fillCount,0);assert.equal(r.firstTradeDate,null);assert.equal(r.orders[0].cycleReturn,null);
});
test('real backtest accounting reconciles across dates, allocation, costs and execution models',()=>{
 let rounds=0,open=0;
 for(const code of ['002281','588080','688825'])for(const execution of ['open','limit'])for(const position of [25,50,100])for(const fee of [0,8]){
  const p={capital:100000,position,fee,slip:5,lot:100,execution,limit:3,start:'2026-01-01',end:'2026-10-29',enhanced:false,marketGate:false};
  const r=E.backtest(E.demo,code,p);assert.ok(!r.error);
  near(r.lastEquity-p.capital,r.realizedProfit+(r.units*r.bars.at(-1).close-r.openCost));
  near(r.ret,(r.lastEquity/p.capital-1)*100);assert.equal(r.fillCount,r.trades.length);assert.equal(r.openUnits,r.units);
  const sells=r.orders.filter(o=>o.cycleReturn!==null),trades=r.trades.filter(t=>t.pnl!==null);
  assert.equal(sells.length,r.completed);sells.forEach((o,i)=>{near(o.cycleReturn,trades[i].pnl);assert.equal(o.cycleBuyCount,1)});
  assert.ok(r.orders.every(o=>o.date>=p.start&&o.date<=p.end));rounds+=sells.length;open+=Number(r.units>0);
 }
 assert.ok(rounds>0);assert.ok(open>0);
});
const htmlCtx=vm.createContext({E,esc:s=>String(s??'').replaceAll('<','&lt;'),cls:n=>n<0?'negative':'positive',change:n=>(n>=0?'+':'')+n.toFixed(2)+'%',num:n=>E.valid(n)?n.toFixed(3):'—',money:n=>'¥'+n.toFixed(2),pill:s=>s});
const source=fs.readFileSync(path.join(root,'strategy-views.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('function btOrdersTable(r)')),htmlCtx);
const render=r=>{htmlCtx.result=r;return vm.runInContext('btOrdersTable(result)',htmlCtx)};
test('orders table renders the new return column, sell value and one final total row',()=>{
 const summary=E.summarizeOrders([filled('buy',100,10,1),filled('buy',200,12,2),filled('sell',300,13,3)]);
 const html=render({...summary,capital:100000,lastEquity:100494,ret:.494,units:0,bars:[{date:'2026-10-30'}]});
 assert.match(html,/本轮收益率（卖出时）/);assert.match(html,/合并 2 笔买入/);assert.match(html,/\+14\.52%/);assert.match(html,/总收益率（当前区间全部成交）/);assert.match(html,/\+0\.49%/);
 assert.equal((html.match(/<tfoot>/g)||[]).length,1);assert.match(html,/含期末未平仓估值/);
});
test('empty orders table retains its summary row with a dash',()=>{
 const html=render({...E.summarizeOrders([]),ret:0,capital:100000,lastEquity:100000,units:0,bars:[{date:'2026-10-30'}]});
 assert.match(html,/colspan="6"/);assert.match(html,/当前区间尚无成交/);assert.match(html,/<strong class="muted">—<\/strong>/);assert.ok(!html.includes('NaN'));
});
