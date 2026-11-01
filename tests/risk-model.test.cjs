const test=require('node:test'),assert=require('node:assert/strict');
const R=require('../dist/risk-engine.js');
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-8,`${a} != ${b}`);
const today='2026-11-01';
const history=(length=280,direction=-.002)=>Array.from({length},(_,i)=>({date:new Date(Date.parse('2026-10-30')-(length-i-1)*864e5).toISOString().slice(0,10),close:100*Math.exp(direction*i)}));
test('missing evidence propagates through both weighting levels with exact bounds',()=>{
 const external=R.aggregate([{score:80,weight:80},{score:null,weight:20}]);near(external.score,80);near(external.coverage,.8);near(external.low,64);near(external.high,84);
 const domestic=R.aggregate([{score:20,coverage:1,weight:70},{...external,weight:30}]);near(domestic.coverage,.94);near(domestic.score,(20*70+80*24)/94);near(domestic.low,33.2);near(domestic.high,39.2);
});
test('entirely missing evidence never becomes neutral or zero risk',()=>{
 const r=R.aggregate([{score:null,weight:1},{score:NaN,weight:3}]);assert.equal(r.score,null);assert.equal(r.coverage,0);assert.equal(r.low,0);assert.equal(r.high,100);
});
test('stale and future history cannot contribute a risk score',()=>{
 const r=R.technical(history(),'equity','2027-01-01');assert.equal(r.score,null);
 assert.equal(R.fresh('2026-11-02',7,today),false);assert.equal(R.fresh('2026-10-30',7,today),true);
});
test('upward macro pressure and downward equity pressure have distinct directions',()=>{
 const up=history(280,.002),down=history(280,-.002);
 assert.ok(R.technical(up,'macro',today).score>R.technical(down,'macro',today).score);
 assert.ok(R.technical(down,'equity',today).score>R.technical(up,'equity',today).score);
});
test('metals score absolute shocks, not a universal equity bearish direction',()=>{
 const up=R.technical(history(280,.002),'metal',today),down=R.technical(history(),'metal',today);
 assert.ok(up.score>0&&down.score>0);assert.equal(up.coverage,1);
});
test('all 15 expected assets remain in the denominator when providers fail',()=>{
 const rows=[{symbol:'usSOXX',kind:'equity',history:history()}],g=R.globalRisk(rows,{},today);
 near(g.coverage,.06);assert.equal(g.assets.length,15);near(g.low,g.score*.06);
});
test('GPR data exceeding 14 calendar days is excluded rather than scored as safe',()=>{
 const g=R.globalRisk([],{as_of:'2026-10-01',items:[{kind:'war',score:99},{kind:'politics',score:99}]},today);assert.equal(g.score,null);
 const fresh=R.globalRisk([],{as_of:'2026-10-26',items:[{kind:'war',score:99},{kind:'politics',score:20}]},today);near(fresh.coverage,.2);near(fresh.score,59.5);
});
test('ETF assessment handles missing NAV and does not apply company financial ratios',()=>{
 const r=R.watchRisk({bars:history()}, {code:'588080',type:'etf'},R.aggregate([]),today);
 assert.equal(r.local.parts[1].name,'成分集中度');assert.equal(r.local.parts[1].score,null);assert.equal(r.local.parts[2].score,null);assert.ok(r.coverage>0);
});
test('mismatched NAV dates cannot be called same-day ETF premium risk',()=>{
 const r=R.watchRisk({bars:history(),nav:{date:'2026-10-29',value:100}}, {code:'588080',type:'etf'},R.aggregate([]),today);assert.equal(r.local.parts[2].score,null);
});
test('good accounting results do not invent valuation or governance impact scores',()=>{
 const instrument={bars:history(),fundamentals:{period:'2026-09-30',notice_date:'2026-10-26',profit:1e8,revenueGrowth:30,cashRatio:1.5,debtRatio:30},announcements:[{title:'董事会公告'}]};
 const r=R.watchRisk(instrument,{code:'002281',type:'stock'},R.aggregate([]),today);assert.ok(r.local.parts[1].coverage===1);assert.equal(r.local.parts[2].score,null);assert.equal(r.local.parts[6].score,null);
});
test('SOX and SOXX are distinct risk leaves sharing the original 12 percent semiconductor budget',()=>{
 const g=R.globalRisk([{symbol:'SOX',kind:'equity',history:history()},{symbol:'usSOXX',kind:'equity',history:history()}],{},today);
 const semiconductor=g.assets.filter(x=>['SOX','usSOXX'].includes(x.symbol));assert.equal(semiconductor.length,2);assert.equal(semiconductor.reduce((s,x)=>s+x.weight,0),12);near(g.coverage,.12);
 assert.equal(g.parts.reduce((s,x)=>s+x.weight,0),100);
});
