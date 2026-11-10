let localSnapshot=null,localStatus=null,localPolling=null,localRefreshError='',localRefreshPending=false;
const MARKET_KEYS={
 ifind:{name:'同花顺 iFinD',path:'/api/ifind/test',url:'https://mcp.51ifind.com/',
  help:'A 股与场内 ETF 的备用报价。分别测试两个市场的样例报价。'},
 massive:{name:'Massive',path:'/api/market-keys/massive/test',url:'https://massive.com/dashboard',
  help:'用于美股和 ETF 的搜索与日线，以及 SOX 指数和 BTC/USD 日线。分别测试三类数据的访问权限。'},
 pingan:{name:'平安证券',path:'/api/market-keys/pingan/test',url:'https://stock.pingan.com/huodong/aiskill/skillPage/index.html#/',
  help:'管理平安证券行情查询的 API Key。测试会请求 A 股样例报价。'}
};
const marketKeyState=Object.fromEntries(Object.keys(MARKET_KEYS).map(id=>[id,{testing:false,result:null,error:''}]));
const hasLocalServer=window.RISKLENS_LOCAL===true;
async function requestLocalPost(path,payload){
 const send=()=>fetch(path,{method:'POST',mode:'same-origin',
  headers:{'X-RiskLens-Token':window.RISKLENS_TOKEN,...(payload?{'Content-Type':'application/json'}:{})},
  body:payload?JSON.stringify(payload):undefined});
 let response=await send(),body=await response.json().catch(()=>null);
 if(response.status===403&&body?.code==='stale_session'){
  const session=await fetch('/api/session',{mode:'same-origin',cache:'no-store'});
  const fresh=session.ok?await session.json().catch(()=>null):null;
  if(!fresh?.token)throw Error('无法恢复页面会话（HTTP '+session.status+'），请重新打开本地页面');
  window.RISKLENS_TOKEN=fresh.token;
  response=await send();body=await response.json().catch(()=>null);
 }
 if(!response.ok||!body)throw Error(body?.error||'本地服务响应异常（HTTP '+response.status+'）');
 return body;
}
async function refreshRequest(){
 const body=await requestLocalPost('/api/refresh');
 if(typeof body.started!=='boolean')throw Error('本地服务未返回刷新状态');
 return body.started;
}
function marketKeyStatus(id){return localStatus?.market_keys?.[id]||(id==='ifind'?localStatus?.ifind:null)}
function marketKeySection(id,provider){
 const status=marketKeyStatus(id),{testing,result,error}=marketKeyState[id];
 const available=status?.available===true,configured=result?.configured??status?.configured;
 const label=testing?'正在测试…':!available?(hasLocalServer&&!localStatus?'正在检查本地连接':'请通过本机地址打开'):configured?'已配置':'未配置';
 const summary={ok:'行情测试通过',partial:'部分行情测试通过',failed:'行情测试未通过'}[result?.result];
 const report=result?`<div class="notice" role="status"><strong>${esc(summary)}${result.saved?' · 密钥已保存':''}</strong>
  ${result.save_error?`<p>${esc(result.save_error)}</p>`:''}
  <ul>${result.checks.map(check=>`<li>${esc(check.market)} ${esc(check.symbol)} · ${check.ok?
   [num(check.price,3),check.timestamp,check.detail].filter(Boolean).map(esc).join(' · '):esc(check.error)}</li>`).join('')}</ul></div>`:'';
 return `<section class="market-key-section" aria-label="${esc(provider.name)}">
  <div class="definition-row"><span>${esc(provider.name)}</span><strong>${label}</strong></div>
  <p class="small muted">${esc(provider.help)}</p>
  <div class="actions">
   <button type="button" class="btn primary" onclick="openMarketKey('${id}')" ${available&&!testing?'':'disabled'}>${configured?'更换密钥':'输入密钥'}</button>
   <button type="button" class="btn" onclick="runMarketKeyTest('${id}')" ${available&&configured&&!testing?'':'disabled'}>测试已配置密钥</button>
   <a href="${esc(provider.url)}" target="_blank" rel="noopener noreferrer">申请 / 管理密钥 ↗</a>
  </div>
  ${testing?`<p class="small muted" role="status">正在请求 ${esc(provider.name)} 样例行情，请稍候。</p>`:''}
  ${error?`<div class="notice" role="alert">${esc(error)}</div>`:report}
  ${available&&!configured&&status.message&&!result&&!error?`<p class="small muted">${esc(status.message)}</p>`:''}
 </section>`;
}
function marketKeyPanel(){
 return panel('行情密钥专区',`<div class="panel-body market-key-panel">
  <h3>密钥管理</h3>
  <p class="small muted">各行情源独立配置，至少一项样例通过时保存新密钥。密钥保存在运行服务的电脑上。</p>
  ${Object.entries(MARKET_KEYS).map(([id,provider])=>marketKeySection(id,provider)).join('')}
 </div>`,'','panel-space');
}
function openMarketKey(id){
 const provider=MARKET_KEYS[id];
 if(marketKeyStatus(id)?.available!==true||marketKeyState[id].testing)return;
 modal('输入 '+provider.name+' 行情密钥',`<form class="market-key-form" method="post" onsubmit="confirmMarketKey(event,'${id}')" autocomplete="off">
  <label for="market-key-input">${esc(provider.name)} API Key</label>
  <input id="market-key-input" type="password" required maxlength="2048" autocomplete="off" spellcheck="false" autofocus>
  <p class="small muted">更换失败时继续使用之前的设置。</p>
  <div class="actions"><button type="submit" class="btn primary">确认并测试</button></div>
 </form>`);
}
function confirmMarketKey(event,id){
 event.preventDefault();
 const input=event.currentTarget.querySelector('input');
 const token=input.value.trim();
 if(!token){input.focus();return;}
 input.value='';
 document.getElementById('dialog').close();
 runMarketKeyTest(id,{token});
}
async function runMarketKeyTest(id,payload={}){
 const current=marketKeyState[id];
 if(marketKeyStatus(id)?.available!==true||current.testing)return;
 current.testing=true;current.error='';current.result=null;
 render();
 try{
  const result=await requestLocalPost(MARKET_KEYS[id].path,payload);
  if(!Array.isArray(result.checks))throw Error('本地服务未返回测试明细');
  current.result=result;
  const status=marketKeyStatus(id);
  if(status)status.configured=result.configured;
 }catch(error){current.error=error.message}
 finally{current.testing=false;render()}
}
function localRefreshDetails(status){
 const sourceErrors=Object.entries(status?.quote_sources||{}).flatMap(([stage,sources])=>Object.entries(sources).map(([source,error])=>[stage+' · '+source,error]));
 const errors=[...sourceErrors,...Object.entries(status?.errors||{}).filter(([key])=>
  !(key==='A股初始报价'&&status.quote_sources?.['初始报价']&&Object.keys(status.quote_sources['初始报价']).length)
  &&!(key==='A股二次报价'&&status.quote_sources?.['二次报价']&&Object.keys(status.quote_sources['二次报价']).length))];
 if(!localRefreshError&&!errors.length)return '';
 return `<div class="notice" style="margin-top:14px"><strong>行情刷新详情</strong>${localRefreshError?`<p>${esc(localRefreshError)}</p>`:''}${errors.length?`<ul>${errors.map(([source,error])=>`<li><strong>${esc(source)}</strong> · ${esc(error)}</li>`).join('')}</ul>`:''}</div>`;
}
function localPanel(){if(!hasLocalServer)return '';const s=localStatus;return panel('本地数据连接',`<div class="panel-body"><div class="definition-row"><span>连接状态</span><strong>${s?.busy?(s.stage||'刷新中')+' · '+s.done+' / '+s.total:s?.price_result==='failed'?'刷新失败，保留可用快照':s?.price_result==='partial'?'行情部分更新':s?.price_result==='ok'?'行情刷新完成':s?.errors?.connection?'本地服务连接失败':s?'本地服务已连接':'正在连接'}</strong></div><div class="definition-row"><span>行情样本覆盖</span><strong>${s?.coverage??0} / ${s?.total??CATALOG.filter(a=>a.watch||a.benchmark).length} · ${esc(s?.as_of||'待获取')}</strong></div><div class="definition-row"><span>最近刷新尝试（北京时间）</span><strong>${esc((s?.busy?s.started_at:s?.last_refresh)||'—')}</strong></div><div class="definition-row"><span>本地缓存</span><strong>${esc(s?.cache_path||(s?.cache_persisted===false?'仅保留在内存':'待获取'))}</strong></div>${s?.cache_warning?`<div class="notice" style="margin-top:12px">${esc(s.cache_warning)}</div>`:''}<p class="small muted" style="margin:13px 0">先重新获取历史日线并复核前一交易日，再用腾讯、新浪或同花顺 iFinD 的有效报价更新当天K线。iFinD 需配置本地密钥。盘中K线及信号标为未确认，回测成交只使用收盘确认样本。未复权日线尚未处理分红/拆股，公司行动可能影响信号与收益。</p><div class="actions">${button('刷新真实行情',"refreshLocalData()",true,'refresh')}${button('下载当前快照',"downloadLocalSnapshot()",false,'download')}</div>${localRefreshDetails(s)}<p class="small muted" style="margin-top:14px">已接入：日K、累计报价量、财务披露、历史资金流、公告、ETF持仓/净值及外围代理行情。各字段以实际来源和日期为准；一致预期与独立舆情样本尚未接入。</p></div>`,'','panel-space')}
async function connectLocal(){if(!hasLocalServer)return;try{const [sr,dr]=await Promise.all([fetch('/api/status',{cache:'no-store'}),fetch('/api/snapshot',{cache:'no-store'})]);if(!sr.ok||!dr.ok)throw Error('本地服务响应异常');const status=await sr.json(),raw=await dr.json(),previousStatus=localStatus,changed=raw.fetched_at!==localSnapshot?.fetched_at||raw.data_revision!==localSnapshot?.data_revision||raw.watchlist_revision!==localSnapshot?.watchlist_revision||!localSnapshot;const clean=E.validateSnapshot(raw);if(clean.catalog)syncWatchCatalog(clean.catalog);for(const [code,r] of Object.entries(clean.instruments)){const source=raw.instruments[code];for(const key of ['fetched_at','volume_unit','latest_quote','provider_url','skipped_rows','closed_as_of','previous_close_check','history_checked_at','financial_history','announcements','etf','nav','flows','etf_flow','research_errors','research_fetched_at','market','currency','timezone','international_method'])r[key]=source[key];}for(const key of ['fetched_at','closed_as_of','previous_session','external','sectors','geopolitics','catalog','watchlist_revision','data_revision'])clean[key]=raw[key];clean.data_kind='local';localSnapshot=clean;localStatus=status;if(clean.as_of&&Object.keys(clean.instruments).length&&!bt.rangeTouched){bt.end=clean.as_of;if(bt.start>bt.end)bt.start=Object.values(clean.instruments)[0].bars[0]?.date||bt.end;}if(changed||state.page==='data')render();else{renderShell();const ribbon=document.querySelector('.data-ribbon');if(ribbon)ribbon.outerHTML=banner();}if(status.price_result==='failed'&&!status.busy&&(previousStatus?.busy||localRefreshPending)&&previousStatus?.last_refresh!==status.last_refresh)toast('行情刷新失败，已保留可用快照。请查看连接详情');if(status.busy||Object.values(status.asset_jobs||{}).some(j=>j.status==='loading')||status.data_revision!==raw.data_revision){clearTimeout(localPolling);localPolling=setTimeout(connectLocal,2200)}}catch(e){localStatus={busy:false,coverage:0,errors:{connection:e.message}};render();toast('本地数据连接失败：'+e.message)}}
async function refreshLocalData(){
 if(!hasLocalServer||localRefreshPending)return;
 localRefreshPending=true;localRefreshError='';
 try{
  const started=await refreshRequest();
  toast(started?'正在抓取真实行情，完成后自动更新':'行情已在刷新，完成后自动更新');
  await connectLocal();
 }catch(e){
  localRefreshError='刷新请求未提交：'+e.message;
  render();
  toast(localRefreshError);
 }finally{localRefreshPending=false}
}
function downloadLocalSnapshot(){if(!localSnapshot)return toast('尚无本地快照');const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(localSnapshot,null,2)],{type:'application/json'}));a.href=url;a.download='RiskLens_Market_Snapshot_'+(localSnapshot.as_of||'pending')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function liveQuoteLine(code){if(mode!=='local')return '';const q=obs(code)?.latest_quote;return q?`<div class="small muted" style="margin-top:7px">${q.quote_only?'独立报价 · K线未更新':obs(code)?.completed===false?'盘中更新':'最新报价'} ${num(q.price,q.price<10?3:2)} · ${esc(q.timestamp?.replace('T',' ').replace('+08:00',''))} ${byCode(code)?.market&&byCode(code).market!=='CN'?esc(obs(code)?.timezone||'交易所时区'):'北京时间'}${q.source?' · '+esc(q.source):''}</div>`:''}

function verificationPanel(){if(mode!=='local')return '';const names={verified:'已复核',repaired:'已修正',missing:'缺失'};return panel('前一交易日收盘复核',`<div class="table-wrap"><table><thead><tr><th>标的</th><th>复核交易日</th><th>结果</th><th>旧 / 新收盘</th><th>历史重取时间</th></tr></thead><tbody>${CATALOG.map(a=>{const check=obs(a.code)?.previous_close_check;return `<tr><td>${a.name}</td><td>${check?.date||'—'}</td><td>${pill(names[check?.status]||'未执行',check?.status==='missing'?'warn':'good')}</td><td class="num">${num(check?.old_close,3)} / ${num(check?.new_close,3)}</td><td>${esc(check?.checked_at?.replace('T',' ')||'—')}</td></tr>`}).join('')}</tbody></table></div><div class="panel-note">先重新获取历史K线并覆盖核对前一交易日，再读取最新报价更新当天OHLC。若前日记录缺失，不自动宣称收盘已确认。</div>`,'','panel-space')}
