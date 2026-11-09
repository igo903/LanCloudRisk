'use strict';
const WY_TRIANGLE='<span class="wy-triangle" aria-label="威科夫量价支持">▲</span>';
const WY_ACTIONS={added:'新增信号',supported:'原信号获支持',filtered:'过滤笑脸',replaced:'移除笑脸，新增哭脸',unchanged:'原策略不变'};
const WY_STATUSES={pending:'待验证',validated:'已验证',failed:'已失效',unconfirmed:'3根内未验证'};
function wyckoffEventReason(e){
 const level=num(e.level,3),close=num(e.close,3),low=num(e.low,3),high=num(e.high,3),prior=`前${e.window}日`;
 const reasons={
  Support:`${prior}支撑 ${level} 有 ${e.touches} 次分散触及。本日低点 ${low} 接近支撑，下影承接后收于 ${close}。`,
  Spring:`低点 ${low} 跌穿${prior}支撑 ${level}，收盘 ${close} 收回区间。`,
  SOS:`放量扩幅，收盘 ${close} 突破${prior}阻力 ${level}，收在当日振幅上部。`,
  LPS:`${e.parent} 出现 SOS。低点 ${low} 缩量回踩原阻力 ${level}，收盘 ${close} 守住该位。`,
  UT:`高点 ${high} 上穿${prior}阻力 ${level}，收盘 ${close} 回落至区间内。`,
  SOW:`放量扩幅，收盘 ${close} 跌穿${prior}支撑 ${level}，收在当日振幅下部。`,
  Test:`${e.parent} 出现 Spring。低点 ${low} 高于弹簧低点，缩量测试支撑 ${level} 后收于 ${close}。`
 };
 return `${reasons[e.type]} 量比 ${num(e.volumeRatio)}×，振幅比 ${num(e.spreadRatio)}×，收盘位置 ${num(e.closePosition*100,0)}%。`;
}
function wyckoffObservation(e){
 if(e.parentFailed)return `${e.knownAt} 前置形态已失效。`;
 const pass=`收盘${e.bullish?'高于':'低于'} ${num(e.trigger,3)}`;
 if(e.status==='validated')return `${e.confirmAt} 收盘 ${num(e.observationClose,3)} ${e.bullish?'高于':'低于'}验证价 ${num(e.trigger,3)}。`;
 if(e.status==='failed')return `${e.knownAt} 收盘 ${num(e.observationClose,3)} ${e.bullish?'跌破':'升破'}失效价 ${num(e.invalidLevel,3)}。`;
 const conditions=e.parent?`前置形态通过验证，且${pass}`:pass;
 return e.status==='unconfirmed'?`${e.knownAt} 观察期结束，验证条件未达成（${conditions}）。`:`观察接下来最多3根收盘线，验证条件为${conditions}。`;
}
function backtestSignalTable(r){
 const adjustments=new Map((r.wyckoff?.rows||[]).map(row=>[row.date,row]));
 const rows=r.signals.map(s=>{
  const event=adjustments.get(s.date)?.event;
  const reason=event?`<div class="table-sub">${esc(event.label)} · ${esc(event.date)}<br>${esc(wyckoffEventReason(event))} ${esc(wyckoffObservation(event))}</div>`:'';
  return `<tr><td>${esc(s.date)} 收盘</td><td>${face(s)} ${s.wyckoff?WY_TRIANGLE:''}</td><td class="num">${num(s.pos)} / ${num(s.vr)}</td><td class="num">${num(s.score)}${s.origin==='wyckoff'?'（威科夫新增）':s.score===null?'（资金缺失）':''}</td><td class="wy-evidence-text">${esc(s.reason)}${reason}</td></tr>`;
 }).join('');
 return `<div class="table-wrap"><table class="wy-backtest-table"><thead><tr><th>形成时间</th><th>信号</th><th>POS / 量比</th><th>三因子分</th><th>触发依据</th></tr></thead><tbody>${rows||'<tr><td colspan="5" class="empty">当前区间没有笑哭脸研究信号。</td></tr>'}</tbody></table></div>`;
}
function wyckoffResearchTable(r){
 const model=r.wyckoff?.model||WyckoffEngine.analyze(E.series(data(),asset().code),{end:bt.end});
 const events=model.events.filter(e=>e.date>=bt.start&&e.date<=bt.end).reverse();
 const rows=events.map(e=>`<tr><td><button class="text-button" onclick="focusBacktestDate('${esc(e.date)}')" aria-label="定位量价形态 ${esc(e.date)}">${esc(e.date)}</button></td><td>${esc(e.label)}<div class="table-sub">${e.bullish?'偏强':'偏弱'}</div></td><td>${pill(WY_STATUSES[e.status],e.status==='validated'?'good':e.status==='failed'?'warn':'neutral')}<div class="table-sub">${e.knownAt!==e.date?esc(e.knownAt):'—'}</div></td><td class="wy-evidence-text">${esc(wyckoffEventReason(e))} ${esc(wyckoffObservation(e))}</td></tr>`).join('');
 const message=model.ready?'当前区间没有识别到符合规则的量价形态。':model.reason;
 return `<div class="panel-head"><h2>威科夫量价形态与理由</h2><span class="small muted">${bt.wyckoff?'因子已启用':'形态观察 · 因子已关闭'}</span></div><div class="table-wrap"><table class="wy-backtest-table"><thead><tr><th>形态日</th><th>形态与方向</th><th>状态 / 观察日</th><th>触发理由与验证条件</th></tr></thead><tbody>${rows||`<tr><td colspan="4" class="empty">${esc(message)}</td></tr>`}</tbody></table></div>${model.warnings.length?`<div class="panel-note">${esc(model.warnings.join(' '))}</div>`:''}`;
}
function wyckoffFactorControl(){
 return '<label class="check-row"><input type="checkbox" id="bt-wyckoff" '+(bt.wyckoff?'checked':'')+' onchange="toggleBacktestWyckoff(this.checked)">威科夫量价因子</label><p class="small muted wy-control-note">按收盘验证新增或过滤信号；'+WY_TRIANGLE+' 标记获量价支持的笑哭脸。</p>';
}
function toggleBacktestWyckoff(enabled){
 const old=document.getElementById('bt-wyckoff'),top=old?.getBoundingClientRect().top;
 bt.wyckoff=!!enabled;render();
 const current=document.getElementById('bt-wyckoff');
 if(current&&Number.isFinite(top))window.scrollBy(0,current.getBoundingClientRect().top-top);
 current?.focus({preventScroll:true});
}
function wyckoffEvidencePanel(r){
 const explanation='<details class="wy-rule"><summary>查看威科夫信号规则</summary><p>优先检查前40根收盘K线的区间，再检查前20根的局部结构。穿越幅度随近期真实波幅调整，量比与振幅比使用此前20根。形态候选、失效与未验证记录可在「研究信号」查看。候选出现后最多观察3根收盘K线，验证日须有有效量比。</p><p>确认当天可新增B级笑脸或强哭脸。同向原信号保留并加蓝色三角；偏弱验证可替换当日笑脸，或过滤随后两根收盘K线的反向笑脸。新验证覆盖旧方向，同日冲突偏弱优先。原哭脸不被偏强证据取消；三因子已过滤的买入、C级跳过及六ETF买入门槛继续生效。</p><p>候选日与验证日分别展示。新增信号从验证日收盘后生效，按所选模型在下一根已收盘日线模拟成交。盘中、未验证和已失效事件不产生因子交易。数值阈值是研究规则。</p></details>';
 const w=r.wyckoff;
 if(!bt.wyckoff||!w)return panel('威科夫量价 · 买卖信号明细','<div class="panel-body"><p class="small muted">勾选研究参数中的“威科夫量价因子”，查看逐日买卖证据和信号变更。</p>'+explanation+'</div>','','wy-backtest-panel');
 const counts={added:0,filtered:0,supported:0};
 for(const row of w.rows){if(row.action==='replaced'){counts.added++;counts.filtered++;}else if(row.action in counts)counts[row.action]++;}
 const orders=new Map((r.orders||[]).map(o=>[o.signal,o]));
 const rows=w.rows.slice().reverse().map(row=>{
  const e=row.event,order=orders.get(row.date);
  const name=row.direction==='buy'?'偏强 · 买入依据':'偏弱 · 卖出依据';
  const status=order?esc(order.status)+' · '+esc(order.reason):row.signal?.wyckoff?'未生成次日订单（区间末日或无后续收盘）':'—';
  return '<tr><td><button class="text-button" onclick="focusBacktestDate(\''+esc(row.date)+'\')" aria-label="定位威科夫信号 '+esc(row.date)+'">'+esc(row.date)+'</button></td><td>'+esc(name)+'<div class="table-sub">'+esc(e.label)+'</div></td><td>'+esc(e.date)+'<div class="table-sub">验证 '+esc(row.confirmationDate)+'</div></td><td>'+pill(WY_ACTIONS[row.action],row.action==='filtered'||row.action==='replaced'?'warn':'neutral')+(row.signal?.wyckoff?' '+WY_TRIANGLE:'')+'<div class="table-sub">'+status+'</div></td><td class="wy-evidence-text">'+esc(wyckoffEventReason(e))+' '+esc(wyckoffObservation(e))+(row.events.length>1?' 同日确认：'+row.events.map(x=>esc(x.type)+' '+esc(x.date)).join('、')+'。':'')+' '+esc(row.reason)+'</td></tr>';
 }).join('');
 return panel('威科夫量价 · 买卖信号明细','<div class="panel-body"><div class="wy-counts">'+pill('新增 '+counts.added,'good')+' '+pill('移除 / 过滤 '+counts.filtered,'warn')+' '+pill('同向支持 '+counts.supported)+' <span class="small muted">按当前回测区间统计 · 点击日期定位K线</span></div>'+(!w.model.ready?'<p class="small muted">'+esc(w.model.reason)+'</p>':'')+explanation+'</div><div class="table-wrap"><table class="wy-backtest-table"><thead><tr><th>信号日</th><th>量价方向 / 事件</th><th>候选日 / 验证日</th><th>策略处理 / 模拟订单</th><th>量价证据与理由</th></tr></thead><tbody>'+(rows||'<tr><td colspan="5" class="empty">本区间没有可用于调整信号的已验证事件。</td></tr>')+'</tbody></table></div>','','wy-backtest-panel');
}
