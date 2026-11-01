'use strict';
// Ordinal research model, v1.0. Weights and thresholds are assumptions, not calibrated probabilities.
const RiskModel=(()=>{
 const valid=x=>typeof x==='number'&&Number.isFinite(x),clip=x=>Math.max(0,Math.min(100,x));
 const mean=a=>a.reduce((s,x)=>s+x,0)/a.length;
 const day=()=>new Date(Date.now()+8*3600000).toISOString().slice(0,10);
 const lag=(date,today=day())=>/^\d{4}-\d{2}-\d{2}$/.test(date||'')?(Date.parse(today)-Date.parse(date))/864e5:Infinity;
 const fresh=(date,max=7,today)=>lag(date,today)>=0&&lag(date,today)<=max;
 function aggregate(parts){
  const total=parts.reduce((s,p)=>s+p.weight,0),known=parts.reduce((s,p)=>s+(valid(p.score)?p.weight*(valid(p.coverage)?Math.max(0,Math.min(1,p.coverage)):1):0),0);
  const sum=parts.reduce((s,p)=>s+(valid(p.score)?clip(p.score)*p.weight*(valid(p.coverage)?Math.max(0,Math.min(1,p.coverage)):1):0),0);
  return {score:known?sum/known:null,coverage:total?known/total:0,low:total?sum/total:0,high:total?(sum+100*(total-known))/total:100,parts};
 }
 const leaf=(name,weight,score,reason,date)=>({name,weight,score:valid(score)?clip(score):null,coverage:valid(score)?1:0,reason,date});
 function technical(history,kind='equity',today=day()){
  const bars=(history||[]).filter(x=>valid(x.close)&&x.close>0&&fresh(x.date,Infinity,today)).slice().sort((a,b)=>a.date.localeCompare(b.date));
  const latest=bars.at(-1),maxAge=kind==='crypto'?2:7;
  if(!latest||!fresh(latest.date,maxAge,today))return {...aggregate([leaf('价格证据',100,null,'数据缺失或过期')]),reason:'缺少有效且新鲜的日线'};
  const c=bars.map(x=>x.close),last=latest.close,n=c.length,m20=n>=20?mean(c.slice(-20)):null,m60=n>=60?mean(c.slice(-60)):null,r20=n>20?(last/c.at(-21)-1)*100:null;
  const returns=c.slice(-21).slice(1).map((v,i)=>v/c.slice(-21)[i]-1),vol=returns.length>=20?Math.sqrt(mean(returns.map(x=>(x-mean(returns))**2)))*Math.sqrt(kind==='crypto'?365:252)*100:null;
  const dd=n>=60?(1-last/Math.max(...c.slice(-60)))*100:null;
  let parts;
  if(kind==='macro'||kind==='yield'){
   const rising=valid(m60)?((last>m20?50:0)+(last>m60?50:0)):null;
   const pressure=n>20?(kind==='yield'?clip((last-c.at(-21))*100/50*100):clip(r20/5*100)):null;
   const changes=c.slice(-21).slice(1).map((v,i)=>(v-c.slice(-21)[i])*100),yv=changes.length===20?Math.sqrt(mean(changes.map(x=>(x-mean(changes))**2))):null;
   parts=[leaf('上行趋势',50,rising,'高于MA20、MA60各计50分'),leaf('20日上行压力',30,pressure,kind==='yield'?'升50bp封顶100':'升5%封顶100'),leaf('波动',20,kind==='yield'?(valid(yv)?clip(yv/10*100):null):(valid(vol)?clip(vol/20*100):null),kind==='yield'?'日变化标准差10bp封顶':'年化波动20%封顶')];
  }else if(kind==='metal'){
   parts=[leaf('20日价格冲击',50,valid(r20)?clip(Math.abs(r20)/15*100):null,'绝对涨跌15%封顶；不把上涨直接视作利空'),leaf('波动',50,valid(vol)?clip(vol/60*100):null,'年化波动60%封顶')];
  }else{
   parts=[leaf('趋势',30,valid(m60)?((last<m20?50:0)+(last<m60?50:0)):null,'低于MA20、MA60各计50分'),leaf('20日下行',25,valid(r20)?clip(-r20/15*100):null,'跌15%封顶100'),leaf('60日回撤',25,valid(dd)?clip(dd/25*100):null,'距近60日最高收盘回撤25%封顶100'),leaf('波动',20,valid(vol)?clip(vol/(kind==='crypto'?100:60)*100):null,'年化波动60%封顶；比特币100%')];
  }
  return {...aggregate(parts),date:latest.date,ret20:r20,vol,drawdown:dd,reason:`${n}条日线 · ${latest.date}`};
 }
 function globalRisk(rows,geo,today=day()){
  const expected=[['DXY',10],['CNH',8],['US10Y',7],['DJI',3],['IXIC',5],['N225',3],['KOSPI',3],['SOX',6],['usSOXX',6],['usQQQ',9],['hkHSTECH',6],['A50',4],['GOLD',3],['SILVER',2],['BTC',5]];
  const assets=expected.map(([symbol,weight])=>{const r=(rows||[]).find(x=>x.symbol===symbol)||{symbol,name:symbol,history:[]};const model=technical(r.history,r.kind,today);return {...r,weight,model}});
  const parts=assets.map(r=>({...r.model,name:r.name,weight:r.weight}));
  for(const kind of ['politics','war']){const item=(geo?.items||[]).find(x=>x.kind===kind);parts.push(leaf(kind==='politics'?'政治 / 地缘威胁':'战争 / 冲突行动',10,fresh(geo?.as_of,14,today)?item?.score:null,'GPR子项30日均值的近3年历史分位；非事件概率',geo?.as_of))}
  return {...aggregate(parts),assets};
 }
 function domesticRisk(instruments,codes,external,today=day()){
  const rows=codes.map(code=>({code,model:technical(instruments?.[code]?.bars,'equity',today)}));
  const local=aggregate(rows.map(r=>({...r.model,name:r.code,weight:1})));
  return {...aggregate([{...local,name:'六ETF本地风险',weight:70},{...external,name:'外围风险传导',weight:30}]),local,rows};
 }
 function watchRisk(instrument,asset,domestic,today=day()){
  const r=instrument||{},f=r.fundamentals||{},etf=asset.type==='etf',price=technical(r.bars,'equity',today),raw=price.parts,qualityDate=f.notice_date||f.period,finFresh=!!f.period&&fresh(qualityDate,220,today),h=r.etf||{},hFresh=fresh(h.period,220,today);
  const financial=aggregate([leaf('收入增长压力',30,finFresh&&valid(f.revenueGrowth)?clip(-f.revenueGrowth/30*100):null,'累计营收同比下降30%封顶'),leaf('现金兑现压力',50,finFresh&&valid(f.cashRatio)&&f.profit>0?clip((1-f.cashRatio)*100):null,'正净利下，现金利润比低于1计压力；现金为负封顶'),leaf('负债压力',20,finFresh&&valid(f.debtRatio)?clip(f.debtRatio/80*100):null,'负债率80%封顶；未区分有息负债')]);
  const flows=(r.flows||[]).filter(x=>valid(x.netFlow)).slice().sort((a,b)=>a.date.localeCompare(b.date)),recent=flows.slice(-5),flow=recent.length===5&&fresh(recent.at(-1).date,7,today)?recent.reduce((s,x)=>s+x.netFlow,0):null;
  // No universal PE fair value and no event severity inferred from a headline count.
  const valuation=etf&&r.nav&&price.date&&r.nav.date===price.date&&valid(r.nav.value)&&r.nav.value>0?clip(Math.abs(r.bars.at(-1).close/r.nav.value-1)*100/3*100):null;
  const parts=[{...(raw.find(x=>x.name==='趋势')||{}),name:'趋势与价格',weight:20},
   etf?leaf('成分集中度',20,hFresh&&valid(h.top10_weight)?h.top10_weight:null,'前十占净值%映射关注分；季报非实时',h.period):{...financial,name:'盈利 / 现金 / 负债',weight:20,date:f.period},
   leaf(etf?'同日折溢价':'估值兑现',10,valuation,etf?'同日净值绝对偏离3%封顶；异日不计算':'缺行业可比与历史分位，不套固定PE阈值',r.nav?.date||f.period),
   leaf(etf?'申赎与流动性':'分类资金压力',10,etf?null:valid(flow)?clip(-flow/5e8*100):null,etf?'缺份额申赎与买卖价差，不用成交量替代':'近5条大单净流出5亿元封顶；非机构身份',flows.at(-1)?.date),
   {...(raw.find(x=>x.name==='波动')||{}),name:'波动风险',weight:15},
   {...(raw.find(x=>x.name==='60日回撤')||{}),name:'回撤压力',weight:15},
   leaf('治理 / 事件',10,null,`${(r.announcements||[]).length}条公告索引；须核对原文与影响，标题数量不自动评分`)];
  // A stale price series invalidates all price-dependent components.
  for(const p of parts)if(['趋势与价格','波动风险','回撤压力'].includes(p.name)&&!valid(p.score))Object.assign(p,{score:null,coverage:0,reason:'有效日线不足或过期'});
  const local=aggregate(parts),combined=aggregate([{...local,name:'个股 / ETF自身',weight:75},{...domestic,name:'A股风险传导',weight:25}]);
  return {...combined,local,code:asset.code,asset,price,parts:combined.parts};
 }
 return {aggregate,technical,globalRisk,domesticRisk,watchRisk,fresh,day,leaf};
})();
if(typeof module!=='undefined')module.exports=RiskModel;
