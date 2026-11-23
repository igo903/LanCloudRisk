/* Private account data is kept only in this page's memory, outside research snapshots/storage. */
const futuAccountView={accounts:[],accountId:'',currency:'HKD',snapshot:null,loading:false,error:'',errorCode:'',sequence:0};
const FUTU_ACCOUNT_STATUS={WAITING_SUBMIT:'等待提交',SUBMITTING:'提交中',SUBMITTED:'已提交未成交',FILLED_PART:'部分成交',CANCELLING_PART:'部分撤单处理中',CANCELLING_ALL:'撤单处理中'};
function accountRender(){if(state.page==='account')render()}
function accountValue(value,digits=2){if(value===null||value===undefined||value==='')return '—';const n=Number(value);return Number.isFinite(n)?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:digits,minimumFractionDigits:digits===2?2:0}).format(n):'—'}
function accountReadTime(section){return esc((section?.data_checked_at||section?.checked_at||'未读取').replace('T',' ').slice(0,19))}
function accountSectionNote(section){return `<p class="account-section-note ${section?.ok===false?'account-warning':''}">${section?.ok===false?esc(section.error||'读取失败')+'。':''}${section?.retained?'保留此前数据；':''}读取时间 ${accountReadTime(section)}${section?.checked_at?' 北京时间':''}</p>`}
function accountMerge(previous,current){
 if(!previous||previous.account?.account_id!==current.account?.account_id||previous.currency!==current.currency)return current;
 const sections={...current.sections};
 for(const [key,value] of Object.entries(sections))if(value.ok===false&&previous.sections?.[key]?.data){
  const old=previous.sections[key];sections[key]={...value,data:old.data,retained:true,data_checked_at:old.data_checked_at||old.checked_at};
 }
 return {...current,sections};
}
async function loadFutuAccounts(){
 const sequence=++futuAccountView.sequence;
 Object.assign(futuAccountView,{accounts:[],accountId:'',snapshot:null,loading:true,error:'',errorCode:''});accountRender();
 try{
  const result=await requestLocalPost('/api/account/read',{action:'list'});
  if(sequence!==futuAccountView.sequence)return;
  if(!Array.isArray(result.accounts))throw Error('账户服务未返回有效账户清单');
  futuAccountView.accounts=result.accounts;
  if(result.accounts.length===1){futuAccountView.accountId=result.accounts[0].account_id;futuAccountView.loading=false;await loadFutuAccount();return}
 }catch(e){if(sequence===futuAccountView.sequence)Object.assign(futuAccountView,{error:e.message,errorCode:e.code||''})}
 finally{if(sequence===futuAccountView.sequence){futuAccountView.loading=false;accountRender()}}
}
async function selectFutuAccount(value){
 ++futuAccountView.sequence;
 Object.assign(futuAccountView,{accountId:value,snapshot:null,error:'',errorCode:'',loading:false});
 if(value)await loadFutuAccount();else accountRender();
}
async function setFutuAccountCurrency(value){
 ++futuAccountView.sequence;Object.assign(futuAccountView,{currency:value,snapshot:null,error:'',errorCode:'',loading:false});
 if(futuAccountView.accountId)await loadFutuAccount();else accountRender();
}
async function loadFutuAccount(){
 if(!futuAccountView.accountId)return;
 const sequence=++futuAccountView.sequence,accountId=futuAccountView.accountId,currency=futuAccountView.currency;
 const previous=futuAccountView.snapshot;
 Object.assign(futuAccountView,{loading:true,error:'',errorCode:''});accountRender();
 try{
  const value=await requestLocalPost('/api/account/read',{action:'snapshot',account_id:accountId,currency});
  if(sequence!==futuAccountView.sequence)return;
  if(value.account?.account_id!==accountId||value.currency!==currency||!value.sections)throw Error('账户响应与当前选择不一致');
  futuAccountView.snapshot=accountMerge(previous,value);
 }catch(e){if(sequence===futuAccountView.sequence)Object.assign(futuAccountView,{error:e.message,errorCode:e.code||''})}
 finally{if(sequence===futuAccountView.sequence){futuAccountView.loading=false;accountRender()}}
}
function clearFutuAccountView(){
 ++futuAccountView.sequence;Object.assign(futuAccountView,{accounts:[],accountId:'',snapshot:null,loading:false,error:'',errorCode:''});accountRender();
}
function futuAccountSetup(){return `<section class="panel panel-space"><div class="panel-head"><h2>首次连接富途账户</h2>${pill('账户 OAuth 授权')}</div><div class="panel-body account-setup"><p><a class="btn" href="/install_futu_cli.py" download="install_futu_cli.py">下载 Futu 安装与诊断工具</a></p><p class="small muted">下载后在运行服务的电脑执行 <code>python3 "$HOME/Downloads/install_futu_cli.py" --login</code>。工具使用 Python HTTP/1.1 下载官方签名程序，显示具体失败阶段。</p><ol><li>安装工具先检查本机 Futu CLI；已正常安装时直接检查登录。</li><li>在该电脑终端运行 <code>futu auth login</code>，按富途授权页提示完成账户访问授权。</li><li>返回本页点击“连接 / 重新读取账户”，多账户时选择需要查看的账户。</li></ol><p class="small muted">行情密钥专区中的 AppKey 用于行情查询；账户读取使用 CLI 独立授权。每台新电脑需完成一次相应授权。</p><a href="https://github.com/FutunnOpen/futu-cli" target="_blank" rel="noopener noreferrer">官方安装与授权说明 ↗</a></div></section>`}
function futuFundsPanel(section){
 const data=section?.data;
 return panel('可用资金与资产',data?`<div class="account-funds-grid">${[
  ['可用资金','available_funds','以富途 available_funds 为准'],['现金余额','cash','现金余额不等于可用资金'],
  ['冻结现金','frozen_cash','订单或其他事项冻结金额'],['购买力（可能含融资）','power','不作为现金余额或可提现金额'],
  ['可提取金额','max_withdrawal','以富途可提取口径为准'],['总资产','total_assets','供应商按所选展示币种汇总']
 ].map(([name,key,hint])=>`<article><small>${name}</small><strong>${accountValue(data[key])}<em>${esc(data.currency)}</em></strong><p>${hint}</p></article>`).join('')}</div>${accountSectionNote(section)}`:
 `<div class="empty">${esc(section?.error||'连接账户后读取可用资金')}</div>${section?accountSectionNote(section):''}`);
}
function futuPositionsPanel(section){
 const data=section?.data,rows=Array.isArray(data?.items)?data.items:[];
 return panel('当前持仓',!data?`<div class="empty">${esc(section?.error||'连接账户后读取当前持仓')}</div>${section?accountSectionNote(section):''}`:
 `<div class="table-wrap account-position-table"><table><thead><tr><th>标的</th><th>方向 / 币种</th><th>持仓数量</th><th>可卖数量</th><th>成本价</th><th>参考价</th><th>持仓市值</th><th>持仓盈亏</th><th>盈亏率</th><th>当日盈亏</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.name||row.code)}</strong><div class="table-sub">${esc(row.code)}</div></td><td>${esc(({LONG:'多头',SHORT:'空头'})[row.side]||row.side||'未返回')}<div class="table-sub">${esc(row.currency)}</div></td><td class="num">${accountValue(row.qty,8)}</td><td class="num">${accountValue(row.can_sell_qty,8)}</td><td class="num">${accountValue(row.cost_price,4)}</td><td class="num">${accountValue(row.nominal_price,4)}</td><td class="num">${accountValue(row.market_val)}</td><td class="num">${accountValue(row.pl_val)}</td><td class="num">${row.pl_ratio==null?'—':accountValue(row.pl_ratio)+'%'}</td><td class="num">${accountValue(row.today_pl_val)}</td></tr>`).join('')||`<tr><td colspan="10" class="empty">${data.skipped?'持仓校验不完整，不能确认没有持仓。':'富途本次成功返回空持仓列表。'}</td></tr>`}</tbody></table></div>
 ${data.skipped?`<p class="account-section-note account-warning">另有 ${esc(data.skipped)} 条缺少可核验代码或数量，未展示。</p>`:''}${accountSectionNote(section)}<div class="panel-note">持仓保留标的原币种，不将跨币种市值或盈亏相加；成本价与盈亏字段按供应商有效性标记展示。读取时间不等于各证券的最新成交时间。</div>`);
}
function futuOrdersPanel(snapshot){
 const sections=Object.entries(snapshot?.sections||{}).filter(([key])=>key.startsWith('orders:'));
 const orders=sections.flatMap(([,value])=>Array.isArray(value.data?.items)?value.data.items.map(row=>({...row,retained:value.retained})):[]);
 const unclassified=sections.some(([,value])=>value.data?.unclassified>0),failed=sections.some(([,value])=>!value.ok),hasData=sections.some(([,value])=>value.data);
 const missing=snapshot?.order_markets_missing,unsupported=snapshot?.unsupported_order_markets||[];
 return panel('今日未成交订单',`<div class="panel-body account-order-summary"><p>按各市场交易所当地日期筛选<strong>今日新建</strong>、仍在途或部分成交的订单。已全部成交、已撤销和失败订单不计入；此前创建的 GTC 订单不在本列表。</p><div class="account-market-status">${sections.map(([key,value])=>`<div><strong>${esc(key.slice(7))}</strong> · ${esc(value.data?.local_date||'日期未取得')} · ${value.ok?'读取成功':value.retained?'本轮失败，保留此前记录':'读取失败'}${value.data?.timezone?' · '+esc(value.data.timezone):''}<small>${value.error?esc(value.error)+' · ':''}读取 ${accountReadTime(value)}${value.data?.unclassified?'；另有 '+esc(value.data.unclassified)+' 条状态、时间或数量无法分类':''}</small></div>`).join('')||'尚未取得授权市场订单'}
 ${missing?'<p class="account-warning">富途未返回该账户的可交易市场，订单覆盖无法确认。</p>':''}${unsupported.length?`<p class="account-warning">尚未接入订单读取的授权市场编号：${unsupported.map(esc).join('、')}。</p>`:''}</div></div>
 <div class="table-wrap account-order-table"><table><thead><tr><th>标的 / 订单号</th><th>市场</th><th>方向 / 类型</th><th>状态</th><th>委托价</th><th>委托数量</th><th>已成交</th><th>未成交数量</th><th>创建时间（北京时间）</th></tr></thead><tbody>${orders.map(row=>`<tr><td><strong>${esc(row.name||row.symbol)}</strong><div class="table-sub">${esc(row.symbol)} · ${esc(row.order_id)}</div></td><td>${esc(row.market)}</td><td>${esc(({BUY:'买入',SELL:'卖出',SELL_SHORT:'卖空',BUY_BACK:'买回'})[row.side]||row.side)}<div class="table-sub">${esc(row.type)}</div></td><td>${esc(FUTU_ACCOUNT_STATUS[row.status]||row.status)}${row.retained?'<div class="table-sub">此前读取记录</div>':''}</td><td class="num">${accountValue(row.price,4)}</td><td class="num">${accountValue(row.qty,8)}</td><td class="num">${accountValue(row.filled_qty,8)}</td><td class="num">${accountValue(row.remaining_qty,8)}</td><td>${esc(new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'medium'}).format(new Date(row.created_at)))}</td></tr>`).join('')||`<tr><td colspan="9" class="empty">${!snapshot?'连接账户后读取今日订单':failed||missing||unsupported.length||unclassified?'订单读取不完整，不能确认今日没有未成交订单':hasData?'已查询市场今日暂无未成交订单':'未取得可核验订单数据'}</td></tr>`}</tbody></table></div>
 <div class="panel-note">部分成交只展示尚未完成的订单，剩余数量在委托与成交数量均返回时计算。CLI 可能省略数值为 0 的字段；未返回数量显示 —，不擅自补为 0。市场单未返回委托价时同样显示 —。</div>`);
}
function futuAccountPage(){
 const view=futuAccountView,snapshot=view.snapshot,local=typeof hasLocalServer!=='undefined'&&hasLocalServer;
 return '<div class="futu-account-page">'+title('账户','查看富途授权账户的资金、持仓和今日未成交订单。',`<button class="btn" onclick="clearFutuAccountView()">清除本页数据</button><button class="btn primary" onclick="loadFutuAccounts()" ${!local||view.loading?'disabled':''}>${view.loading?'正在读取…':'连接 / 重新读取账户'}</button>`)+
 (!local?notice('账户读取需要运行本地启动程序。此页不会使用研究演示数据作为真实账户数据。'):'')+
 (view.error?`<div class="notice account-warning" role="alert">${esc(view.error)}${snapshot?'；保留同一账户此前读取的数据，日期见各表。':''}</div>`:'')+
 `<section class="panel panel-space"><div class="panel-body account-controls"><label>富途账户<select aria-label="富途账户" onchange="selectFutuAccount(this.value)" ${view.loading?'disabled':''}><option value="">${view.accounts.length?'请选择账户':'尚未读取账户'}</option>${view.accounts.map(account=>`<option value="${esc(account.account_id)}" ${view.accountId===account.account_id?'selected':''}>${esc([account.display_id,account.name,account.type,account.security_firm].filter(Boolean).join(' · '))}</option>`).join('')}</select></label><label>资金展示币种<select aria-label="资金展示币种" onchange="setFutuAccountCurrency(this.value)" ${view.loading?'disabled':''}>${['HKD','USD','CNY','SGD','JPY','AUD','CAD','MYR','KRW'].map(code=>`<option ${view.currency===code?'selected':''}>${code}</option>`).join('')}</select></label><button class="btn primary" onclick="loadFutuAccount()" ${!view.accountId||view.loading?'disabled':''}>刷新账户数据</button><span class="small muted" role="status">${view.loading?'正在读取账户，请稍候':snapshot?'最近读取 '+esc(snapshot.checked_at?.replace('T',' ').slice(0,19))+' 北京时间':view.accounts.length>1?'已读取多个账户，请选择要查看的账户':'账户数据按需读取'}</span></div></section>`+
 (!view.accounts.length?futuAccountSetup():'')+futuFundsPanel(snapshot?.sections?.funds)+futuPositionsPanel(snapshot?.sections?.positions)+futuOrdersPanel(snapshot)+
 '<p class="small muted account-privacy">本页仅查询账户；资金、持仓和订单保留在当前浏览器内存，不写入行情缓存、导出快照或 Git 文件。清除本页数据或关闭页面后需重新读取。</p></div>';
}
