const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(){
 const writes=[],context=vm.createContext({URL,Intl,console,window:{RISKLENS_LOCAL:true},localStorage:{getItem(){return null},setItem(...args){writes.push(args)}},document:{querySelector(){return null}}});
 for(const name of ['data.js','engine.js','app.js','views.js','local-connector.js','futu-account.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../dist',name),'utf8'),context,{filename:name});
 const run=s=>vm.runInContext(s,context);run("state.page='account';mode='local';accountRender=()=>{};");
 return {context,run,writes};
}
function inject(h){h.run(`futuAccountView.accounts=[{account_id:'A',display_id:'••••A',name:'账户 A',security_firm:'FUTUSECURITIES'}];futuAccountView.accountId='A';futuAccountView.snapshot={account:{account_id:'A'},currency:'HKD',checked_at:'2026-11-23T12:00:00+08:00',sections:{
 funds:{ok:true,checked_at:'2026-11-23T12:00:00+08:00',data:{currency:'HKD',available_funds:null,cash:'1000',power:'5000',total_assets:'2000'}},
 positions:{ok:true,checked_at:'2026-11-23T12:00:00+08:00',data:{items:[{code:'US.AAPL',name:'苹果',qty:'1.5',currency:'USD',cost_price:null},{code:'HK.00700',qty:'2',currency:'HKD'}],skipped:0}},
 'orders:US':{ok:true,checked_at:'2026-11-23T12:00:00+08:00',data:{local_date:'2026-11-22',timezone:'America/New_York',items:[{order_id:'1',symbol:'US.AAPL',side:'BUY',type:'LIMIT',status:'FILLED_PART',qty:'10',filled_qty:'2',remaining_qty:'8',created_at:'2026-11-23T03:30:00+00:00',market:'US'}]}}
 }};`)}
test('account route appears once and page renders connection instructions without demo positions',()=>{
 const h=setup();assert.equal(h.run("NAV.filter(n=>n[0]==='account').length"),1);assert.equal(h.run("parseAppRoute('#account').page"),'account');
 const html=h.run('futuAccountPage()');for(const label of ['账户','可用资金与资产','当前持仓','今日未成交订单','futu auth login'])assert.ok(html.includes(label));assert.doesNotMatch(html,/NaN|undefined|\[object Object\]/);
 assert.equal(h.run('futuAccountView.snapshot'),null);
});
test('available funds, cash and buying power are distinct and missing funds stay blank',()=>{
 const h=setup();inject(h);const html=h.run('futuAccountPage()');assert.match(html,/可用资金<\/small><strong>—/);assert.match(html,/现金余额<\/small><strong>1,000.00/);assert.match(html,/购买力（可能含融资）<\/small><strong>5,000.00/);assert.match(html,/USD/);assert.match(html,/HKD/);assert.match(html,/原币种/);
});
test('pending orders show partial fills and their market-local date',()=>{
 const h=setup();inject(h);const html=h.run('futuAccountPage()');for(const text of ['部分成交','2026-11-22','America/New_York','此前创建的 GTC','未成交数量'])assert.ok(html.includes(text));assert.doesNotMatch(html,/NaN|undefined/);
});
test('order outages do not masquerade as an empty order list',()=>{
 const h=setup();h.run("futuAccountView.snapshot={account:{account_id:'A'},sections:{'orders:US':{ok:false,error:'权限不足'}},unsupported_order_markets:[]}");
 const html=h.run('futuAccountPage()');assert.match(html,/不能确认今日没有未成交订单/);assert.match(html,/权限不足/);assert.doesNotMatch(html,/已查询市场今日暂无未成交订单/);
});
test('partial refresh retains data with original dates only for the same account and currency',()=>{
 const h=setup();inject(h);h.run("const old=futuAccountView.snapshot;const newer={account:{account_id:'A'},currency:'HKD',sections:{funds:{ok:false,error:'403',checked_at:'2026-11-23T13:00:00+08:00'}}};");
 const same=h.run('accountMerge(old,newer)');assert.equal(same.sections.funds.data.cash,'1000');assert.equal(same.sections.funds.data_checked_at,'2026-11-23T12:00:00+08:00');
 assert.equal(h.run("accountMerge(old,{...newer,currency:'USD'}).sections.funds.data"),undefined);
 assert.equal(h.run("accountMerge(old,{...newer,account:{account_id:'B'}}).sections.funds.data"),undefined);
});
test('switching accounts ignores a late response from the prior account',async()=>{
 const h=setup();h.run("let resolvers={};requestLocalPost=(_path,payload)=>new Promise(resolve=>resolvers[payload.account_id]=resolve);futuAccountView.accountId='A';");
 const first=h.run('loadFutuAccount()'),second=h.run("selectFutuAccount('B')");
 h.run("resolvers.B({account:{account_id:'B'},currency:'HKD',sections:{}})");await second;
 h.run("resolvers.A({account:{account_id:'A'},currency:'HKD',sections:{}})");await first;
 assert.equal(h.run('futuAccountView.snapshot.account.account_id'),'B');
});
test('clearing the page invalidates pending requests without writing personal data to storage',async()=>{
 const h=setup();h.run("let release;requestLocalPost=()=>new Promise(resolve=>release=resolve);futuAccountView.accountId='A';");
 const request=h.run('loadFutuAccount()');h.run('clearFutuAccountView()');
 h.run("release({account:{account_id:'A'},currency:'HKD',sections:{}})");await request;
 assert.equal(h.run('futuAccountView.snapshot'),null);assert.equal(h.run('futuAccountView.accounts.length'),0);assert.equal(h.writes.length,0);
});
test('account and provider strings are escaped',()=>{
 const h=setup();inject(h);h.run(`futuAccountView.accounts[0].name='<img src=x onerror=alert(1)>';futuAccountView.snapshot.sections.positions.data.items[0].name='<script>alert(1)</script>';`);
 const html=h.run('futuAccountPage()');assert.match(html,/&lt;img/);assert.match(html,/&lt;script/);assert.doesNotMatch(html,/<img src=x|<script>alert/);
});
test('account module loads before bootstrap and is included in the page routing map',()=>{
 const index=fs.readFileSync(path.join(__dirname,'../dist/index.html'),'utf8'),bootstrap=fs.readFileSync(path.join(__dirname,'../dist/bootstrap.js'),'utf8');
 assert.ok(index.indexOf('src="futu-account.js"')<index.indexOf('src="bootstrap.js"'));assert.match(bootstrap,/account:futuAccountPage/);
});

test('unclassified orders and invalid positions never render a misleading empty-account claim',()=>{
 const h=setup();inject(h);h.run("futuAccountView.snapshot.sections['orders:US'].data={items:[],unclassified:1};futuAccountView.snapshot.sections.positions.data={items:[],skipped:1}");
 const html=h.run('futuAccountPage()');assert.match(html,/不能确认今日没有未成交订单/);assert.match(html,/不能确认没有持仓/);assert.doesNotMatch(html,/已查询市场今日暂无未成交订单|富途本次成功返回空持仓列表/);
});
