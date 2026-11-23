/* Company evidence and conservative readings; independent of signals/backtest inputs. */
const StockResearch=(()=>{
 const labels={pingan:'平安证券资金查询',futu_capital:'富途资金异动',ifind:'同花顺经营与披露',futu_news:'富途新闻'};
 const rows=value=>Array.isArray(value)?value:[];
 const available=p=>p?.data&&typeof p.data==='object'?p.data:null;
 function evidence(record){const raw=record?.research_evidence;return raw?.version===1?raw:{providers:{}}}
 function facts(record){return rows(available(evidence(record).providers?.ifind)?.facts).filter(f=>f&&typeof f.label==='string').slice(0,60)}
 function news(record){return rows(available(evidence(record).providers?.futu_news)?.items).filter(n=>n&&typeof n.title==='string').slice(0,10)}
 function eventCue(item){
  const text=item.title+' '+(item.text||'');
  const negative=/下滑|下降|预亏|亏损扩大|减持|诉讼|处罚|终止|取消订单|调查|降级|下调|decline|downgrade|lawsuit/i.test(text);
  const positive=/增长|增持|回购|中标|订单增加|量产|交付|上调|获批|profit growth|upgrade|buyback/i.test(text);
  const uncertain=/拟|计划|传闻|网传|或将|辟谣|否认|未获|未经证实|rumou?r|denies|plans? to/i.test(text);
  const topic=/订单|产品|交付|量产|客户/.test(text)?'产品与订单':/财报|业绩|营收|利润|盈利/.test(text)?'经营业绩':/回购|增持|减持|解禁/.test(text)?'股本与治理':'公司动态';
  const transmission={
   '产品与订单':['产品验证或交付可能影响未来收入与毛利，但技术路线、客户认证与商业订单处在不同阶段。','核对是否有正式合同、交付批次、对应收入占比及回款；不能将研发合作直接计作收入。'],
   '经营业绩':['业绩信息可能改变盈利兑现预期；收入、利润与经营现金应在同一报告期交叉验证。','核对同比与环比、累计与单季、主营与一次性收益，并检查现金流是否同步。'],
   '股本与治理':['回购、增持、减持或解禁可能改变股票供给与治理预期，影响取决于规模和实际执行。','核对拟议/完成状态、金额或股数、占总股本/流通股比例、执行期限及后续披露。'],
   '公司动态':['这条信息与公司有关，但对经营与估值的传导仍需具体事实支持。','先核对原文涉及的业务、事项范围与时间，再判断是否改变现有研究假设。']};
  return {direction:uncertain||positive===negative?'neutral':positive?'bullish':'bearish',topic,
   impact:transmission[topic][0],verify:transmission[topic][1],
   limitation:uncertain?'计划、否认或未确认措辞，暂不判断兑现方向':'仅为标题/摘要措辞线索，须核对原文适用范围'};
 }
 function digest(record){
  const events=news(record).map(n=>({...n,...eventCue(n)})),positive=events.filter(n=>n.direction==='bullish').length,negative=events.filter(n=>n.direction==='bearish').length;
  const direction=!events.length?'missing':positive&&negative?'neutral':positive?'bullish':negative?'bearish':'neutral';
  return {events,direction,positive,negative,label:{missing:'近期公司新闻未取得',neutral:'事件线索分化 / 待核实',bullish:'偏正面事件线索',bearish:'偏负面事件线索'}[direction],
   summary:!events.length?'没有可核验的近期新闻，不能判断消息面方向。':`${events.length}条去重公司新闻中，${positive}条包含正向措辞、${negative}条包含负向措辞。${positive&&negative?'正负线索同时存在，保持中性。':'方向仅用于定位原文，尚未核实对收入、利润和现金流的实际影响。'}`};
 }
 function checks(c){
  const f=c.f||{},valid=Number.isFinite,fs=facts(c.r),has=key=>fs.find(x=>x.key===key);
  if(c.a.type==='etf')return [];
  const tech=(c.a.market||'CN')==='CN'&&/科技|芯片|半导体|通信|光|算力|AI|人工智能|新能源|锂|云|软件|电子|存储|机器人|数据/i.test([c.a.industry,c.a.topic,c.a.name].join(' '));
  const checks=[
   {title:'增长与估值兑现',ready:valid(f.pe)||valid(f.ps),warn:valid(f.pe)&&f.pe>60&&valid(f.revenueGrowth)&&f.revenueGrowth<20,
    text:`PE（TTM）${valid(f.pe)?f.pe.toFixed(1)+'倍':'未返回'}，PS（TTM）${valid(f.ps)?f.ps.toFixed(1)+'倍':'未返回'}；${f.period||'报告期未返回'}收入同比${valid(f.revenueGrowth)?f.revenueGrowth.toFixed(1)+'%':'未返回'}。`,
    next:'比较同一细分行业的增长、毛利率及估值；历史累计同比不用于计算前瞻 PEG。PE>60 且收入增长<20%仅为本页关注阈值。'},
   {title:'利润到现金的转换',ready:valid(f.operatingCash),warn:valid(f.operatingCash)&&(f.operatingCash<0||f.profit>0&&valid(f.cashRatio)&&f.cashRatio<.8),
    text:valid(f.operatingCash)?`经营现金${f.operatingCash<0?'为负':'为正'}，现金/净利润${valid(f.cashRatio)?f.cashRatio.toFixed(2)+'倍':'不可比 / 未返回'}；自由现金流${valid(f.freeCashFlow)?(f.freeCashFlow<0?'为负':'为正'):'未返回'}。`:'经营现金、利润及资本开支尚未形成同报告期证据。',
    next:'同报告期核对应收、存货与资本开支；负现金流需区分投入扩张和回款恶化。'},
   {title:'研发与股份支付',ready:!!has('rd_capitalization')||!!has('sbc'),warn:false,
    text:[has('rd_capitalization')?'已取得研发资本化字段':'研发资本化比例未返回',has('sbc')?'已取得股份支付字段':'股份支付费用未返回'].join('；')+'。',
    next:'核对资本化会计政策及同行差异；GAAP 净利润已含股份支付费用时，不重复扣减。'},
   {title:'资本效率',ready:!!has('roic')||valid(f.roe),warn:false,
    text:has('roic')?`已取得 ROIC：${has('roic').value} ${has('roic').unit}。`:`ROIC 未返回；ROE ${valid(f.roe)?f.roe.toFixed(1)+'%':'未返回'}不能直接替代 ROIC。`,
    next:'将资本效率与资本成本、研发和扩产周期比较；缺少同业或资本成本时不判定高低估。'},
   {title:tech?'科技概念与商业化':'产品与业务兑现',ready:!!has('business_share')||!!has('segments'),warn:false,
    text:has('business_share')||has('segments')?'已有主营收入构成或占比字段，见业务证据及其报告期。':'尚未取得产品收入占比、商业化收入或主营构成，不能把概念热度当作订单收入。',
    next:'从公告核实产品验证→交付→收入确认→现金回款；战略合作及研发计划保持待验证。'},
   {title:'供给与治理压力',ready:!!c.events?.length||!!available(evidence(c.r).providers?.ifind)?.notices?.length,warn:false,
    text:'从减持、解禁、关联交易和客户集中度等披露定位风险；未披露数值不估计。',
    next:'核对实际规模、占流通股比例、执行日期和进展；标题中的“拟减持”不等于已完成。'}
  ];
  return checks.map(x=>({...x,tech,status:!x.ready?'缺少证据':x.warn?'需关注':'继续核对'}));
 }
 return {labels,evidence,facts,news,eventCue,digest,checks};
})();
function stockResearchProvider(c,key){return StockResearch.evidence(c.r).providers?.[key]||{}}
function stockResearchData(c,key){return stockResearchProvider(c,key).data||{}}
function researchTimestamp(value){return esc(typeof value==='string'?value.replace('T',' ').slice(0,19):'未返回')}
function stockResearchActions(c){
 if(c.a.type==='etf')return '';
 const job=typeof localStatus==='undefined'?{}:localStatus?.research_jobs?.[c.a.code]||{},enabled=typeof hasLocalServer!=='undefined'&&hasLocalServer&&mode==='local';
 return `<button class="btn primary" onclick="refreshStockResearch('${esc(c.a.code)}')" ${!enabled||job.status==='loading'?'disabled':''}>${job.status==='loading'?'研究证据读取中…':'补充 / 刷新公司研究'}</button>`;
}
async function refreshStockResearch(code){
 try{await requestLocalPost('/api/research/refresh',{code});await connectLocal();toast('正在补充该股票的独立研究证据')}catch(e){toast('研究刷新失败：'+e.message)}
}
function stockResearchOverview(c){
 if(c.a.type==='etf')return `<div class="notice panel-space">ETF 研究继续使用成分持仓、净值与申赎证据；经营、产品和科技估值模型仅适用于公司。</div>`;
 const evidence=StockResearch.evidence(c.r),digest=StockResearch.digest(c.r),checks=StockResearch.checks(c),warn=checks.filter(x=>x.warn),facts=StockResearch.facts(c.r),job=typeof localStatus==='undefined'?{}:localStatus?.research_jobs?.[c.a.code]||{};
 const adopted=Object.values(evidence.providers||{}).filter(p=>p.data).length;
 return `<section class="panel company-research panel-space" aria-label="公司研究摘要"><div class="panel-head"><div><h2>公司研究摘要</h2><p class="small muted">经营兑现 × 资金行为 × 事件证据</p></div>${stockResearchActions(c)}</div>
 <div class="company-summary-grid"><article><span class="small muted">经营与估值</span><h3>${warn.length?warn.length+'项需要进一步核对':'先核对经营兑现'}</h3><p>${esc(warn[0]?.text||checks[0]?.text||'经营证据尚未返回')}</p><small>财务报告期 ${esc(c.f.period||'未返回')} · 估值时点 ${researchTimestamp(c.f.valuation_date)}</small></article>
 <article><span class="small muted">公司事件摘要</span><h3>${esc(digest.label)}</h3><p>${esc(digest.summary)}</p>${digest.events[0]?`<p><strong>最新关注：</strong>${esc(digest.events[0].title)}</p>`:''}<small>最新新闻 ${researchTimestamp(digest.events[0]?.published_at)} · 规则摘要，非独立事实鉴定</small></article>
 <article><span class="small muted">证据完整性</span><h3>${adopted} / 4 类来源有返回</h3><p>${facts.length}项业务/财务字段，${digest.events.length}条公司新闻。${Object.values(evidence.providers||{}).some(p=>p.status==='retained')?'本轮部分来源失败，正在展示带原日期的旧证据。':'数据覆盖不等于研究结论可信度。'}</p><small>最近读取 ${researchTimestamp(evidence.checked_at)}${job.result==='failed'?' · 本轮所有来源均未取得新证据':''}</small></article></div>
 ${job.status==='error'?`<p class="notice" role="alert">${esc(job.error)}</p>`:''}
 <details class="company-source-details"><summary>来源连接、缺口与证据日期</summary><div class="company-source-grid">${Object.entries(StockResearch.labels).map(([key,label])=>{
  const p=stockResearchProvider(c,key),d=p.data,asof=d?.as_of||(d?.time_range||d?.requested_range?.start&&d.requested_range.start+' — '+d.requested_range.end);
  const status={ok:'已返回',partial:'部分字段返回',retained:'失败，保留旧证据',skipped:'跳过',failed:'失败'}[p.status]||'尚未读取';
  return `<article><strong>${esc(label)}</strong><span>${esc(status)}</span><p>${p.error?esc(p.error):d?.basis?esc(d.basis):'点击补充研究读取此来源。'}</p>${d?.errors?`<p>${Object.entries(d.errors).map(([k,v])=>esc(k+'：'+v)).join('；')}</p>`:''}<small>证据日期 ${esc(asof||'未返回；不得视为当日数据')}<br>读取 ${researchTimestamp(p.data_checked_at||p.checked_at)}</small></article>`;
 }).join('')}</div></details><div class="panel-note">仅为公开信息研究。新闻摘要与经营核对由规则生成，不构成投资建议；当前证据不回填历史回测。</div></section>`;
}
function stockResearchFactTable(c,keys){
 const rows=StockResearch.facts(c.r).filter(f=>keys.includes(f.key));
 if(!rows.length)return '<div class="empty">该类结构化经营证据尚未返回；原有财报及下方验证框架继续保留。</div>';
 return `<div class="table-wrap"><table class="wrap-table"><thead><tr><th>证据字段</th><th>供应商原值 / 单位</th><th>报告期 / 披露日期</th><th>来源</th></tr></thead><tbody>${rows.map(f=>`<tr><td>${esc(f.label)}</td><td class="company-fact-value">${esc(f.value)}${f.kind==='text'?'':' · '+esc(f.unit||'单位未返回')}${f.currency?' · '+esc(f.currency):''}</td><td>${esc(f.period)}<br><small>${esc(f.notice_date||'披露日期未返回')}</small></td><td>${f.url?sourceLink(f.url,f.source||'原文'):esc(f.source||'同花顺 iFinD')+' · 原文链接未返回'}</td></tr>`).join('')}</tbody></table></div>`;
}
function stockResearchNews(c,includeNotices=false){
 const digest=StockResearch.digest(c.r),notices=Array.isArray(stockResearchData(c,'ifind').notices)?stockResearchData(c,'ifind').notices.filter(n=>n&&typeof n.title==='string'):[],events=includeNotices?[...notices.map(n=>({...n,direction:'neutral',topic:'公告检索',limitation:'检索片段不等于公告全文，核对原文'})),...digest.events]:digest.events;
 if(!events.length)return '<div class="empty">未取得该公司可核验的近期新闻或公告；不据此判断“没有事件风险”。</div>';
 return `<div class="company-event-list">${events.slice(0,18).map(n=>`<article><div class="chip-line">${pill(esc(n.topic||'公司动态'))}${pill({bullish:'偏正面措辞',bearish:'偏负面措辞',neutral:'待核实'}[n.direction]||'待核实',n.direction==='bearish'?'warn':'neutral')}<small>${researchTimestamp(n.published_at)} · ${esc(n.source||'富途新闻')}</small></div><h3>${sourceLink(n.url,n.title)}</h3>${typeof n.text==='string'&&n.text?`<p>${esc(n.text.slice(0,500))}</p>`:''}${n.impact?`<p><strong>潜在传导：</strong>${esc(n.impact)}</p><p><strong>下一步验证：</strong>${esc(n.verify)}</p>`:''}<small>${esc(n.limitation||'仅为线索，需核对原文')}</small></article>`).join('')}</div>`;
}
function stockResearchCapital(c){
 const pingan=stockResearchData(c,'pingan'),futu=stockResearchData(c,'futu_capital'),m=pingan.metrics||{};
 return `<div class="panel-body"><h3>资金分类与资金异动分开核对</h3>${pingan.metrics?`<div class="evidence-metrics"><div><small>主力分类净额</small><strong>${billions(m.main_net_inflow,'CNY')}</strong></div><div><small>超大单 / 大单</small><strong>${billions(m.super_large_net_inflow,'CNY')} / ${billions(m.large_net_inflow,'CNY')}</strong></div></div><p class="small muted">平安证券 · 行情时间 ${researchTimestamp(pingan.as_of)}${pingan.time_missing?'；缺少资金观察时间，本数值不计入当日或连续净流入。':''}</p>`:'<p class="muted">平安证券资金分类尚未返回。</p>'}
 ${futu.content?`<h3>富途 · 最近7个自然日资金异动</h3><p class="small muted">${esc(futu.time_range||'请求窗口 '+futu.requested_range?.start+' — '+futu.requested_range?.end)}</p><div class="company-capital-original">${esc(futu.content)}</div>`:'<p class="muted">富途资金分布/经纪商、流向与卖空异动尚未取得。查看来源详情中的 CLI 或权限提示。</p>'}
 <p class="small muted">供应商原文保留各类“无异动”结论。资金分类、卖空成交与公司经营现金流不能互相替代；不同供应商金额不直接相加。</p></div>`;
}
function stockResearchHype(c){
 const checks=StockResearch.checks(c);
 return `<div class="panel-body"><h3>${checks[0]?.tech?'科技炒作与基本面验证':'经营与估值验证'}</h3><p class="small muted">概念→产品→收入→现金回款逐步验证。缺少同业估值、前瞻盈利或商业化收入时，不输出“高估/低估”断言、目标价或修复概率。</p><div class="company-check-grid">${checks.map(x=>`<article class="${x.warn?'attention':''}"><div>${pill(x.status,x.warn?'warn':'neutral')}</div><h3>${esc(x.title)}</h3><p>${esc(x.text)}</p><small><strong>下一步：</strong>${esc(x.next)}</small></article>`).join('')}</div></div>`;
}
function stockResearchDimension(c,index){
 if(c.a.type==='etf')return '';
 const parts=[
  ()=>panel('主营产品与产业兑现证据',stockResearchFactTable(c,['industry','business','products','segments','business_share'])+`<div class="panel-body"><h3>最近产品 / 订单线索</h3>${stockResearchNews(c)}</div>`),
  ()=>panel('增长、现金与资本效率补充',stockResearchFactTable(c,['revenue_growth','profit_growth','gross_margin','operating_cash','capex','roic','rd_capitalization','sbc'])+stockResearchHype(c)),
  ()=>panel('估值兑现与科技概念核对',stockResearchHype(c)+stockResearchFactTable(c,['segments','business_share','roic','rd_capitalization','sbc'])),
  ()=>panel('资金异动与交易分类补充',stockResearchCapital(c)),
  ()=>panel('量价与事件的交叉验证',`<div class="panel-body"><p>量价和信号由原有收盘规则计算；下方事件单独保留发布时间，不用于回填或解释发布前的信号。</p>${stockResearchNews(c)}</div>`),
  ()=>panel('公司事件语气与经营兑现',`<div class="panel-body"><h3>${esc(StockResearch.digest(c.r).label)}</h3><p>${esc(StockResearch.digest(c.r).summary)}</p>${stockResearchNews(c)}</div>`),
  ()=>panel('公司新闻与公告原文核对',`<div class="panel-body">${stockResearchNews(c,true)}</div>`)
 ];return parts[index]?.()||'';
}
