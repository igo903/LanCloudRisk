/* Rule-based Wyckoff research proxies, v1.0. No order execution or probability model. */
(function(root){
 'use strict';
 const valid=Number.isFinite,mean=a=>a.reduce((s,x)=>s+x,0)/a.length;
 const LABELS={Spring:'弹簧候选',UT:'上冲回落候选',SOS:'强势突破候选',SOW:'弱势跌破候选',Test:'缩量测试候选',LPS:'支撑回测候选'};
 const RULES={range:40,volume:20,confirm:3,breakout:.005,highVolume:1.5,wideSpread:1.2,lowVolume:.8,maxRange:.35};
 function analyze(input,{end='',range=40}={}){
  range=[20,40,60].includes(Number(range))?Number(range):40;
  const base={ready:false,reason:'至少需要61根有效收盘日线。',bars:[],points:[],events:[],latest:null,live:[],range,rules:{...RULES,range},warnings:[]};
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
   const b=closed[i],past=closed.slice(Math.max(0,i-range),i),volumePast=closed.slice(Math.max(0,i-20),i);
   const volumesReady=volumePast.length===20&&volumePast.every(x=>valid(x.vol)&&x.vol>0)&&valid(b.vol)&&b.vol>0;
   const avgVolume=volumesReady?mean(volumePast.map(x=>x.vol)):null,avgSpread=volumePast.length===20?mean(volumePast.map(x=>x.high-x.low)):null;
   const volumeRatio=avgVolume?b.vol/avgVolume:null,spreadRatio=avgSpread>0?(b.high-b.low)/avgSpread:null,closePosition=b.high>b.low?(b.close-b.low)/(b.high-b.low):.5;
   const support=past.length===range?Math.min(...past.map(x=>x.low)):null,resistance=past.length===range?Math.max(...past.map(x=>x.high)):null;
   const p={...b,index:i,ma20:i>=19?mean(closed.slice(i-19,i+1).map(x=>x.close)):null,ma60:i>=59?mean(closed.slice(i-59,i+1).map(x=>x.close)):null,support,resistance,volumeRatio,spreadRatio,closePosition,events:[]};
   points.push(p);if(i<60||!support||!volumesReady)continue;
   const width=(resistance/support-1),inRange=width<=RULES.maxRange;
   let type=null,level=null,parent=null;
   if(inRange&&b.low<support*(1-RULES.breakout)&&b.close>support&&b.close<resistance&&closePosition>=.6){type='Spring';level=support}
   else if(inRange&&b.high>resistance*(1+RULES.breakout)&&b.close<resistance&&b.close>support&&closePosition<=.4){type='UT';level=resistance}
   else if(b.close>resistance*(1+RULES.breakout)&&b.close>b.open&&volumeRatio>=RULES.highVolume&&spreadRatio>=RULES.wideSpread&&closePosition>=.7){type='SOS';level=resistance}
   else if(b.close<support*(1-RULES.breakout)&&b.close<b.open&&volumeRatio>=RULES.highVolume&&spreadRatio>=RULES.wideSpread&&closePosition<=.3){type='SOW';level=support}
   else {
    const recent=events.filter(e=>i-e.index>=1&&i-e.index<=10);
    const spring=recent.filter(e=>e.type==='Spring').at(-1),sos=recent.filter(e=>e.type==='SOS').at(-1);
    if(spring&&closed.slice(spring.index+1,i+1).every(x=>x.close>=spring.low)&&b.low>spring.low&&b.low<=spring.level*1.02&&b.close>=spring.level&&volumeRatio<RULES.lowVolume&&b.vol<spring.volume&&spreadRatio<1&&closePosition>=.5){type='Test';level=spring.level;parent=spring.date}
    else if(sos&&closed.slice(sos.index+1,i+1).every(x=>x.close>=sos.level*.98)&&b.low>=sos.level*.98&&b.low<=sos.level*1.02&&b.close>=sos.level&&volumeRatio<RULES.lowVolume&&spreadRatio<1){type='LPS';level=sos.level;parent=sos.date}
   }
   if(type){
    const bullish=['Spring','SOS','Test','LPS'].includes(type),e={id:b.date+'-'+type,date:b.date,index:i,type,label:LABELS[type],bullish,level,parent,low:b.low,high:b.high,close:b.close,volume:b.vol,volumeRatio,spreadRatio,closePosition,status:'pending',knownAt:b.date,confirmAt:null};
    events.push(e);p.events.push(e.id);
   }
  }
  // Confirmation is a separate dated observation, never an event backdated to a later bar.
  for(const e of events){
   const after=closed.slice(e.index+1,e.index+1+RULES.confirm);
   for(const b of after){
    const fail=e.bullish?b.close<e.low:b.close>e.high;
    const pass=e.type==='SOS'?b.close>e.close:e.type==='SOW'?b.close<e.close:e.bullish?b.close>e.high:b.close<e.low;
    if(fail||pass){e.status=fail?'failed':'validated';e.confirmAt=b.date;e.knownAt=b.date;break}
   }
   if(e.status==='pending'&&after.length===RULES.confirm){e.status='unconfirmed';e.knownAt=after.at(-1).date}
  }
  const latest=points.at(-1)||null,ready=closed.length>=Math.max(61,range+1);
  const recent=closed.slice(-20),directions=recent.map((b,j)=>({b,prev:closed[closed.length-recent.length+j-1]}));
  const volumeReady=recent.length===20&&directions.every(({b,prev})=>prev&&valid(b.vol)&&b.vol>0);
  const up=volumeReady?directions.filter(x=>x.b.close>x.prev.close).reduce((s,x)=>s+x.b.vol,0):null;
  const down=volumeReady?directions.filter(x=>x.b.close<x.prev.close).reduce((s,x)=>s+x.b.vol,0):null;
  const upDownRatio=volumeReady&&down>0?up/down:null;
  const ret20=closed.length>20?(latest.close/closed.at(-21).close-1)*100:null;
  const slope=points.length>=65&&latest.ma60&&points.at(-6)?.ma60?(latest.ma60/points.at(-6).ma60-1)*100:null;
  let phase='样本不足',tone='neutral';
  if(ready){
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
    const event=today.filter(e=>!e.bullish).at(-1)||today.at(-1);
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
