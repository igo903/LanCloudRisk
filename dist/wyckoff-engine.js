/* Rule-based Wyckoff research proxies. */
(function(root){
 'use strict';
 const valid=Number.isFinite,mean=a=>a.reduce((s,x)=>s+x,0)/a.length;
 const LABELS={Support:'支撑附近承接',Spring:'Spring · 假跌破',SOS:'SOS · 强势信号',LPS:'LPS · 缩量回踩支撑',UT:'UT／UTAD候选 · 假突破',SOW:'SOW · 弱势信号',Test:'Spring 后缩量测试'};
 const RULES={range:40,localRange:20,volume:20,confirm:3,retest:18,breakout:.0015,atrBreakout:.12,near:.55,highVolume:1.25,wideSpread:1.05,lowVolume:.9,maxRange:.35};
 const margin=(level,atr)=>Math.max(level*RULES.breakout,atr*RULES.atrBreakout);
 function rangeLevels(bars,i,window,atr){
  if(i<window)return null;
  const prior=bars.slice(i-window,i),support=Math.min(...prior.map(b=>b.low)),resistance=Math.max(...prior.map(b=>b.high));
  const touches=(field,level)=>{
   const tolerance=Math.max(.35*atr,level*.002);
   let count=0,last=-3;
   prior.forEach((b,j)=>{if(j-last>=3&&Math.abs(b[field]-level)<=tolerance){count++;last=j}});
   return count;
  };
  return {window,support,resistance,supportTouches:touches('low',support),resistanceTouches:touches('high',resistance)};
 }

 function detect(p,previous,levels,events,bars){
  const {index:i,atr,low,high,close,open,vol,volumeRatio:v,spreadRatio:s,closePosition:pos}=p;
  const match=(field,test)=>levels.find(zone=>(zone.primary||zone[field+'Touches']>=2)&&test(zone,zone[field],margin(zone[field],atr)));
  const event=(type,field,zone)=>({type,level:zone[field],window:zone.window,touches:zone[field+'Touches']});
  const inRange=z=>z.resistance-z.support<=Math.max(z.support*RULES.maxRange,12*atr);
  let zone=match('support',(z,level,m)=>inRange(z)&&low<level-m&&low>=level-1.5*atr&&close>level&&close<z.resistance&&pos>=.6);
  if(zone)return event('Spring','support',zone);
  zone=match('resistance',(z,level,m)=>inRange(z)&&high>level+m&&high<=level+1.5*atr&&close<level&&close>z.support&&pos<=.4);
  if(zone)return event('UT','resistance',zone);

  const expansion=(v>=RULES.highVolume&&s>=RULES.wideSpread)||(v>=1.1&&s>=1.45);
  if(expansion&&pos>=.65&&close>Math.max(open,previous.close)){
   zone=match('resistance',(_,level,m)=>close>level+m);
   if(zone)return event('SOS','resistance',zone);
  }
  if(expansion&&pos<=.35&&close<Math.min(open,previous.close)){
   zone=match('support',(_,level,m)=>close<level-m);
   if(zone)return event('SOW','support',zone);
  }

  const near=atr*RULES.near;
  if(v<RULES.lowVolume&&s<1&&pos>=.5){
   const recent=events.slice(-RULES.retest),spring=recent.findLast(e=>e.type==='Spring'&&i-e.index<=10);
   if(spring&&low>spring.low&&low<=spring.level+near&&close>=spring.level&&vol<spring.volume
     &&bars.slice(spring.index+1,i).every(b=>b.close>=spring.low)){
    return {type:'Test',level:spring.level,window:spring.window,parent:spring};
   }
   const sos=recent.findLast(e=>e.type==='SOS'&&i-e.index<=RULES.retest);
   if(sos){
    const floor=sos.level-Math.max(sos.level*.01,near);
    if(low>=floor&&low<=sos.level+near&&close>=sos.level&&vol<sos.volume*.8
      &&bars.slice(sos.index+1,i).every(b=>b.close>=floor)){
     return {type:'LPS',level:sos.level,window:sos.window,parent:sos};
    }
   }
  }

  const lowerWick=(Math.min(open,close)-low)/(high-low);
  if(pos>=.65&&lowerWick>=.25&&v>=1.05&&s<=1.25&&(close>open||close>previous.close)){
   zone=match('support',(z,level,m)=>z.supportTouches>=2&&low>=level-m&&low<=level+near&&close>level);
   if(zone)return event('Support','support',zone);
  }
  return null;
 }

 function validationLevels(type,p,level,bullish){
  const {close,low,high,atr}=p;
  if(type==='SOS'||type==='SOW')return {trigger:close,invalidLevel:bullish?Math.max(low,level-margin(level,atr)):Math.min(high,level+margin(level,atr))};
  return {trigger:bullish?Math.min(high,close+.2*atr):Math.max(low,close-.2*atr),invalidLevel:bullish?low:high};
 }
 function analyze(input,{end='',range=40}={}){
  range=[20,40,60].includes(Number(range))?Number(range):40;
  const base={ready:false,reason:'至少需要21根有效收盘日线。',bars:[],points:[],events:[],latest:null,live:[],range,rules:{...RULES,range},warnings:[]};
  if(!Array.isArray(input))return base;
  const bars=input.filter(b=>!end||b.date<=end).map(b=>({...b}));
  let previous='';
  for(const b of bars){
   if(!/^\d{4}-\d{2}-\d{2}$/.test(b.date)||b.date<=previous||!['open','high','low','close'].every(k=>valid(b[k])&&b[k]>0)||b.low>Math.min(b.open,b.close)||b.high<Math.max(b.open,b.close))return {...base,reason:'日期须升序且唯一，OHLC须有效；请先核对行情。'};
   previous=b.date;
  }
  const firstLive=bars.findIndex(b=>b.completed===false);
  if(firstLive>=0&&bars.slice(firstLive+1).some(b=>b.completed!==false))return {...base,reason:'盘中记录出现在历史收盘记录之前，请先重新核验行情。'};
  const closed=bars.filter(b=>b.completed!==false),live=bars.filter(b=>b.completed===false),points=[],events=[];
  for(let i=0;i<closed.length;i++){
   const b=closed[i],volumePast=closed.slice(Math.max(0,i-RULES.volume),i);
   const volumesReady=volumePast.length===RULES.volume&&volumePast.every(x=>valid(x.vol)&&x.vol>0)&&valid(b.vol)&&b.vol>0;
   const avgVolume=volumesReady?mean(volumePast.map(x=>x.vol)):null,avgSpread=volumePast.length===RULES.volume?mean(volumePast.map(x=>x.high-x.low)):null;
   const volumeRatio=avgVolume?b.vol/avgVolume:null,spreadRatio=avgSpread>0?(b.high-b.low)/avgSpread:null,closePosition=b.high>b.low?(b.close-b.low)/(b.high-b.low):.5;
   const atr=avgSpread===null?null:mean(volumePast.map((x,j)=>{
    const prev=closed[i-volumePast.length+j-1]?.close??x.open;
    return Math.max(x.high,prev)-Math.min(x.low,prev);
   }));
   const levels=[...new Set([range,RULES.localRange])].map(n=>rangeLevels(closed,i,n,atr)).filter(Boolean);
   for(const zone of levels)zone.primary=zone.window===range;
   const p={...b,index:i,ma20:i>=19?mean(closed.slice(i-19,i+1).map(x=>x.close)):null,ma60:i>=59?mean(closed.slice(i-59,i+1).map(x=>x.close)):null,support:levels[0]?.support??null,resistance:levels[0]?.resistance??null,atr,volumeRatio,spreadRatio,closePosition,events:[]};
   points.push(p);if(!levels.length||!volumesReady||!(atr>0)||!(spreadRatio>0))continue;
   const candidate=detect(p,closed[i-1],levels,events,closed);
   if(candidate){
    const {type,level,window,touches,parent}=candidate,bullish=type!=='UT'&&type!=='SOW';
    const e={id:b.date+'-'+type,date:b.date,index:i,type,label:LABELS[type],bullish,level,window,touches,parent:parent?.date??null,parentId:parent?.id??null,low:b.low,high:b.high,close:b.close,volume:b.vol,volumeRatio,spreadRatio,closePosition,atr,...validationLevels(type,p,level,bullish),status:'pending',knownAt:b.date,confirmAt:null,observationClose:null};
    events.push(e);p.events.push(e.id);
   }
  }
  // Confirmation is a separate dated observation, never an event backdated to a later bar.
  const byId=new Map(events.map(e=>[e.id,e]));
  for(const e of events){
   const parent=byId.get(e.parentId);
   const after=closed.slice(e.index+1,e.index+1+RULES.confirm);
   for(const b of after){
    const parentFailed=parent?.status==='failed'&&parent.knownAt<=b.date;
    const fail=parentFailed||(e.bullish?b.close<e.invalidLevel:b.close>e.invalidLevel);
    const parentReady=!parent||(parent.status==='validated'&&parent.knownAt<=b.date);
    const pass=parentReady&&(e.bullish?b.close>e.trigger:b.close<e.trigger);
    if(fail||pass){e.status=fail?'failed':'validated';e.confirmAt=b.date;e.knownAt=b.date;e.observationClose=b.close;e.parentFailed=parentFailed;break}
   }
   if(e.status==='pending'&&after.length===RULES.confirm){e.status='unconfirmed';e.knownAt=after.at(-1).date}
  }
  const latest=points.at(-1)||null,ready=closed.length>RULES.volume;
  const recent=closed.slice(-20),directions=recent.map((b,j)=>({b,prev:closed[closed.length-recent.length+j-1]}));
  const volumeReady=recent.length===20&&directions.every(({b,prev})=>prev&&valid(b.vol)&&b.vol>0);
  const up=volumeReady?directions.filter(x=>x.b.close>x.prev.close).reduce((s,x)=>s+x.b.vol,0):null;
  const down=volumeReady?directions.filter(x=>x.b.close<x.prev.close).reduce((s,x)=>s+x.b.vol,0):null;
  const upDownRatio=volumeReady&&down>0?up/down:null;
  const ret20=closed.length>20?(latest.close/closed.at(-21).close-1)*100:null;
  const slope=points.length>=65&&latest.ma60&&points.at(-6)?.ma60?(latest.ma60/points.at(-6).ma60-1)*100:null;
  let phase='样本不足',tone='neutral';
  if(closed.length>=61){
   if(latest.close>latest.ma20&&latest.ma20>latest.ma60&&slope>0){phase='需求占优 · 上涨结构';tone='good'}
   else if(latest.close<latest.ma20&&latest.ma20<latest.ma60&&slope<0){phase='供给占优 · 下跌结构';tone='warn'}
   else if(Math.abs(ret20)<=8){phase='区间震荡 · 方向待辨'}else{phase='结构过渡 · 多空分歧'}
  }
  const effort=!valid(latest?.volumeRatio)?'成交量证据不足':latest.volumeRatio>=1.5&&latest.spreadRatio<=.8?'放量窄幅 · 努力与结果背离':latest.volumeRatio>=1.5&&latest.spreadRatio>=1.2?'放量宽幅 · 波动扩张':latest.volumeRatio<=.8?'缩量 · 需结合价格位置':'量幅未触发显著阈值';
  const warnings=[];
  if(live.length)warnings.push('盘中K线仅展示，不参与结构、事件识别及验证；盘中成交量未经时段校准。');
  if(!valid(latest?.volumeRatio))warnings.push('最近21根成交量存在缺失、零值或预热不足，量价事件识别暂停。');
  return {...base,ready,reason:ready?'':base.reason,bars,points,events,latest,live,closedCount:closed.length,asOf:latest?.date||null,upDownRatio,ret20,slope,phase,tone,effort,warnings};
 }
 // Decisions use the confirmation date, never the earlier candidate date.
 function integrate(bars,baseline){
  const model=analyze(bars),byDate=new Map(baseline.map(s=>[s.date,s])),confirmations=new Map(),rows=[];
  for(const event of model.events){
   if(event.status!=='validated'||!event.confirmAt)continue;
   const group=confirmations.get(event.confirmAt)||[];group.push(event);confirmations.set(event.confirmAt,group);
  }
  let active=null;
  for(const p of model.points){
   const original=byDate.get(p.date),today=confirmations.get(p.date)||[];
   if(today.length){
    const event=today.findLast(e=>!e.bullish)||today.findLast(e=>e.type!=='Support')||today.at(-1);
    active={event,index:p.index};
   }
   if(!active||p.index-active.index>=3||(!today.length&&!original))continue;
   const event=active.event,direction=event.bullish?'buy':'sell',evidence={event,direction,confirmationDate:event.confirmAt,confirmationClose:model.points[active.index].close};
   let action='unchanged',reason='',signal=original;
   if(!valid(p.volumeRatio)){reason='当前日有效量比不足，保留原策略，不用该证据修改信号。'}
   else if(original?.type===direction){
    if(original.filtered){reason='方向一致，但原三因子过滤仍生效。'}
    else {action='supported';reason=original.grade==='C'?'量价方向一致，仍按原规则跳过C级。':'量价方向一致，保留原信号。';signal={...original,wyckoff:evidence,reason:original.reason+'；威科夫量价支持'};}
   }else if(direction==='sell'&&original?.type==='buy'&&!today.length){
    if(!original.filtered){action='filtered';reason='近3个收盘观察日内已有偏弱验证，过滤反向笑脸。';signal={...original,filtered:true,filterReason:'威科夫偏弱，过滤笑脸',reason:original.reason+'；'+reason};}
    else reason='原笑脸已被三因子过滤。';
   }else if(direction==='buy'&&original?.type==='sell'){reason='出现偏强证据，但保留原哭脸，不阻挡退出。';}
   else if(today.length){
    action=original?.type==='buy'&&!original.filtered?'replaced':'added';
    reason=action==='replaced'?'同日偏弱验证优先，移除原笑脸并新增哭脸。':direction==='buy'?'偏强事件当日获收盘验证，新增B级笑脸。':'偏弱事件当日获收盘验证，新增强哭脸。';
    signal={i:p.index,date:p.date,type:direction,grade:direction==='buy'?'B':'strong',reason,filtered:false,price:p.close,vr:p.volumeRatio,pos:p.closePosition,score:null,provisional:false,origin:'wyckoff',wyckoff:evidence};
   }else continue;
   if(signal)byDate.set(p.date,signal);
   rows.push({date:p.date,...evidence,action,reason,signal:signal||null,events:today.length?today:[event]});
  }
  return {signals:[...byDate.values()].sort((a,b)=>a.date.localeCompare(b.date)),rows,model};
 }
 const api={analyze,integrate,RULES,LABELS};if(typeof module==='object'&&module.exports)module.exports=api;else root.WyckoffEngine=api;
})(typeof globalThis!=='undefined'?globalThis:this);
