const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..');
const U=require(path.join(root,'dist/user-state.js'));
const note=(name='研究A')=>({name,code:'002281',topic:'七维研究',kind:'不确定',evidence:'原始事实',counter:'验证条件',time:'2026/11/1'});
function memory(){const data=new Map();return {getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),data}}

test('legacy records receive stable unique identities without changing saved content on read',()=>{
 const s=memory(),raw=JSON.stringify([note(),note()]);s.setItem(U.NOTES_KEY,raw);const store=U.createStore(s),a=store.list(),b=store.list();
 assert.equal(a[0].id,b[0].id);assert.notEqual(a[0].id,a[1].id);assert.equal(s.getItem(U.NOTES_KEY),raw);
 const deleted=store.remove(a[0].id);assert.equal(deleted.length,1);assert.equal(store.list().length,1);assert.equal(store.list()[0].id,a[1].id);
});
test('single delete and undo preserve remaining records and their order',()=>{
 const s=memory(),store=U.createStore(s);store.add(note('C'));const b=store.add(note('B'));store.add(note('A'));
 const deleted=store.remove(b.id);assert.deepEqual(store.list().map(n=>n.name),['A','C']);store.restore(deleted);
 assert.deepEqual(store.list().map(n=>n.name),['A','B','C']);
});
test('clearing records only affects notes, not the theme or market snapshot',()=>{
 const s=memory(),store=U.createStore(s);store.add(note());store.saveTheme('polymarket');s.setItem('risklens-v2-snapshot','retained');
 const removed=store.clear(store.list().map(n=>n.id));assert.equal(store.list().length,0);assert.equal(removed.length,1);
 assert.equal(s.getItem('risklens-v2-snapshot'),'retained');assert.equal(store.getTheme(),'polymarket');
});
test('a concurrent new note aborts a stale clear confirmation',()=>{
 const store=U.createStore(memory());store.add(note('A'));const approved=store.list().map(n=>n.id);store.add(note('B'));
 assert.throws(()=>store.clear(approved),/重新确认/);assert.equal(store.list().length,2);
});
test('undo merges with newly added records and cannot restore duplicates',()=>{
 const store=U.createStore(memory());store.add(note('Old 2'));store.add(note('Old 1'));const removed=store.clear();store.add(note('New'));
 assert.equal(store.restore(removed),2);assert.deepEqual(store.list().map(n=>n.name),['New','Old 1','Old 2']);assert.equal(store.restore(removed),0);
});
test('storage write denial does not discard records or report a successful deletion',()=>{
 const s=memory(),store=U.createStore(s);const n=store.add(note());const before=s.getItem(U.NOTES_KEY);s.setItem=()=>{throw Error('quota exceeded')};
 assert.throws(()=>store.remove(n.id),/quota/);assert.equal(s.getItem(U.NOTES_KEY),before);assert.throws(()=>store.clear(),/quota/);
});
test('corrupted record storage is preserved instead of silently overwritten',()=>{
 const s=memory();s.setItem(U.NOTES_KEY,'{broken');const store=U.createStore(s);
 assert.throws(()=>store.add(note()),/未覆盖/);assert.equal(s.getItem(U.NOTES_KEY),'{broken');
});
test('theme allowlist defaults safely and remembers both supported themes',()=>{
 const s=memory(),store=U.createStore(s);assert.equal(store.getTheme(),'light');store.saveTheme('polymarket');assert.equal(U.createStore(s).getTheme(),'polymarket');
 store.saveTheme('light');assert.equal(store.getTheme(),'light');assert.throws(()=>store.saveTheme('unknown'),/未知/);
 s.setItem(U.THEME_KEY,'<script>');assert.equal(store.getTheme(),'light');
});
test('unsafe or duplicate legacy IDs are normalized before embedding in record controls',()=>{
 const s=memory();s.setItem(U.NOTES_KEY,JSON.stringify([{...note(),id:"x');alert(1)//"},{...note(),id:'same'},{...note(),id:'same'}]));
 const notes=U.createStore(s).list();assert.equal(new Set(notes.map(n=>n.id)).size,3);assert.ok(notes.every(n=>/^[a-zA-Z0-9_-]+$/.test(n.id)));
});
test('default simulation capital is one million and reaches actual backtest accounting',()=>{
 const c=vm.createContext({console});for(const name of ['data.js','engine.js'])vm.runInContext(fs.readFileSync(path.join(root,'dist',name),'utf8'),c);
 const declaration=fs.readFileSync(path.join(root,'dist/views.js'),'utf8').match(/const bt=\{[^;]+;/)[0];vm.runInContext(declaration,c);
 const result=vm.runInContext("RiskEngine.backtest(RiskEngine.demo,'002281',bt)",c);assert.equal(result.capital,1000000);
 assert.ok(Math.abs((result.lastEquity/1000000-1)*100-result.ret)<1e-8);
});
