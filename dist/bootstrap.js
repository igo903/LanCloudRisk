function render(){destroyResearchChart();renderShell();const pages={overview,market,watchlist,research,sentiment,valuation,factors,backtest,review,data:dataCenter};$('#main').innerHTML=`<div class="loading-free">${banner()}${(pages[state.page]||overview)()}</div>`;mountResearchChart()}
$('#global-search').addEventListener('input',e=>{state.query=e.target.value;state.page='watchlist';state.filter='all';render()});
$('#dialog').addEventListener('click',e=>{if(e.target===$('#dialog')){const r=$('#dialog').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('#dialog').close()}});
if(NAV.some(n=>n[0]===location.hash.slice(1)))state.page=location.hash.slice(1);
if(hasLocalServer)mode='local';
render();
if(hasLocalServer)connectLocal();
