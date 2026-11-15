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

const date=i=>new Date(Date.UTC(2026,0,i+1)).toISOString().slice(0,10);
const boostedBars=()=>Array.from({length:69},(_,i)=>({date:date(i),open:10,close:10-i/1000,
 high:10.01,low:9.5,vol:1000,netFlow:3e8,completed:true}));
function runFixture(bars,settings={},otherInstruments={}){
 const code='002281',data={instruments:{...otherInstruments,[code]:{bars,completed:true}}};
 return E.backtest(data,code,{capital:100000,position:25,fee:0,slip:0,lot:100,execution:'open',
  start:date(60),end:bars.at(-1).date,enhanced:true,...settings});
}

test('repeated 25% signals reach full exposure, skip further buys and settle all four entries together',()=>{
 const bars=boostedBars();
 const closes=[10.2,10.4,10.6,10.8,11,11.2,11.4,11.6,11.8,12,11,10.5,10,9.5,9,8.5,8];
 bars.push(...closes.map((price,i)=>({date:date(69+i),open:price,close:price,high:price+.1,low:price-.1,
  vol:1000,netFlow:0,completed:true})));
 const r=runFixture(bars),buys=r.orders.filter(o=>o.type==='buy'&&o.status==='模拟成交');
 assert.deepEqual(Array.from(buys,o=>o.qty),[2500,2500,2500,2500]);
 assert.deepEqual(Array.from(buys,o=>o.positionPct),[25,50,75,100]);
 const skipped=r.orders[4];
 assert.equal(skipped.status,'未成交/跳过');assert.match(skipped.reason,/已满仓/);
 assert.equal(skipped.positionPct,null);
 const sell=r.orders.find(o=>o.type==='sell'&&o.status==='模拟成交');
 assert.equal(sell.qty,10000);assert.equal(sell.positionPct,0);assert.equal(sell.cycleBuyCount,4);
 near(sell.cycleBuyCost,100000);near(sell.cycleReturn,-10);near(r.ret,-10);
});

test('tranche sizing uses current total equity, caps spending at cash and respects fees and lots',()=>{
 const moving=boostedBars();
 moving[62]={...moving[62],open:20,high:20.1};
 const second=runFixture(moving,{end:date(62)}).orders[1];
 assert.equal(second.qty,1500);near(second.positionPct,64);
 const capped=runFixture(boostedBars(),{position:75});
 assert.deepEqual(Array.from(capped.orders.slice(0,2),o=>o.positionPct),[75,100]);
 const withFees=runFixture(boostedBars(),{position:75,fee:10});
 assert.deepEqual(Array.from(withFees.orders.slice(0,2),o=>o.qty),[7400,2500]);
 near(withFees.cash,901);assert.match(withFees.orders[2].reason,/剩余现金不足/);
 const full=boostedBars();full[62]={...full[62],open:5,low:4.9};
 const single=runFixture(full,{position:100,fee:10});
 assert.equal(single.fillCount,1);assert.match(single.orders[1].reason,/已按100%研究仓位建仓/);
});

test('a negative three-factor score no longer blocks grade B, while grade A and the separate market gate keep their rules',()=>{
 const bars=Array.from({length:62},(_,i)=>({date:date(i),open:10,close:i===60?10.1:10,
  high:10.18,low:9.92,vol:1000,netFlow:i===60?-1e9:0,completed:true}));
 const b=runFixture(bars);
 assert.equal(b.signals[0].grade,'B');assert.ok(b.signals[0].score<-.5);assert.equal(b.signals[0].filtered,false);
 assert.equal(b.orders[0].status,'模拟成交');assert.equal(b.orders[0].date,date(61));
 assert.match(runFixture(bars,{marketGate:true}).orders[0].reason,/六ETF历史不足/);
 const stronger=bars.map((bar,i)=>i===60?{...bar,high:10.11,low:9.9,vol:1300}:bar);
 const a=runFixture(stronger);
 assert.equal(a.signals[0].grade,'A');assert.ok(a.signals[0].score<-.5);assert.equal(a.signals[0].filtered,true);
 assert.equal(a.orders[0].status,'未成交/跳过');
});

test('the last confirmed signal is listed through missing, intraday, out-of-range and confirmed next sessions',()=>{
 const bars=boostedBars().slice(0,62),signalDay=date(60),nextDay=date(61);
 const order=r=>r.orders.find(o=>o.signal===signalDay);
 const missing=runFixture(bars.slice(0,61),{end:nextDay});
 assert.equal(order(missing).grade,'B');assert.equal(order(missing).date,null);
 assert.equal(order(missing).status,'待次日日线');assert.equal(missing.fillCount,0);
 const liveBars=[...bars.slice(0,-1),{...bars.at(-1),completed:false}];
 const live=runFixture(liveBars);
 assert.equal(order(live).date,nextDay);assert.equal(order(live).status,'待次日日线');
 assert.equal(live.provisionalSignals[0].date,nextDay);
 assert.equal(live.orders[1].status,'待信号确认');assert.equal(live.fillCount,0);near(live.ret,0);
 const beyond=runFixture(bars,{end:signalDay});
 assert.equal(order(beyond).date,nextDay);assert.equal(order(beyond).status,'区间外');assert.equal(beyond.fillCount,0);
 const closed=runFixture(bars);
 assert.equal(order(closed).date,nextDay);assert.equal(order(closed).status,'模拟成交');assert.equal(closed.fillCount,1);
});

test('a missing or provisional historical execution bar is never bypassed for a later close',()=>{
 const bars=boostedBars().slice(0,63),session={bars:[bars[61]],completed:true};
 const missing=runFixture(bars.filter((_,i)=>i!==61),{}, {'510300':session});
 const incomplete=runFixture(bars.map((b,i)=>i===61?{...b,completed:false}:b));
 for(const r of [missing,incomplete]){
  const order=r.orders.find(o=>o.signal===date(60));
  assert.equal(order.date,date(61));assert.equal(order.status,'未成交/跳过');
  assert.match(order.reason,/订单未顺延/);assert.equal(r.fillCount,0);
 }
});

test('real backtest accounting reconciles across dates, allocation, costs and execution models',()=>{
 let rounds=0,open=0;
 for(const code of ['002281','588080','688825'])for(const execution of ['open','limit'])for(const position of [25,50,100])for(const fee of [0,8]){
  const p={capital:100000,position,fee,slip:5,lot:100,execution,limit:3,start:'2026-01-01',end:'2026-10-29',enhanced:false,marketGate:false};
  const r=E.backtest(E.demo,code,p);assert.ok(!r.error);
  near(r.lastEquity-p.capital,r.realizedProfit+(r.units*r.bars.at(-1).close-r.openCost));
  near(r.ret,(r.lastEquity/p.capital-1)*100);assert.equal(r.fillCount,r.trades.length);assert.equal(r.openUnits,r.units);
  if(r.units){
   const position=r.openPosition,last=r.bars.at(-1);
   near(position.value,r.units*last.close);near(position.return,(position.value/r.openCost-1)*100);
   near(position.profit,position.value-r.openCost);assert.equal(position.date,last.date);
  }else assert.equal(r.openPosition,null);
  const sells=r.orders.filter(o=>o.cycleReturn!==null),trades=r.trades.filter(t=>t.pnl!==null);
  assert.equal(sells.length,r.completed);
  let buys=0,sellIndex=0;
  for(const order of r.orders.filter(o=>o.status==='模拟成交')){
   if(order.type==='buy')buys++;
   else {near(order.cycleReturn,trades[sellIndex++].pnl);assert.equal(order.cycleBuyCount,buys);buys=0;}
  }
  assert.ok(r.orders.every(o=>o.signal>=p.start&&o.signal<=p.end));
  assert.ok(r.trades.every(o=>o.date>o.signal&&o.date<=p.end));
  assert.equal(r.orders.length,r.signals.length+r.provisionalSignals.length);
  rounds+=sells.length;open+=Number(r.units>0);
 }
 assert.ok(rounds>0);assert.ok(open>0);
});
const htmlCtx=vm.createContext({E,esc:s=>String(s??'').replaceAll('<','&lt;'),cls:n=>n<0?'negative':'positive',change:n=>(n>=0?'+':'')+n.toFixed(2)+'%',num:(n,d=3)=>E.valid(n)?n.toFixed(d):'—',money:n=>'¥'+n.toFixed(2),pill:s=>s});
const source=fs.readFileSync(path.join(root,'strategy-views.js'),'utf8');
vm.runInContext(source.slice(source.indexOf('function btOrdersTable(r)')),htmlCtx);
const render=r=>{htmlCtx.result=r;return vm.runInContext('btOrdersTable(result)',htmlCtx)};
test('orders table renders the new return column, sell value and one final total row',()=>{
 const summary=E.summarizeOrders([{...filled('buy',100,10,1),positionPct:25},filled('buy',200,12,2),
  {...filled('sell',300,13,3),positionPct:0}]);
 const html=render({...summary,capital:100000,lastEquity:100494,ret:.494,units:0,bars:[{date:'2026-10-30'}]});
 assert.match(html,/本轮收益率（卖出时）/);assert.match(html,/合并 2 笔买入/);assert.match(html,/\+14\.52%/);assert.match(html,/总收益率（当前区间全部成交）/);assert.match(html,/\+0\.49%/);
 assert.equal((html.match(/<tfoot>/g)||[]).length,1);assert.match(html,/含期末未平仓估值/);
 assert.match(html,/价格 \/ 数量 \/ 仓位占比/);assert.match(html,/10\.000 \/ 100 \/ 25\.00%/);
 assert.match(html,/13\.000 \/ 300 \/ 0\.00%/);assert.match(html,/class="panel-note bt-orders-note">本轮收益率/);
 assert.doesNotMatch(html,/当前持仓收益率/);
});
test('remaining position return appears after the account total, even after a skipped sell',()=>{
 const orders=[filled('buy',100,10,1,'2026-10-29'),{...filled('sell',0,12,0,'2026-10-30'),status:'未成交/跳过'}];
 const html=render({...E.summarizeOrders(orders),capital:10000,lastEquity:10199,ret:1.99,units:100,bars:[{date:'2026-10-30'}],
  openPosition:{units:100,cost:1001,value:1200,profit:199,return:199/1001*100,price:12,date:'2026-10-30',buyDate:'2026-10-29'}});
 assert.match(html,/总收益率（当前区间全部成交）[\s\S]*当前持仓收益率（未实现）/);
 assert.match(html,/\+19\.88%/);assert.match(html,/剩余买入成本 ¥1001\.00/);assert.match(html,/浮动盈亏 ¥199\.00/);
});
test('empty orders table retains its summary row with a dash',()=>{
 const html=render({...E.summarizeOrders([]),ret:0,capital:100000,lastEquity:100000,units:0,bars:[{date:'2026-10-30'}]});
 assert.match(html,/colspan="6"/);assert.match(html,/当前区间尚无成交/);assert.match(html,/<strong class="muted">—<\/strong>/);assert.ok(!html.includes('NaN'));
});
test('orders table displays a provisional-only interval without fabricating a fill or a return',()=>{
 const bars=boostedBars().slice(0,62);bars.at(-1).completed=false;
 const r=runFixture(bars,{start:date(61),end:date(61)}),html=render(r);
 assert.match(r.error,/没有已确认收盘日线/);
 assert.match(html,/待定[\s\S]*买入 · B级[\s\S]*待信号确认/);
 assert.match(html,/<strong class="muted">—<\/strong>/);assert.doesNotMatch(html,/模拟成交<\/td>/);
});
test('changing the start does not pin the end date that follows local snapshots',()=>{
 const context=vm.createContext({bt:{},render(){}});
 vm.runInContext(fs.readFileSync(path.join(root,'interactive-chart.js'),'utf8'),context);
 assert.equal(vm.runInContext("changeBacktestDate('start','2026-11-11');bt.endTouched",context),undefined);
 assert.equal(vm.runInContext('bt.startTouched',context),true);
 assert.equal(vm.runInContext("changeBacktestDate('end','2026-11-12');bt.endTouched",context),true);
});
