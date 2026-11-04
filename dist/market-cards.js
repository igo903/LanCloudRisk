'use strict';
// Shared card anatomy for global assets and the six benchmark ETFs.
const MARKET_CARD_GROUPS = [
 ['all','全部'],['macro','美元 / 汇率 / 利率'],['equity','海外股市'],['china','离岸中国'],['cross','贵金属 / 加密']
];
function marketAssetGroup(symbol){return ['DXY','CNH','US10Y'].includes(symbol)?'macro':['hkHSTECH','A50'].includes(symbol)?'china':['GOLD','SILVER','BTC'].includes(symbol)?'cross':'equity'}
function marketMonogram(symbol){return ({DXY:'DX',CNH:'CNH',US10Y:'10Y',DJI:'DJ',IXIC:'IXIC',N225:'225',KOSPI:'KR',SOX:'SOX',usSOXX:'SOXX',usQQQ:'QQQ',hkHSTECH:'HST',A50:'A50',GOLD:'Au',SILVER:'Ag',BTC:'₿','510300':'300','159338':'A500','510500':'500','512100':'1000','159915':'创业','588080':'科创'})[symbol]||symbol}
function cardPeriod(label,value,{unit='%',start,end,need}={}){
 const known=E.valid(value),detail=known&&start&&end?`${start} → ${end}`:need?`至少需要${need}条有效日线`:'历史不足';
 return `<div class="market-period ${known?(value>0?'positive':value<0?'negative':'flat'):'unavailable'}" title="${esc(detail)}"><span>${esc(label)}</span><strong>${known?(value>0?'+':'')+num(value)+(unit==='bp'?' bp':'%'):'—'}</strong>${!known?'<small>历史不足</small>':''}</div>`;
}
function marketMiniChart(bars,label){
 const b=(bars||[]).filter(x=>E.valid(x.close)&&x.close>0).slice(-61);
 if(b.length<2)return `<div class="market-card-chart empty-chart"><span>历史日线不足</span></div>`;
 const lo=Math.min(...b.map(x=>x.close)),hi=Math.max(...b.map(x=>x.close)),up=b.at(-1).close>=b[0].close;
 const points=b.map((x,i)=>`${(i/(b.length-1)*300).toFixed(2)},${(38-(x.close-lo)/(hi-lo||1)*32).toFixed(2)}`).join(' ');
 return `<div class="market-card-chart"><svg viewBox="0 0 300 44" role="img" aria-label="${esc(label)} · 最近${b.length}条日线 ${esc(b[0].date)}至${esc(b.at(-1).date)}"><path d="M0 42H300" stroke="var(--line)" fill="none"/><polyline points="${points}" fill="none" stroke="${up?'var(--red)':'var(--green)'}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/></svg></div>`;
}
function globalAssetCard(x){
 const model=x.model||R.technical(x.history,x.kind),context=RISK_CONTEXT[x.symbol],fresh=R.fresh(x.history_as_of,x.kind==='crypto'?2:7);
 return `<article class="market-card global-asset-card" data-symbol="${esc(x.symbol)}"><header class="market-card-heading"><span class="market-monogram ${marketAssetGroup(x.symbol)}" aria-hidden="true">${esc(marketMonogram(x.symbol))}</span><div class="market-card-identity"><h3>${esc(x.name)}</h3><span>${esc(x.ticker||x.symbol)} · ${x.kind==='yield'?'国债收益率':x.unit==='点'?'指数':'资产代理'}</span></div><div class="market-card-score"><strong>${riskNumber(model)}</strong><small>风险 /100</small></div></header><div class="market-card-price"><strong>${num(x.price,x.symbol==='CNH'?4:2)}</strong><span>${esc(x.unit||'')}</span>${E.valid(x.change)?`<span class="market-day ${cls(x.change)}">${change(x.change)}<small>最近一日</small></span>`:''}</div>${marketMiniChart(x.history,x.name)}<div class="market-card-periods">${cardPeriod('5日变化',x.returns?.['5']?.value,{...x.returns?.['5'],need:6})}${cardPeriod('20日变化',x.returns?.['20']?.value,{...x.returns?.['20'],need:21})}${cardPeriod('60日变化',x.returns?.['60']?.value,{...x.returns?.['60'],need:61})}${cardPeriod('250日变化',x.returns?.['250']?.value,{...x.returns?.['250'],need:251})}</div><p class="market-card-insight">${esc(context?.[0]||'辅助观察海外产业链风险。')}</p><button class="market-card-evidence" aria-haspopup="dialog" onclick="openExternalRiskDetails('${x.symbol}')">解读与评分依据 ${icon('arrow')}</button><footer class="market-card-footer"><span class="market-card-time ${fresh?'':'outdated'}">${esc(x.history_as_of||'日期缺失')}${fresh?(x.provisional?' · 盘中':''):' · 缺失或过期'}</span><span>${x.url?sourceLink(x.url,'来源'):''}<span class="market-card-weight">权重 ${num(x.weight,0)}%</span></span></footer></article>`;
}
function selectedGlobalAssets(assets){const selected=MARKET_CARD_GROUPS.some(([key])=>key===state.riskGroup)?state.riskGroup:'all';return assets.filter(x=>selected==='all'||marketAssetGroup(x.symbol)===selected)}
function globalAssetControls(assets){const selected=MARKET_CARD_GROUPS.some(([key])=>key===state.riskGroup)?state.riskGroup:'all';return `<div class="market-card-controls" role="group" aria-label="外围风险分类">${MARKET_CARD_GROUPS.map(([key,label])=>{const count=key==='all'?assets.length:assets.filter(x=>marketAssetGroup(x.symbol)===key).length;return `<button class="filter-chip ${key===selected?'active':''}" aria-pressed="${key===selected}" onclick="state.riskGroup='${key}';render()">${label}<span>${count}</span></button>`}).join('')}</div>`}
function globalAssetCards(assets){return globalAssetControls(assets)+`<div class="market-card-grid global-card-grid">${selectedGlobalAssets(assets).map(globalAssetCard).join('')}</div>`}

function openBenchmark(code){state.market='bench';setAsset(code,'market')}
function benchmarkCard(code,model,selected){
 const a=byCode(code),bars=series(code),m=E.metrics(bars),last=bars.at(-1),r=obs(code),hasRisk=!!model;
 return `<article class="market-card benchmark-card ${selected===code?'selected':''}" data-code="${code}"><header class="market-card-heading"><span class="market-monogram benchmark" aria-hidden="true">${marketMonogram(code)}</span><div class="market-card-identity"><h3><button onclick="openBenchmark('${code}')">${esc(a.shortName||a.name)}</button></h3><span>${code} · ${esc(a.manager||'ETF代理')}</span></div>${hasRisk?`<div class="market-card-score"><strong>${riskNumber(model)}</strong><small>风险 /100</small></div>`:`<span class="market-card-trend">${pill(trend(m).text,trend(m).type)}</span>`}</header><div class="market-card-price"><strong>${num(m?.price,3)}</strong><span>元 / 份</span><span class="market-day ${cls(m?.change)}">${change(m?.change)}<small>最近一日</small></span></div>${marketMiniChart(bars,a.shortName||a.name)}<div class="market-card-periods">${cardPeriod('20日涨跌',m?.ret20,{need:21,start:bars.at(-21)?.date,end:last?.date})}${cardPeriod('60日涨跌',m?.ret60,{need:61,start:bars.at(-61)?.date,end:last?.date})}</div><div class="market-card-context"><span>20日年化波动 <b>${change(m?.vol20)}</b></span><span>${hasRisk?'本地权重 1/6':esc(a.industry||'指数观察代理')}</span></div><footer class="market-card-footer"><span>${esc(last?.date||'日期缺失')}${last?.completed===false?' · 盘中':r?' · 收盘':''}</span><button class="market-card-link" onclick="setAsset('${code}','backtest')">查看K线 ${icon('arrow')}</button></footer></article>`;
}
function benchmarkCards(selected=null,riskRows=null){return `<div class="market-card-grid benchmark-card-grid">${BENCH_CODES.map(code=>benchmarkCard(code,riskRows?.find(x=>x.code===code)?.model,selected)).join('')}</div>`}

// Market layouts are independent of the risk overview; both ETF groups share one view.
function marketLayout(){return state.marketLayout==='table'?'table':'cards'}
function marketLayoutControl(){return `<div class="risk-view-switch" role="group" aria-label="六ETF与行业展示方式">${[['cards','卡片'],['table','表格']].map(([value,label])=>`<button data-market-view="${value}" aria-pressed="${marketLayout()===value}" onclick="setMarketLayout('${value}')">${icon(value==='cards'?'nodes':'settings')}${label}</button>`).join('')}</div>`}
function setMarketLayout(layout){
 if(!['cards','table'].includes(layout))return;
 const top=document.getElementById('market-bench-view')?.getBoundingClientRect().top;
 state.marketLayout=layout;render();
 const section=document.getElementById('market-bench-view');if(section&&Number.isFinite(top))window.scrollBy(0,section.getBoundingClientRect().top-top);
 document.querySelector(`[data-market-view="${layout}"]`)?.focus({preventScroll:true});
}
function marketTrendRank(metrics){return metrics?.ma60?({warn:0,neutral:1,good:2})[trend(metrics).type]??null:null}
function marketBenchDisplay(selected){return `<section id="market-bench-view" class="panel market-bench-view"><div class="panel-head"><h2>六类ETF · 市场观察</h2>${marketLayoutControl()}</div>${marketLayout()==='cards'?benchmarkCards(selected):marketBenchmarkTable()}</section>`}
function marketBenchmarkTable(){
 const rows=BENCH_CODES.map(code=>{const a=byCode(code),bars=series(code),m=E.metrics(bars),last=bars.at(-1),t=trend(m);return {values:{name:a.shortName||a.name,price:m?.price,change:m?.change,ret20:m?.ret20,ret60:m?.ret60,volatility:m?.vol20,trend:marketTrendRank(m),date:last?.date},cells:[`<button class="stock-link" onclick="openBenchmark('${code}')">${esc(a.shortName||a.name)}</button><small class="table-sub">${esc(code)} · ${esc(a.manager||'ETF代理')}</small>`,num(m?.price,3),`<span class="${cls(m?.change)}">${change(m?.change)}</span>`,`<span class="${cls(m?.ret20)}">${change(m?.ret20)}</span>`,`<span class="${cls(m?.ret60)}">${change(m?.ret60)}</span>`,change(m?.vol20),pill(t.text,t.type),esc(last?.date||'未返回')+(last?.completed===false?' · 盘中':'') ,button('查看K线',`setAsset('${code}','backtest')`)]}});
 return riskDataTable('marketBench','六ETF市场观察表',[['name','六类ETF'],['price','价格（元）'],['change','日涨跌'],['ret20','20日涨跌'],['ret60','60日涨跌'],['volatility','20日年化波动'],['trend','趋势','升序：偏弱、分歧、偏强；样本不足置后'],['date','数据日期'],[null,'研究']],rows);
}
function marketSectorRead(s,m){return `20日涨跌${change(m?.ret20)}，距60日最高收盘${change(m?.drawdown)}，最新价${num(m?.price,3)}${m?.ma60?(m.price<m.ma60?'低于':'高于')+'MA60':'；MA60样本不足'}。${s.code==='512480'?'与长鑫科技/中芯国际的经营、资本开支和折旧交叉观察。':'与光迅科技/长飞光纤的订单、盈利与现金流交叉观察。'}`}
function marketSectorsDisplay(sectors){
 const body=marketLayout()==='cards'?`<div class="panel-body">${sectors.map(s=>{const m=E.metrics(s.bars);return finding(esc(s.name),marketSectorRead(s,m),'该ETF为行业交易代理；覆盖与自选公司业务并不完全一致。',m?.ret20<0?'warn':'neutral')}).join('')||'<p class="muted small">行业代理接口未返回，不以股票涨跌拼造行业指数。</p>'}</div>`:marketSectorTable(sectors);
 return panel('科技与半导体行业代理',body,'','market-sector-panel');
}
function marketSectorTable(sectors){
 const rows=sectors.map(s=>{const m=E.metrics(s.bars),last=s.bars?.at(-1),t=trend(m);return {values:{name:s.name,price:m?.price,change:m?.change,ret20:m?.ret20,ret60:m?.ret60,drawdown:m?.drawdown,trend:marketTrendRank(m),date:last?.date},cells:[`<strong>${esc(s.name)}</strong><small class="table-sub">${esc(s.code)}</small>`,num(m?.price,3),`<span class="${cls(m?.change)}">${change(m?.change)}</span>`,`<span class="${cls(m?.ret20)}">${change(m?.ret20)}</span>`,`<span class="${cls(m?.ret60)}">${change(m?.ret60)}</span>`,change(m?.drawdown),pill(t.text,t.type),esc(last?.date||'未返回')+(last?.completed===false?' · 盘中':''),`<div class="risk-explain">${marketSectorRead(s,m)}<p class="muted">该ETF为行业交易代理，不能替代公司经营证据。</p></div>`]}});
 return riskDataTable('marketSector','科技与半导体行业代理表',[['name','行业代理'],['price','价格（元）'],['change','日涨跌'],['ret20','20日涨跌'],['ret60','60日涨跌'],['drawdown','60日高点回撤'],['trend','趋势','升序：偏弱、分歧、偏强；样本不足置后'],['date','数据日期'],[null,'产业核对']],rows);
}
