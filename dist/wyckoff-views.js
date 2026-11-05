'use strict';
const WY_TRIANGLE='<span class="wy-triangle" aria-label="威科夫量价支持">▲</span>';
const WY_ACTIONS={added:'新增信号',supported:'原信号获支持',filtered:'过滤笑脸',replaced:'移除笑脸，新增哭脸',unchanged:'原策略不变'};
const WY_EVENT_REASONS={Spring:'跌破前区间下沿后收回，收盘位于当日振幅上部40%。',UT:'突破前区间上沿后回落，收盘位于当日振幅下部40%。',SOS:'放量宽幅突破前区间上沿，收盘位于振幅上部30%。',SOW:'放量宽幅跌破前区间下沿，收盘位于振幅下部30%。',Test:'弹簧后在支撑附近缩量窄幅测试，未跌破弹簧低点。',LPS:'强势突破后缩量回测原阻力，收盘仍守住该位置。'};
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
 const explanation='<details class="wy-rule"><summary>查看威科夫信号规则</summary><p>区间取前40根收盘K线，量比与振幅比以此前20根为基准。弹簧、缩量测试、支撑回测和强势突破属于偏强候选；上冲回落、弱势跌破属于偏弱候选。候选出现后最多观察3根收盘K线，只有越过该事件的验证价才进入因子。验证日的量比须可用。</p><p>确认当天可新增B级笑脸或强哭脸。同向原信号保留并加蓝色三角；偏弱验证可替换当日笑脸，或过滤随后两根收盘K线的反向笑脸。新验证覆盖旧方向，同日冲突偏弱优先。原哭脸不被偏强证据取消；三因子已过滤的买入、C级跳过及六ETF买入门槛继续生效。</p><p>候选日与验证日分别展示。新增信号从验证日收盘后生效，按所选模型在下一根已收盘日线模拟成交。盘中、未验证和已失效事件不产生因子交易。数值阈值是研究规则。</p></details>';
 const w=r.wyckoff;
 if(!bt.wyckoff||!w)return panel('威科夫量价 · 买卖信号明细','<div class="panel-body"><p class="small muted">勾选研究参数中的“威科夫量价因子”，查看逐日买卖证据和信号变更。</p>'+explanation+'</div>','','wy-backtest-panel');
 const counts={added:0,filtered:0,supported:0};
 for(const row of w.rows){if(row.action==='replaced'){counts.added++;counts.filtered++;}else if(row.action in counts)counts[row.action]++;}
 const orders=new Map((r.orders||[]).map(o=>[o.signal,o]));
 const rows=w.rows.slice().reverse().map(row=>{
  const e=row.event,trigger=e.type==='SOS'||e.type==='SOW'?e.close:e.bullish?e.high:e.low,order=orders.get(row.date);
  const name=row.direction==='buy'?'偏强 · 买入依据':'偏弱 · 卖出依据';
  const status=order?esc(order.status)+' · '+esc(order.reason):row.signal?.wyckoff?'未生成次日订单（区间末日或无后续收盘）':'—';
  return '<tr><td><button class="text-button" onclick="focusBacktestDate(\''+esc(row.date)+'\')" aria-label="定位威科夫信号 '+esc(row.date)+'">'+esc(row.date)+'</button></td><td>'+esc(name)+'<div class="table-sub">'+esc(e.type)+' · '+esc(e.label)+'</div></td><td>'+esc(e.date)+'<div class="table-sub">验证 '+esc(row.confirmationDate)+'</div></td><td>'+pill(WY_ACTIONS[row.action],row.action==='filtered'||row.action==='replaced'?'warn':'neutral')+(row.signal?.wyckoff?' '+WY_TRIANGLE:'')+'<div class="table-sub">'+status+'</div></td><td class="wy-evidence-text">'+esc(WY_EVENT_REASONS[e.type])+' 候选量比 '+num(e.volumeRatio)+'×、振幅比 '+num(e.spreadRatio)+'×；验证收盘 '+num(row.confirmationClose,3)+(e.bullish?' 高于 ':' 低于 ')+num(trigger,3)+'。'+(row.events.length>1?' 同日确认：'+row.events.map(x=>esc(x.type)+' '+esc(x.date)).join('、')+'。':'')+' '+esc(row.reason)+'</td></tr>';
 }).join('');
 return panel('威科夫量价 · 买卖信号明细','<div class="panel-body"><div class="wy-counts">'+pill('新增 '+counts.added,'good')+' '+pill('移除 / 过滤 '+counts.filtered,'warn')+' '+pill('同向支持 '+counts.supported)+' <span class="small muted">按当前回测区间统计 · 点击日期定位K线</span></div>'+(!w.model.ready?'<p class="small muted">'+esc(w.model.reason)+'</p>':'')+explanation+'</div><div class="table-wrap"><table class="wy-backtest-table"><thead><tr><th>信号日</th><th>量价方向 / 事件</th><th>候选日 / 验证日</th><th>策略处理 / 模拟订单</th><th>量价证据与理由</th></tr></thead><tbody>'+(rows||'<tr><td colspan="5" class="empty">本区间没有可用于调整信号的已验证事件。</td></tr>')+'</tbody></table></div>','','wy-backtest-panel');
}
