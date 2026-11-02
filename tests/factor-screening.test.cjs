const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const ctx=vm.createContext({window:{},num:(v,d=1)=>typeof v==='number'?v.toFixed(d):'—'}),root=path.join(__dirname,'..','dist');
for(const file of ['data.js','engine.js'])vm.runInContext(fs.readFileSync(path.join(root,file),'utf8'),ctx);
vm.runInContext('const E=RiskEngine;',ctx);vm.runInContext(fs.readFileSync(path.join(root,'factor-screening.js'),'utf8'),ctx);
const E=vm.runInContext('RiskEngine',ctx),screen=vm.runInContext('screenAssess',ctx),weights=[0,0,0,0,1,0];
const day=new Date().toISOString().slice(0,10),assets=Array.from({length:4},(_,i)=>({code:'60000'+i,symbol:'60000'+i,name:'样本'+i,market:'CN',currency:'CNY',type:'stock',watch:false}));
const b=[{date:day,open:10,high:11,low:9,close:10,vol:100,completed:true}],result=()=>({catalog:assets,instruments:Object.fromEntries(assets.map((a,i)=>[a.code,{bars:b,as_of:day,fundamentals:{marketCap:(i+1)*100}}]))});
test('screen starts with no universe, selected securities, or manufactured results',()=>{
 assert.equal(vm.runInContext('factorScreen.source',ctx),'');assert.equal(vm.runInContext('factorScreen.codes.length',ctx),0);assert.equal(vm.runInContext('factorScreen.result',ctx),null);
});
test('explicit screening universe never falls back to default watchlist and supports non-watch constituents',()=>{
 assert.equal(E.factorRows({instruments:{}},weights,[]).length,0);
 const rows=E.factorRows(result(),weights,assets.slice(0,3));assert.equal(rows.length,3);assert.ok(rows.every(r=>assets.some(a=>a.code===r.asset.code)));assert.ok(rows.every(r=>r.total!==null));
});
test('button-run screening applies minimum score and top N with distinct exclusion reasons',()=>{
 const q=screen(result(),{weights,minimum:50,limit:1});assert.equal(q.shown.length,1);assert.equal(q.shown[0].asset.code,assets[0].code);assert.equal(q.matched.length,2);
 assert.match(q.reason(q.shown[0]),/相对优势：规模/);assert.ok(q.excluded.some(r=>q.reason(r).includes('前1名之外')));assert.ok(q.excluded.some(r=>q.reason(r).includes('低于最低50分')));
});
test('missing indicators and insufficient market peers produce reasons, never fabricated ranks',()=>{
 const r=result();delete r.instruments[assets[0].code].fundamentals.marketCap;delete r.instruments[assets[1].code].fundamentals.marketCap;
 const q=screen(r,{weights,minimum:0,limit:10});assert.equal(q.shown.length,0);assert.ok(q.all.every(r=>r.total===null));assert.match(q.reason(q.all[0]),/至少3个/);
});
test('expired prices cannot contribute size scores from retained fundamentals',()=>{
 const r=result();r.instruments[assets[0].code].as_of='2020-01-01';const q=screen(r,{weights,minimum:0,limit:10});const old=q.all.find(a=>a.asset.code===assets[0].code);assert.equal(old.total,null);assert.match(q.reason(old),/过期/);
});
test('market groups are not compared or ranked with another currency',()=>{
 const r=result();r.catalog=[...assets.slice(0,3),{...assets[3],code:'US.TEST',symbol:'TEST',market:'US',currency:'USD'}];r.instruments['US.TEST']={bars:b,as_of:day,fundamentals:{marketCap:1}};
 const rows=E.factorRows(r,weights,r.catalog);assert.equal(rows.find(r=>r.asset.code==='US.TEST').total,null);assert.ok(rows.filter(r=>r.asset.market==='CN').every(r=>r.total!==null));
});
test('editing a threshold updates draft state without rewriting the prior run or results',()=>{
 ctx.document={querySelector:()=>null};
 vm.runInContext('factorScreen.result={previous:true};factorScreen.runConfig={minimum:50};',ctx);
 vm.runInContext('setScreenMinimum({value:"70"})',ctx);
 assert.equal(vm.runInContext('factorScreen.minimum',ctx),70);assert.equal(vm.runInContext('factorScreen.runConfig.minimum',ctx),50);assert.equal(vm.runInContext('factorScreen.result.previous',ctx),true);assert.equal(vm.runInContext('factorScreen.dirty',ctx),true);
 vm.runInContext('factorScreen.result=null;factorScreen.runConfig=null;factorScreen.minimum=50;factorScreen.dirty=false;',ctx);
});
