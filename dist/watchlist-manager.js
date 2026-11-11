'use strict';
const WATCH_MARKET_NAMES={CN:'A股',HK:'港股',US:'美股'};
const originalCatalog=new Map(CATALOG.map(a=>[a.code,{...a,market:a.market||'CN',currency:a.currency||'CNY',symbol:a.symbol||a.code}]));
let watchSearch={sequence:0,query:'',market:'ALL',results:[],loading:false,error:'',timer:null,controller:null,busy:false};
function syncWatchCatalog(items){
 const normalized=E.validateCatalog(items),previous=new Map(CATALOG.map(a=>[a.code,a]));
 const next=normalized.map(m=>{const old=originalCatalog.get(m.code)||previous.get(m.code)||{};return {...old,...m,industry:old.industry||WATCH_MARKET_NAMES[m.market]+(m.type==='etf'?'ETF':'股票'),topic:old.topic||'跨市场证券研究',questions:old.questions||['价格趋势与本地市场是否同步？','财报的报告期、披露时间与币种是否一致？','交易单位、费用、流动性与公司行动有哪些限制？']}});
 CATALOG.splice(0,CATALOG.length,...next);WATCH_CODES.splice(0,WATCH_CODES.length,...next.filter(a=>a.watch).map(a=>a.code));
 if(!CATALOG.some(a=>a.code===state.code&&(a.watch||a.benchmark))){state.code=WATCH_CODES[0]||BENCH_CODES[0];state.dimension=0;bt.lot=(byCode(state.code)?.market||'CN')==='CN'?100:1;}
}
function watchRevision(){return localSnapshot?.watchlist_revision??0}
function watchJob(code){return localStatus?.asset_jobs?.[code]||{}}
function watchJobText(code){const job=watchJob(code);return job.status==='loading'?`<div class="table-sub watch-job loading" role="status">${esc(job.stage||'更新中')}</div>`:job.status==='error'?`<div class="table-sub watch-job error" title="${esc(job.error)}">更新失败${series(code).length?' · 保留旧快照':''}</div>`:!series(code).length?'<div class="table-sub muted">等待行情</div>':''}
function watchManagementCell(a){const enabled=hasLocalServer&&mode==='local';return `<td class="watch-manage"><button class="text-button" aria-label="刷新${esc(a.name)}数据" onclick="refreshWatchAsset('${a.code}')" ${!enabled||watchJob(a.code).status==='loading'?'disabled':''}>刷新</button><button class="text-button danger" aria-label="删除${esc(a.name)}自选" onclick="requestRemoveWatch('${a.code}')" ${!enabled?'disabled':''}>删除</button></td>`}
async function watchRequest(path,payload){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-RiskLens-Token':window.RISKLENS_TOKEN},body:JSON.stringify(payload)});const value=await r.json();if(!r.ok){if(r.status===409)await connectLocal();throw Error(value.error||'操作失败')}return value}
function openWatchSearch(){
 if(!hasLocalServer||mode!=='local')return toast('请在本地真实行情模式管理自选');
 clearTimeout(watchSearch.timer);watchSearch.controller?.abort();watchSearch={...watchSearch,query:'',results:[],error:'',loading:false,sequence:watchSearch.sequence+1};
 const dialog=$('#dialog');dialog.dataset.kind='watch-search';dialog.setAttribute('aria-label','添加自选股');dialog.innerHTML=`<div class="dialog-head watch-dialog-heading"><div><h2>添加自选股</h2><p>A股、港股与美股按独立证券代码管理</p></div><button class="icon-button" aria-label="关闭添加自选" onclick="document.getElementById('dialog').close()">${icon('close')}</button></div><div class="dialog-body watch-dialog-body"><div class="watch-search-form"><label>搜索市场<select id="watch-search-market" aria-label="搜索市场" onchange="queueWatchSearch()"><option value="ALL">全部市场</option><option value="CN">A股</option><option value="HK">港股</option><option value="US">美股</option></select></label><label>名称或代码<input id="watch-search-query" aria-label="搜索证券名称或代码" placeholder="光迅 / 00700 / NVDA" maxlength="50" autocomplete="off" oninput="queueWatchSearch()"></label></div><div id="watch-search-results" class="watch-search-results" aria-live="polite"><p class="muted">输入名称、拼音或证券代码搜索。</p></div><p class="watch-dialog-note">添加后自动获取日线和可用研究数据，并联动风险、七维分析与回测。海外回测采用交易币种；缺失的财务、资金与公告不会补造。</p></div>`;
 dialog.showModal();$('#watch-search-query').focus();
}
function queueWatchSearch(){clearTimeout(watchSearch.timer);watchSearch.controller?.abort();const query=$('#watch-search-query')?.value.trim()||'',market=$('#watch-search-market')?.value||'ALL';watchSearch.query=query;watchSearch.market=market;watchSearch.sequence++;if(!query){watchSearch.results=[];watchSearch.error='';watchSearch.loading=false;renderWatchSearch();return}watchSearch.loading=true;watchSearch.error='';renderWatchSearch();watchSearch.timer=setTimeout(searchWatchAssets,300)}
async function searchWatchAssets(){const sequence=watchSearch.sequence,controller=new AbortController();watchSearch.controller=controller;try{const r=await fetch('/api/search?'+new URLSearchParams({q:watchSearch.query,market:watchSearch.market}),{signal:controller.signal,cache:'no-store'}),value=await r.json();if(!r.ok)throw Error(value.error||'证券搜索失败');if(sequence!==watchSearch.sequence)return;watchSearch.results=E.validateCatalog((value.results||[]).map(x=>({...x,watch:false})));watchSearch.loading=false;watchSearch.error=''}catch(e){if(e.name==='AbortError'||sequence!==watchSearch.sequence)return;watchSearch.loading=false;watchSearch.error=e.message}renderWatchSearch()}
function renderWatchSearch(){const root=$('#watch-search-results');if(!root||!$('#dialog').open||$('#dialog').dataset.kind!=='watch-search')return;if(watchSearch.loading){root.innerHTML='<p class="muted" role="status">正在搜索证券…</p>';return}if(watchSearch.error){root.innerHTML=`<p class="watch-search-error" role="alert">${esc(watchSearch.error)}</p>`;return}root.innerHTML=watchSearch.results.map(a=>`<article class="watch-search-result"><div><strong>${esc(a.name)}</strong><small>${WATCH_MARKET_NAMES[a.market]} · ${esc(a.symbol)} · ${a.type==='etf'?'ETF':'股票'} · ${esc(a.currency)}</small></div><button class="btn small ${WATCH_CODES.includes(a.code)?'':'primary'}" onclick="addWatchCandidate('${a.code}')" ${WATCH_CODES.includes(a.code)||watchSearch.busy?'disabled':''}>${WATCH_CODES.includes(a.code)?'已在自选':'添加'}</button></article>`).join('')||`<p class="muted">${watchSearch.query?'搜索源未返回匹配的股票/ETF，可尝试准确代码或切换市场。':'输入名称、拼音或证券代码搜索。'}</p>`}
async function addWatchCandidate(code){const a=watchSearch.results.find(x=>x.code===code);if(!a||watchSearch.busy)return;watchSearch.busy=true;renderWatchSearch();try{await watchRequest('/api/watchlist/add',{market:a.market,symbol:a.symbol,expected_revision:watchRevision()});await connectLocal();toast('已添加'+a.name+'，正在更新行情及研究数据')}catch(e){toast('添加失败：'+e.message)}finally{watchSearch.busy=false;renderWatchSearch()}}
async function refreshWatchAsset(code){if(!hasLocalServer||mode!=='local')return toast('请在本地行情模式刷新');try{await watchRequest('/api/watchlist/refresh',{code});await connectLocal();toast('正在重新获取该标的数据')}catch(e){toast('刷新失败：'+e.message)}}
function requestRemoveWatch(code){const a=byCode(code);if(!a?.watch)return;const revision=watchRevision(),dialog=$('#dialog');dialog.dataset.kind='watch-remove';dialog.setAttribute('aria-label','删除自选股');dialog.innerHTML=`<div class="dialog-head"><h2>删除这只自选股？</h2><button class="icon-button" aria-label="关闭删除自选" onclick="document.getElementById('dialog').close()">${icon('close')}</button></div><div class="dialog-body watch-dialog-body"><div class="deletion-detail"><strong>${esc(a.name)}</strong><p>${WATCH_MARKET_NAMES[a.market||'CN']} · ${esc(a.symbol||a.code)}</p></div><p class="small muted">仅移出自选，已保存的研究记录保留。${a.benchmark?'该ETF仍在六ETF观察池中，继续更新市场数据。':'之后可通过搜索重新添加。'}</p><div class="dialog-actions"><button class="btn" onclick="document.getElementById('dialog').close()">取消</button><button id="confirm-watch-remove" class="btn danger-solid" onclick="confirmRemoveWatch('${code}',${revision})">删除自选</button></div></div>`;dialog.showModal()}
async function confirmRemoveWatch(code,revision){const control=$('#confirm-watch-remove');if(control)control.disabled=true;try{await watchRequest('/api/watchlist/remove',{code,expected_revision:revision});$('#dialog').close();await connectLocal();toast('已从自选移除，可通过搜索重新添加')}catch(e){if(control)control.disabled=false;toast('删除失败：'+e.message)}}
function watchMarketOf(asset){
 if(asset.market)return asset.market;
 return /^(US|HK)\./.exec(asset.code)?.[1]||'CN';
}
function watchMatchesCategory(asset,category){
 return category==='all'||asset.type===category||watchMarketOf(asset)===category;
}
function managedFilteredStocks(){
 const category=state.filter||'all';
 const query=state.query?.toLowerCase();
 return WATCH_CODES.map(byCode).filter(a=>a&&watchMatchesCategory(a,category)&&(!query||
  [a.name,a.code,a.symbol,a.industry,a.verified_name].filter(Boolean).some(value=>value.toLowerCase().includes(query))));
}
function managedWatchlist(){
 const stocks=WATCH_CODES.map(byCode).filter(Boolean),category=state.filter||'all';
 const canManage=hasLocalServer&&mode==='local';
 const categories=[['all','全部'],...Object.entries(WATCH_MARKET_NAMES),['stock','股票'],['etf','ETF']];
 const tabs=categories.map(([id,label])=>{
  const count=stocks.filter(asset=>watchMatchesCategory(asset,id)).length;
  return `<button type="button" class="filter-chip ${category===id?'active':''}" aria-pressed="${category===id}" onclick="state.filter='${id}';render()">${label} ${count}</button>`;
 }).join('');
 return title('我的自选','按上市市场查看股票与 ETF，新增后自动联动研究和回测。',
  `<button class="btn primary" onclick="openWatchSearch()" ${canManage?'':'disabled'}>${icon('plus')}添加自选股</button>`+button('比较A股因子',"go('factors')",false,'settings'))+
  `<div class="toolbar"><div class="filter-tabs watch-category-tabs" role="group" aria-label="自选分类">${tabs}</div>
   <div class="watch-filter-tools"><input placeholder="名称 / 代码 / 行业" aria-label="筛选自选" value="${esc(state.query)}" oninput="state.query=this.value;document.getElementById('watch-rows').innerHTML=watchTable(filteredStocks())"></div>
  </div>
  ${!canManage?notice('运行本地单文件服务并切换真实行情模式后，可搜索、添加和删除自选股。'):''}
  ${localStatus?.cache_warning?notice('自选持久化状态：'+esc(localStatus.cache_warning),'warn'):''}
  <section class="panel"><div id="watch-rows">${watchTable(filteredStocks())}</div></section>
  <p class="watch-market-note">5 / 20 / 60 / 220日均为各标的有效日频间隔，按交易币种计算，不是人民币组合收益。港美股不套A股六ETF过滤；海外研究成交单位为1股，港股真实每手数量、碎股撮合与完整税费尚未建模。年度财务与A股累计季度披露分开标注。</p>`;
}
window.watchlist=managedWatchlist;window.filteredStocks=managedFilteredStocks;
if(uploaded?.catalog)syncWatchCatalog(uploaded.catalog);
document.getElementById('dialog').addEventListener('close',()=>{if(document.getElementById('dialog').dataset.kind==='watch-search'){watchSearch.controller?.abort();clearTimeout(watchSearch.timer);watchSearch.sequence++}});

const WATCH_SORT_KEYS=new Set(['name','industry','price','change','ret5','ret20','ret60','ret220','trend','signal','samples','asOf','flow1','flow3','flow5','flow10','flow20','flow60']);
function watchSortValue(a,key){
 if(['flow1','flow3','flow5','flow10','flow20','flow60'].includes(key))return watchFlowSnapshot(a).values[key];
 if(key==='name')return a.name||null;
 if(key==='industry')return [a.industry,a.type==='etf'?'ETF':'股票'].filter(Boolean).join(' ');
 const bars=series(a.code);
 if(key==='samples')return bars.length;
 if(key==='asOf')return obs(a.code)?.as_of||null;
 if(key==='signal')return latestSignal(a.code)?.date||null;
 const m=E.metrics(bars);
 if(key==='trend')return ({'趋势偏弱':0,'趋势分歧':1,'趋势偏强':2})[trend(m).text]??null;
 return m?.[key]??null;
}
function sortWatchRows(list,sort=state.watchSort,valueOf=watchSortValue){
 if(!sort||!WATCH_SORT_KEYS.has(sort.key)||!['asc','desc'].includes(sort.direction))return list.slice();
 const missing=v=>v===null||v===undefined||typeof v==='number'&&!Number.isFinite(v)||v==='';
 const sign=sort.direction==='asc'?1:-1;
 return list.map((asset,index)=>({asset,index,value:valueOf(asset,sort.key)})).sort((a,b)=>{
  const am=missing(a.value),bm=missing(b.value);
  if(am||bm)return am===bm?a.index-b.index:am?1:-1;
  const order=typeof a.value==='number'&&typeof b.value==='number'?a.value-b.value:String(a.value).localeCompare(String(b.value),'zh-CN',{numeric:true,sensitivity:'base'});
  return order*sign||a.index-b.index;
 }).map(row=>row.asset);
}
function watchSortButton(key,label,hint=''){
 const active=state.watchSort?.key===key,direction=active?state.watchSort.direction:null,next=direction==='asc'?'降序':'升序';
 return `<button class="watch-sort-button ${active?'active':''}" data-watch-sort="${key}" onclick="toggleWatchSort('${key}')" aria-label="${esc(label)}，点击${next}排列" title="${esc(hint||label)}；点击${next}">${esc(label)}<span aria-hidden="true">${direction==='asc'?'↑':direction==='desc'?'↓':'↕'}</span></button>`;
}
function watchSortHeader(key,label,hint=''){
 const active=state.watchSort?.key===key||key==='samples'&&state.watchSort?.key==='asOf';
 const order=active?(state.watchSort.direction==='asc'?'ascending':'descending'):'none';
 return `<th aria-sort="${order}">${key==='samples'?watchSortButton('samples','样本','按有效日线条数排序')+'<span class="watch-sort-separator">/</span>'+watchSortButton('asOf','数据时间','按行情日期排序'):watchSortButton(key,label,hint)}</th>`;
}
function toggleWatchSort(key){
 if(!WATCH_SORT_KEYS.has(key))return;
 state.watchSort={key,direction:state.watchSort?.key===key&&state.watchSort.direction==='asc'?'desc':'asc'};
 const root=document.getElementById('watch-rows');
 if(!root){render();return}
 const scroll=root.querySelector('.table-wrap')?.scrollLeft||0;
 root.innerHTML=watchTable(filteredStocks());
 const wrap=root.querySelector('.table-wrap');if(wrap)wrap.scrollLeft=scroll;
 root.querySelector(`[data-watch-sort="${key}"]`)?.focus({preventScroll:true});
}
