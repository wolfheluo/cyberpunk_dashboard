// ============================================================
// I18n
// ============================================================
var I18n=(function(){var l='en',d={},r=false,q=[];
function L(ln){return fetch('/dashboard/i18n/'+ln+'.json').then(function(r){return r.json();}).then(function(dd){d[ln]=dd;});}
function T(k,p){var dd=d[l]||d['en']||{},s=dd[k]||(d['en']&&d['en'][k])||k;if(p){for(var kk in p)s=s.split('{'+kk+'}').join(p[kk]);}return s;}
function B(){var es=document.querySelectorAll('[data-i18n]');for(var i=0;i<es.length;i++){var e=es[i],k=e.getAttribute('data-i18n');if(k)e.textContent=T(k);}}
function I(){return Promise.all([L('en'),L('zh')]).then(function(){r=true;B();q.forEach(function(f){f();});q=[];});}
return{init:I,t:T,setLang:function(ln){l=ln;document.getElementById('btnEN').className='lang-btn'+(ln==='en'?' active':'');document.getElementById('btnZH').className='lang-btn'+(ln==='zh'?' active':'');B();if(typeof R==='function')R();},ready:function(f){if(r)f();else q.push(f);}};})();

// ============================================================
// SIDEBAR & PAGES
// ============================================================
var currentPage='dashboard';
function toggleSidebar(){document.getElementById('sidebar').classList.toggle('collapsed');}
function switchPage(page){
  currentPage=page;
  document.querySelectorAll('.nav-item').forEach(function(el){el.classList.toggle('active',el.getAttribute('data-page')===page);});
  document.querySelectorAll('.page').forEach(function(el){el.classList.toggle('active',el.id==='page-'+page);});
  if(page==='strategies')loadStrategyList();
  if(page==='backtest')runBacktest();if(page==='account')loadAccount();if(page==='watchlist')loadSymbols();
}

// ============================================================
// DATA
// ============================================================
var DATA={connected:false,latency_ms:null,tickers:[],strategy_matrix:{strategies:[],timeframes:[],cells:[]},kpi:{},factors:[],exec_log:[],active_strategy:'',positions:[],trades:[],executed:[],rejected:[],failed:[]};
var strategiesList=[],activeStratFile='',editingFile='';

// ============================================================
// STRATEGY LOADER
// ============================================================
function loadStrategies(){
  fetch('/api/strategies').then(function(r){return r.json();}).then(function(d){
    strategiesList=d.strategies;activeStratFile=d.active;loadActiveJSStrategy();
    var as=document.getElementById('acctStrategySelect');if(as){as.innerHTML='<option value="">No strategy selected</option>';for(var j=0;j<strategiesList.length;j++){var ss=strategiesList[j];as.innerHTML+='<option value="'+ss.filename+'"'+(ss.filename===d.active?' selected':'')+'>'+ss.name+'</option>';}}
  });
}
function activateStrategy(fname){
  if(!fname||fname===activeStratFile)return;
  fetch('/api/strategy/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:fname})})
    .then(function(r){return r.json();}).then(function(d){activeStratFile=d.active;loadActiveJSStrategy();});
}

// ============================================================
// STRATEGY EDITOR
// ============================================================
function loadStrategyList(){
  var el=document.getElementById('strategyList');el.innerHTML='';
  strategiesList.forEach(function(s){
    var isActive=s.filename===activeStratFile;
    el.innerHTML+='<div class="panel p-2 hover:border-[#00E5FF40] cursor-pointer" style="border-color:'+(isActive?'#00E5FF':'')+'" onclick="openStrategy(\''+s.filename+'\')">'+
      '<div class="flex justify-between items-center"><span class="text-[15px] '+(isActive?'text-[#00FF66]':'text-[#00E5FF]')+'">'+s.name+'</span>'+(isActive?'<span class="badge-green">ACTIVE</span>':'')+'</div>'+
      '<div class="flex justify-between items-center mt-0.5"><div class="text-[15px] text-[#5A6275]">'+s.description+'</div><button class="btn-sm danger" onclick="event.stopPropagation();deleteStrategy(\''+s.filename+'\')">✕</button></div></div>';
  });
  if(!strategiesList.length)el.innerHTML='<div class="text-[#5A6275]">No strategies found</div>';
}
function openStrategy(fname){
  editingFile=fname;
  fetch('/api/strategy/'+fname+'/code').then(function(r){return r.json();}).then(function(d){
    document.getElementById('editorTitle').textContent=d.name+' ('+d.filename+')';
    document.getElementById('codeEditor').value=d.code;document.getElementById('codeEditor').disabled=false;
    document.getElementById('btnSave').disabled=false;
    document.getElementById('editorStatus').textContent='Editing: '+d.filename;
  });
}
function createStrategy(){
  document.getElementById('stratModal').style.display='flex';
  var inp=document.getElementById('stratNameInput');
  inp.value='new_strategy.js';
  setTimeout(function(){inp.focus();inp.select();},50);
}
function closeStratModal(){document.getElementById('stratModal').style.display='none';}
function confirmCreateStrategy(){
  var name=document.getElementById('stratNameInput').value.trim();
  if(!name)return;
  closeStratModal();
  fetch('/api/strategy/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:name})})
    .then(function(r){return r.json();}).then(function(d){
      if(d.error){alert(d.error);return;}
      loadStrategies();loadStrategyList();openStrategy(d.filename);
    });
}
document.getElementById('stratNameInput').addEventListener('keydown',function(e){
  if(e.key==='Enter')confirmCreateStrategy();
  if(e.key==='Escape')closeStratModal();
});
function deleteStrategy(fname){
  if(fname===activeStratFile){alert('Cannot delete active strategy. Switch first.');return;}
  if(!confirm('Delete '+fname+'?'))return;
  fetch('/api/strategy/'+fname+'/delete',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    loadStrategies();loadStrategyList();
    if(editingFile===fname){document.getElementById('codeEditor').value='';document.getElementById('codeEditor').disabled=true;document.getElementById('btnSave').disabled=true;document.getElementById('editorTitle').textContent='Select a strategy';editingFile='';}
  });
}
function saveStrategy(){
  if(!editingFile)return;
  var code=document.getElementById('codeEditor').value;
  fetch('/api/strategy/'+editingFile+'/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:code})})
    .then(function(r){return r.json();}).then(function(d){
      document.getElementById('editorStatus').textContent=d.error?'ERROR: '+d.error:'Saved — '+d.name;
      document.getElementById('editorStatus').style.color=d.error?'#FF2A6D':'#00FF66';
      if(!d.error){
        loadStrategies();
        // Hot-reload: re-eval all tickers with new strategy
        if(editingFile===activeStratFile){
          eval('activeJSStrategy='+code.replace(/\\n/g,'\\n'));
          for(var i=0;i<DATA.tickers.length;i++){
            var s=evaluateJSStrategy(DATA.tickers[i]);
            DATA.tickers[i].signal=s.signal;DATA.tickers[i].confidence=s.confidence;
          }
          if(DATA.tickers.length&&currentPage==='dashboard')R(false);
        }
      }
    }).catch(function(e){document.getElementById('editorStatus').textContent='ERROR: '+e.message;document.getElementById('editorStatus').style.color='#FF2A6D';});
}

// ============================================================
// BACKTEST
// ============================================================
var btData=[];
function runBacktest(){
  var st=document.getElementById('backtestStatus');
  st.textContent=I18n.t('computing');
  st.className='text-[15px] text-[#FFCC00] mb-2';
  var start=Date.now();
  fetch('/api/backtest/run',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    if(d.error){st.textContent='ERROR: '+d.error;st.className='text-[15px] text-[#FF2A6D] mb-2';return;}
    btData=d.backtests||[];
    var dur=((Date.now()-start)/1000).toFixed(1);
    st.textContent=I18n.t('done_in')+' '+dur+'s \u2014 '+btData.length+' '+I18n.t('results');
    st.className='text-[15px] text-[#00FF66] mb-2';

    // Group by strategy
    var groups={},strategyOrder=[];
    for(var i=0;i<btData.length;i++){
      var b=btData[i],s=b.strategy;
      if(!groups[s]){groups[s]=[];strategyOrder.push(s);}
      groups[s].push(b);
    }
    // Sort each group by return desc
    for(var s in groups){groups[s].sort(function(a,b){return b.total_return_pct-a.total_return_pct;});}

    var el=document.getElementById('backtestSummary');
    el.innerHTML='';
    for(var gi=0;gi<strategyOrder.length;gi++){
      var sn=strategyOrder[gi],items=groups[sn],avgRet=0,minRet=Infinity,maxRet=-Infinity;
      for(var j=0;j<items.length;j++){avgRet+=items[j].total_return_pct;if(items[j].total_return_pct<minRet)minRet=items[j].total_return_pct;if(items[j].total_return_pct>maxRet)maxRet=items[j].total_return_pct;}
      avgRet/=items.length;
      var aCls=avgRet>=0?'up':'down',aSgn=avgRet>=0?'+':'',mCls=maxRet>=0?'text-[#00FF66]':'text-[#FF2A6D]',mnCls=minRet>=0?'text-[#00FF66]':'text-[#FF2A6D]';

      var div=document.createElement('div');div.className='panel mb-1';
      var avgEq=0;for(var j=0;j<items.length;j++)avgEq+=items[j].final_equity;avgEq/=items.length;
      var eCls=avgEq>=10000?'text-[#00FF66]':'text-[#FF2A6D]',eSgn=avgEq>=10000?'+':'';
      div.innerHTML='<div class="flex items-center justify-between p-2 cursor-pointer hover:bg-[#0F1117]" onclick="toggleBTGroup(this)" style="user-select:none">'+
        '<span class="bt-arrow">\u25b6</span> <span class="text-[#00E5FF] text-[15px]">'+sn+'</span>'+
        '<span class="text-[15px]"><span class="'+aCls+'">Avg: '+aSgn+avgRet.toFixed(1)+'%</span> <span class="text-[#5A6275]">|</span> <span class="'+eCls+'">$'+avgEq.toFixed(0)+'</span> <span class="text-[#5A6275]">|</span> Best: <span class="'+mCls+'">'+items[0].symbol+' '+(items[0].total_return_pct>=0?'+':'')+items[0].total_return_pct.toFixed(1)+'%</span> <span class="text-[#5A6275]">|</span> '+items.length+' symbols</span></div>'+
        '<div class="hidden bt-detail p-3"><div class="text-[#00E5FF] text-[15px] mb-2">'+sn+' — '+items.length+' symbols, $10,000 initial</div><table class="bt-table w-full">'+
        '<thead><tr><th class="w-24">Symbol</th><th class="w-20">Return</th><th class="w-28">Final Equity</th><th class="w-24">Trades</th><th>vs $10k</th></tr></thead><tbody>'+
        items.map(function(b){var cl=b.total_return_pct>=0?'up':'down',sn=b.total_return_pct>=0?'+':'',barW=Math.min(100,Math.abs(b.total_return_pct)*3),barCl=b.total_return_pct>=0?'#00E5FF':'#FF2A6D';return '<tr><td class="text-[#00E5FF]">'+b.symbol+'</td><td class="'+cl+'">'+sn+b.total_return_pct+'%</td><td>$'+b.final_equity.toLocaleString()+'</td><td class="text-[#5A6275]">'+b.trades_count+'</td><td><span style="display:inline-block;background:'+barCl+'20;height:6px;width:'+barW+'px;border-radius:3px"></span></td></tr>';}).join('')+
        '</tbody></table></div>';
      el.appendChild(div);
    }
    drawBTCanvas();
  }).catch(function(e){st.textContent='ERROR: '+e.message;st.className='text-[15px] text-[#FF2A6D] mb-2';});
}
function toggleBTGroup(header){
  var detail=header.nextElementSibling,arrow=header.querySelector('.bt-arrow');
  if(detail.classList.contains('hidden')){detail.classList.remove('hidden');arrow.textContent='\u25bc';}
  else{detail.classList.add('hidden');arrow.textContent='\u25b6';}
}
function drawBTCanvas(){
  var c=document.getElementById('btChart'),p=c.parentElement;
  c.width=p.clientWidth-8;c.height=p.clientHeight-8;
  var ctx=c.getContext('2d'),w=c.width,h=c.height;
  ctx.clearRect(0,0,w,h);
  if(!btData.length){ctx.fillStyle='#5A6275';ctx.font='12px monospace';ctx.textAlign='center';ctx.fillText(I18n.t('no_historical'),w/2,h/2);ctx.textAlign='start';return;}

  // Find min/max equity across all curves
  var allEq=[],allDates=[];
  btData.forEach(function(b){allEq=allEq.concat(b.equity_curve||[]);allDates=allDates.concat(b.dates||[]);});
  if(!allEq.length){ctx.fillStyle='#5A6275';ctx.font='12px monospace';ctx.textAlign='center';ctx.fillText(I18n.t('no_equity_data'),w/2,h/2);ctx.textAlign='start';return;}
  var eqMin=Math.min.apply(null,allEq),eqMax=Math.max.apply(null,allEq),eqRng=eqMax-eqMin||1;
  var pad=40;

  // Axes
  ctx.strokeStyle='#1E222D';ctx.lineWidth=0.5;
  ctx.beginPath();ctx.moveTo(pad,pad);ctx.lineTo(pad,h-pad);ctx.lineTo(w-10,h-pad);ctx.stroke();
  // Y labels
  ctx.fillStyle='#5A6275';ctx.font='11px monospace';ctx.textAlign='right';
  for(var i=0;i<=4;i++){var y=h-pad-(h-2*pad)*i/4,val=eqMin+eqRng*i/4;ctx.fillText('$'+(val/1000).toFixed(0)+'k',pad-4,y+3);}
  // Baseline $10k
  var baseY=h-pad-(h-2*pad)*(10000-eqMin)/eqRng;
  if(baseY>pad&&baseY<h-pad){ctx.strokeStyle='#5A6275';ctx.setLineDash([3,5]);ctx.beginPath();ctx.moveTo(pad,baseY);ctx.lineTo(w-10,baseY);ctx.stroke();ctx.setLineDash([]);}

  // Draw curves
  var colors=['#00E5FF','#00FF66','#FFCC00','#FF2A6D','#c0c8d8'];
  btData.forEach(function(b,bi){
    var eq=b.equity_curve||[],dts=b.dates||[],clr=colors[bi%colors.length];
    if(eq.length<2)return;
    ctx.strokeStyle=clr;ctx.lineWidth=1.5;ctx.beginPath();
    for(var j=0;j<eq.length;j++){
      var x=pad+(w-pad-10)*j/(eq.length-1),yV=h-pad-(h-2*pad)*(eq[j]-eqMin)/eqRng;
      j===0?ctx.moveTo(x,yV):ctx.lineTo(x,yV);
    }
    ctx.stroke();
    // Label at end
    ctx.fillStyle=clr;ctx.font='11px monospace';ctx.textAlign='start';
    ctx.fillText(b.strategy.substring(0,12),w-8,h-pad-12-bi*14);
  });
  ctx.textAlign='start';
}

// ============================================================
// DASHBOARD RENDER
// ============================================================
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function R(full){renderConnection();if(full!==false)renderTickerRail();renderSignalTable();renderRadar();renderKPIs();renderPositionsBar();renderPipeline(0);if(!pipeAnimId)animatePipeline();renderSignalSummary();renderExecLog();renderRecentTrades();}

function renderConnection(){
  document.getElementById('latency').innerHTML=DATA.connected&&DATA.latency_ms!=null?DATA.latency_ms+'ms':'--';
  var dot=document.getElementById('liveDot'),st=document.getElementById('liveStatus');
  if(DATA.connected){dot.className='dot-green';st.className='text-[#00FF66]';st.setAttribute('data-i18n','live');st.textContent=I18n.t('live');}
  else{dot.className='dot-cyan';st.className='text-[#00FF66]';st.setAttribute('data-i18n','connecting');st.textContent=I18n.t('connecting');}
  var pill=document.getElementById('tradingPill');pill.className=DATA.connected?'pill-live':'pill-off';
  pill.setAttribute('data-i18n',DATA.connected?'live_trading':'disconnected');pill.textContent=I18n.t(DATA.connected?'live_trading':'disconnected');
}

function renderTickerRail(){
  var rail=document.getElementById('tickerRail');rail.innerHTML='';
  if(!DATA.tickers.length){rail.innerHTML='<div class="text-[#5A6275] text-[15px] p-4">'+I18n.t('no_data')+'</div>';return;}
  DATA.tickers.forEach(function(tk,i){
    var chg=parseFloat(tk.change_pct)||0,sign=chg>=0?'+':'',cc=chg>0?'up':chg<0?'down':'neutral';
    var card=document.createElement('div');card.className='panel p-2 cursor-pointer hover:border-[#00E5FF40] transition-colors';
    card.innerHTML='<div class="flex justify-between items-center mb-1"><div><span class="text-[#00E5FF] text-[15px] font-bold">'+esc(tk.id)+'</span><span class="text-[#5A6275] text-[15px] ml-1.5">'+esc(tk.name||'')+'</span></div><span class="text-[#5A6275] text-[15px]">Vol '+(tk.volume_m!=null?Number(tk.volume_m).toFixed(1)+'M':'--')+'</span></div><canvas class="w-full sparkline" height="20" data-idx="'+i+'" style="width:100%"></canvas><div class="flex justify-between mt-0.5"><span class="text-[15px]">'+(tk.price!=null?'$'+Number(tk.price).toFixed(tk.price<1?4:2):'--')+'</span><span class="'+cc+' text-[15px]">'+sign+Math.abs(chg).toFixed(2)+'%</span></div>';
    rail.appendChild(card);
  });
  setTimeout(drawSparklines,50);
}
function drawSparklines(){
  var cs=document.querySelectorAll('#tickerRail canvas.sparkline');
  for(var c=0;c<cs.length;c++){var can=cs[c],idx=parseInt(can.getAttribute('data-idx'));var sp=DATA.tickers[idx]&&DATA.tickers[idx].sparkline,w=can.parentElement.clientWidth-4;can.width=w;var ctx=can.getContext('2d'),h=can.height;ctx.clearRect(0,0,w,h);if(!sp||!sp.length){ctx.fillStyle='#5A6275';ctx.font='11px monospace';ctx.fillText('...',4,h/2+4);continue;}var pts=[],mn=sp[0],mx=sp[0],j;for(j=0;j<sp.length;j++){if(sp[j]<mn)mn=sp[j];if(sp[j]>mx)mx=sp[j];}var rng=mx-mn||1;for(j=0;j<sp.length;j++)pts.push(h*0.9-((sp[j]-mn)/rng)*h*0.8);var last=pts[pts.length-1],first=pts[0],tr=last<first?'#FF2A6D80':'#00FF6680';ctx.strokeStyle=tr;ctx.lineWidth=1;ctx.beginPath();for(j=0;j<pts.length;j++){var x=(j/(pts.length-1))*w;j===0?ctx.moveTo(x,pts[j]):ctx.lineTo(x,pts[j]);}ctx.stroke();ctx.fillStyle=tr.replace('80','15');ctx.lineTo(w,h);ctx.lineTo(0,h);ctx.closePath();ctx.fill();}
}
function renderSignalTable(){
  var st=document.getElementById('signalTable');st.innerHTML='';
  if(!DATA.tickers.length){st.innerHTML='<div class="text-[#5A6275] text-[15px]">--</div>';return;}
  DATA.tickers.forEach(function(tk){var sk='signal_'+((tk.signal||'wait').toLowerCase()),stx=I18n.t(sk);var b=tk.signal==='BUY'?'badge-green':tk.signal==='SELL'?'badge-red':tk.signal==='WAIT'?'badge-yellow':'badge-cyan';var chg=parseFloat(tk.change_pct)||0,ps=chg>0?'up':chg<0?'down':'neutral';st.innerHTML+='<div class="flex items-center gap-3 py-0.5 border-b border-[#1E222D40]"><span class="text-[#00E5FF] w-14">'+esc(tk.id)+'</span><span class="'+ps+' w-24 text-right">'+(tk.price!=null?'$'+Number(tk.price).toFixed(tk.price<1?4:2):'--')+'</span><span class="'+b+'">'+stx+'</span><span class="text-[#5A6275] text-[15px] ml-auto">'+(tk.confidence!=null?tk.confidence+'%':'--')+'</span></div>';});
}
function renderRadar(){
  var rd=document.getElementById('factorRadar'),rctx=rd.getContext('2d'),cx=105,cy=105,rr=85;rctx.clearRect(0,0,210,210);
  if(!DATA.factors.length){rctx.fillStyle='#5A6275';rctx.font='12px monospace';rctx.textAlign='center';rctx.fillText(I18n.t('loading_factors'),cx,cy);rctx.textAlign='start';return;}
  var nf=DATA.factors.length;for(var i=1;i<=4;i++){rctx.beginPath();rctx.arc(cx,cy,rr*i/4,0,Math.PI*2);rctx.strokeStyle='#1E222D';rctx.lineWidth=0.5;rctx.stroke();}
  var pts=[];for(i=0;i<nf;i++){var a=(i/nf)*Math.PI*2-Math.PI/2,d=rr*(DATA.factors[i].value||0);pts.push({x:cx+Math.cos(a)*d,y:cy+Math.sin(a)*d});rctx.beginPath();rctx.moveTo(cx,cy);rctx.lineTo(cx+Math.cos(a)*rr,cy+Math.sin(a)*rr);rctx.strokeStyle='#1E222D50';rctx.lineWidth=0.3;rctx.stroke();rctx.fillStyle='#5A6275';rctx.font='11px monospace';rctx.textAlign='center';rctx.fillText(DATA.factors[i].label,cx+Math.cos(a)*(rr+14),cy+Math.sin(a)*(rr+14)+3);}
  rctx.beginPath();for(i=0;i<nf;i++)i===0?rctx.moveTo(pts[i].x,pts[i].y):rctx.lineTo(pts[i].x,pts[i].y);rctx.closePath();rctx.fillStyle='rgba(0,229,255,0.08)';rctx.fill();rctx.strokeStyle='#00E5FF';rctx.lineWidth=1.2;rctx.stroke();
  for(i=0;i<nf;i++){rctx.beginPath();rctx.arc(pts[i].x,pts[i].y,2.5,0,Math.PI*2);rctx.fillStyle='#00E5FF';rctx.fill();}
  var pulse=arguments[0];var dotR=4+(pulse!==undefined?Math.sin(pulse)*1:0);var blurR=8+(pulse!==undefined?Math.sin(pulse)*3:2);rctx.beginPath();rctx.arc(cx,cy,Math.max(2,dotR),0,Math.PI*2);rctx.fillStyle='#00FF66';rctx.shadowColor='#00FF66';rctx.shadowBlur=Math.max(4,blurR);rctx.fill();rctx.shadowBlur=0;if(pulse!==undefined){var ringR=Math.max(1,5+Math.sin(pulse)*3);rctx.beginPath();rctx.arc(cx,cy,ringR,0,Math.PI*2);rctx.strokeStyle='rgba(0,229,255,'+Math.max(0.05,0.25-Math.sin(pulse)*0.15)+')';rctx.lineWidth=1;rctx.stroke();}rctx.textAlign='start';
}

var radarPulseR=0,radarAnimId=null;
function animateRadarPulse(){
  radarPulseR=(radarPulseR+0.08)%(Math.PI*2);
  renderRadar(radarPulseR);
  radarAnimId=requestAnimationFrame(animateRadarPulse);
}

function renderKPIs(){var k=DATA.kpi;document.getElementById('kpiSharpe').textContent=k.sharpe!=null?Number(k.sharpe).toFixed(2):'--';document.getElementById('kpiWin').textContent=k.win_rate!=null?Number(k.win_rate).toFixed(1)+'%':'--';document.getElementById('kpiPnL').textContent=k.pnl_day!=null?(k.pnl_day>=0?'+':'')+'$'+Math.abs(k.pnl_day).toFixed(0):'--';document.getElementById('kpiDD').textContent=k.max_drawdown!=null?Number(k.max_drawdown).toFixed(1)+'%':'--';document.getElementById('navTotal').textContent=k.aum!=null?'$'+Number(k.aum).toLocaleString():'--';}

var pipePulse=0,pipeAnimId=null,pipeOrbs=[];
// Event-driven pipeline orbs: one orb per filled/rejected/failed trade event.
// Each orb travels its path to completion, holds, then fades out — it never
// disappears mid-flight (signal state no longer controls the animation).
function pipeEdges(path){
  if(path==='reject')return [[0,2],[2,3],[3,0]];
  if(path==='fail')return [[0,2],[2,4],[4,5],[5,0]];
  if(path==='wait')return [[0,1]];
  return [[0,2],[2,4],[4,6],[6,7]]; // exec
}
function spawnPipeOrb(path,label){
  // Stagger simultaneous events slightly so orbs don't perfectly overlap.
  pipeOrbs.push({path:path,t:(pipeOrbs.length%3)*0.4,done:false,doneAge:0,label:label||''});
  if(pipeOrbs.length>40)pipeOrbs.shift();
}
// One orb per server scan (1/s): the orb's path reflects the scan's main
// outcome — SIGNAL→WAIT when nothing fired, or SIGNAL→RISK→…→DONE/REJECT/FAIL
// when trades were attempted. Priority: filled > failed > rejected > hold.
function spawnOrbsFromData(){
  var path='wait',label='';
  if(DATA.executed.length>0){
    path='exec';
    label=DATA.executed[0].symbol+' '+(DATA.executed[0].side||'');
  }else if(DATA.failed.length>0){
    path='fail';
    label=DATA.failed[0].symbol+' '+(DATA.failed[0].side||'');
  }else if(DATA.rejected.length>0){
    path='reject';
    label=DATA.rejected[0].symbol+' '+(DATA.rejected[0].side||'');
  }
  spawnPipeOrb(path,label);
}
function renderPipeline(pulse){
  var c=document.getElementById('pipelineCanvas');if(!c)return;
  var p=c.parentElement;c.width=p.clientWidth-16;c.height=Math.max(p.clientHeight-4,50);
  var ctx=c.getContext('2d'),w=c.width,h=c.height;
  var cx=15,cy=h*0.5,step=(w-30)/7;
  var t=pulse||0;
  var nodes=[
    {x:cx,y:cy,label:'SIGNAL',color:'#00E5FF',active:true},
    {x:cx+step,y:cy-24,label:'WAIT',color:'#5A6275',active:false},
    {x:cx+step*2,y:cy,label:'RISK',color:'#00FF66',active:true},
    {x:cx+step*3,y:cy-24,label:'REJECT',color:'#5A6275',active:false},
    {x:cx+step*4,y:cy,label:'ORDER',color:'#00E5FF',active:true},
    {x:cx+step*5,y:cy-24,label:'FAIL',color:'#5A6275',active:false},
    {x:cx+step*6,y:cy,label:'FILL',color:'#00FF66',active:true},
    {x:cx+step*7,y:cy,label:'DONE',color:'#00E5FF',active:true}
  ];
  var allEdges=[
    [0,1,'','#FF2A6D'],[0,2,'signal','#00E5FF'],
    [2,3,'','#FF2A6D'],[2,4,'pass','#00FF66'],
    [4,5,'','#FF2A6D'],[4,6,'exec','#00FF66'],
    [6,7,'settle','#00E5FF']
  ];
  ctx.lineWidth=0.8;
  for(var i=0;i<allEdges.length;i++){
    var e=allEdges[i],a=nodes[e[0]],b=nodes[e[1]];
    ctx.beginPath();ctx.moveTo(a.x+12,a.y);ctx.lineTo(b.x-12,b.y);
    ctx.strokeStyle=e[3];ctx.setLineDash(e[3]==='#FF2A6D'?[2,4]:[]);ctx.stroke();ctx.setLineDash([]);
    ctx.fillStyle=e[3];ctx.font='11px monospace';ctx.fillText(e[2],(a.x+b.x)/2-12,a.y+14);
  }
  // Orbs — one per symbol per scan, spawned at SIGNAL, traveling one-way to
  // their terminal node (WAIT/REJECT/FAIL/DONE), then destroyed after a hold.
  // No return trips, no idle patrol orb.
  if(t!==undefined){
    var ORB_SPEED=0.06,HOLD_FRAMES=90,FADE_FRAMES=30; // ~2.2s travel, 1.5s hold, 0.5s fade
    for(var oi=0;oi<pipeOrbs.length;oi++){
      var orb=pipeOrbs[oi],edges=pipeEdges(orb.path),segCount=edges.length,alpha=1;
      var HOLD=orb.path==='wait'?30:HOLD_FRAMES; // wait orbs mark "no trigger" — fade out quickly
      if(!orb.done){
        orb.t+=ORB_SPEED;
        if(orb.t>=segCount*2){orb.done=true;orb.doneAge=0;orb.t=segCount*2-0.001;}
      }else{
        orb.doneAge++;
        if(orb.doneAge>HOLD)alpha=Math.max(0,1-(orb.doneAge-HOLD)/FADE_FRAMES);
        if(orb.doneAge>HOLD+FADE_FRAMES){pipeOrbs.splice(oi,1);oi--;continue;}
      }
      var segIdx=Math.floor((orb.t%(segCount*2))/2);
      var segProg=((orb.t%(segCount*2))%2)/2;
      if(segIdx>=segCount)continue;
      var sa=nodes[edges[segIdx][0]],sb=nodes[edges[segIdx][1]];
      // Travel node-center to node-center so the orb visually ARRIVES at the
      // terminal node before holding and fading out.
      var dx=sa.x+(sb.x-sa.x)*segProg,dy=sa.y+(sb.y-sa.y)*segProg;
      var orbColor='#00E5FF'; // unified orb color
      ctx.globalAlpha=alpha;
      ctx.beginPath();ctx.arc(dx,dy,3,0,Math.PI*2);ctx.fillStyle=orbColor;
      ctx.shadowColor=orbColor;ctx.shadowBlur=8;ctx.fill();ctx.shadowBlur=0;
      if(orb.label){ctx.fillStyle='#5A6275';ctx.font='9px monospace';ctx.fillText(orb.label,dx-10,dy-8);}
      ctx.globalAlpha=1;
    }
  }
  for(var n=0;n<nodes.length;n++){
    var nd=nodes[n],br=nd.label==='SIGNAL'||nd.label==='DONE'?6:4;
    if(nd.active&&t!==undefined)br+=Math.sin(t+n*0.5)*1.5;
    br=Math.max(2,br);
    ctx.beginPath();ctx.arc(nd.x,nd.y,br,0,Math.PI*2);ctx.fillStyle=nd.color;ctx.fill();
    if(nd.active){ctx.shadowColor=nd.color;ctx.shadowBlur=Math.max(2,5+Math.sin(t+n*0.5)*3);ctx.fill();ctx.shadowBlur=0;}
    ctx.fillStyle='#5A6275';ctx.font='12px monospace';ctx.fillText(nd.label,nd.x-14,nd.y+18);
  }
}
function animatePipeline(){
  if(!document.getElementById('pipelineCanvas')){pipeAnimId=null;return;}
  if(currentPage!=='dashboard'){pipeAnimId=null;return;} // pause while off-page
  pipePulse=(pipePulse+0.03)%(8*2);
  renderPipeline(pipePulse);
  pipeAnimId=requestAnimationFrame(animatePipeline);
}

function renderPositionsBar(){
  var pb=document.getElementById('positionsBar');pb.innerHTML='';
  if(!DATA.positions||!DATA.positions.length){pb.innerHTML='<span class="text-[#5A6275]">No open positions</span>';return;}
  for(var i=0;i<DATA.positions.length;i++){var p=DATA.positions[i],pnl=p.unrealized_pnl||0,pc=pnl>=0?'text-[#00FF66]':'text-[#FF2A6D]',ps=pnl>=0?'+':'';pb.innerHTML+='<span class="text-[#00E5FF]">'+p.symbol+'</span><span class="'+(p.side==='BUY'?'text-[#00FF66]':'text-[#FF2A6D]')+'">'+p.side+'</span><span class="text-[#5A6275]">@$'+Number(p.entry_price).toFixed(p.entry_price<1?4:2)+'</span><span class="'+pc+'">'+ps+'$'+Math.abs(pnl).toFixed(2)+'</span><span class="text-[#5A6275] mx-2">|</span>';}
}
function renderSignalSummary(){
  var buys=DATA.tickers.filter(function(t){return t.signal==='BUY';}),sells=DATA.tickers.filter(function(t){return t.signal==='SELL';}),waits=DATA.tickers.filter(function(t){return t.signal==='WAIT';}),others=DATA.tickers.filter(function(t){return t.signal==='HOLD'||!t.signal;});
  var fmt=function(a){return a.length?a.length+' ('+a.map(function(t){return t.id;}).join(', ')+')':'--';};
  document.getElementById('posLong').textContent=fmt(buys);document.getElementById('posShort').textContent=fmt(sells);document.getElementById('posFlat').textContent=fmt(waits);document.getElementById('posPending').textContent=fmt(others);
  var pbc=document.getElementById('posBar'),pctx=pbc.getContext('2d'),w=pbc.width,h=pbc.height;pctx.clearRect(0,0,w,h);
  var total=DATA.tickers.length||1,segs=[{pct:buys.length/total,color:'#00FF66'},{pct:sells.length/total,color:'#FF2A6D'},{pct:waits.length/total,color:'#FFCC00'},{pct:others.length/total,color:'#5A6275'}],sx=0;
  for(var s=0;s<segs.length;s++){var sw=w*segs[s].pct;pctx.fillStyle=segs[s].color;pctx.fillRect(sx,0,sw,h);sx+=sw;}
}
function renderRecentTrades(){
  var el=document.getElementById('recentTrades');el.innerHTML='';
  if(!DATA.trades||!DATA.trades.length){el.innerHTML='<div class="text-[#5A6275]">No trades yet</div>';return;}
  for(var i=0;i<Math.min(DATA.trades.length,20);i++){var t=DATA.trades[i],sc=t.side==='BUY'?'text-[#00FF66]':'text-[#FF2A6D]';el.innerHTML+='<div class="flex gap-3 border-b border-[#1E222D30] py-0.5"><span class="text-[#5A6275] w-16">'+(t.created_at||'').slice(11,19)+'</span><span class="text-[#00E5FF] w-14">'+t.symbol+'</span><span class="'+sc+' w-8">'+t.side+'</span><span class="text-[#5A6275]">$'+Number(t.price).toFixed(t.price<1?4:2)+' x'+t.quantity+'</span></div>';}
}
function renderExecLog(){
  var el=document.getElementById('execLog');el.innerHTML='';
  if(!DATA.exec_log.length){el.innerHTML='<div class="text-[#5A6275] py-px">'+I18n.t('awaiting_feed')+'</div>';return;}
  for(var i=Math.max(0,DATA.exec_log.length-50);i<DATA.exec_log.length;i++){var e=DATA.exec_log[i],div=document.createElement('div');div.className='py-px';div.innerHTML=e.html||'';el.appendChild(div);}
  el.parentElement.scrollTop=el.parentElement.scrollHeight;
}

// ============================================================
// FETCH LOOP
// ============================================================
// Binance WebSocket — real-time prices + order book.
// Two separate raw streams: !bookTicker does NOT deliver in a combined
// stream (verified against the API), so they cannot share one connection.
var binanceWS=null,bookWS=null,wsPrices={},wsBook={};
function connectBinanceWS(){
  if(binanceWS)try{binanceWS.close();}catch(e){}
  binanceWS=new WebSocket('wss://stream.binance.com:9443/ws/!miniTicker@arr');
  binanceWS.onmessage=function(e){
    try{
      var data=JSON.parse(e.data);
      if(Array.isArray(data)){
        data.forEach(function(t){wsPrices[t.s]={price:parseFloat(t.c),high:parseFloat(t.h),low:parseFloat(t.l)};});
        updatePrices();
      }
    }catch(ex){}
  };
  binanceWS.onclose=function(){setTimeout(connectBinanceWS,5000);};
  binanceWS.onerror=function(){binanceWS.close();};

  if(bookWS)try{bookWS.close();}catch(e){}
  bookWS=new WebSocket('wss://stream.binance.com:9443/ws/!bookTicker');
  bookWS.onmessage=function(e){
    try{
      var t=JSON.parse(e.data);
      if(t&&t.s){
        wsBook[t.s]={best_bid:parseFloat(t.b),best_ask:parseFloat(t.a),bid_qty:parseFloat(t.B),ask_qty:parseFloat(t.A)};
      }
    }catch(ex){}
  };
  bookWS.onclose=function(){setTimeout(connectBinanceWS,5000);};
  bookWS.onerror=function(){bookWS.close();};
}
var _priceBuffer={};
function updateRailPrices(){
  // Surgical DOM update of the ticker rail price spans (no full redraw).
  // Called from updatePrices (WebSocket ticks) AND fetchData (1s poll fallback),
  // so prices keep moving even if the WS feed drops.
  if(!DATA.tickers.length)return;
  var cards=document.querySelectorAll('#tickerRail .panel');
  for(var i=0;i<cards.length&&i<DATA.tickers.length;i++){
    var tk=DATA.tickers[i],spans=cards[i].querySelectorAll('span');
    if(spans.length>=2){
      spans[spans.length-2].textContent='$'+Number(tk.price).toFixed(tk.price<1?4:2);
    }
  }
}
function updatePrices(){
  if(!DATA.tickers)return;
  for(var i=0;i<DATA.tickers.length;i++){
    var sym=DATA.tickers[i].id,w=wsPrices[sym+'USDT'];
    if(w){
      DATA.tickers[i].price=w.price;
      // Accumulate live price buffer for indicators
      if(!_priceBuffer[sym])_priceBuffer[sym]=[];
      _priceBuffer[sym].push(w.price);
      if(_priceBuffer[sym].length>100)_priceBuffer[sym].shift();
    }
  }
  updateRailPrices();
  // Re-evaluate strategies on every price update
  if(activeJSStrategy){
    for(var i=0;i<DATA.tickers.length;i++){
      var s=evaluateJSStrategy(DATA.tickers[i]);
      DATA.tickers[i].signal=s.signal;DATA.tickers[i].confidence=s.confidence;
    }
  }
  if(DATA.tickers.length&&currentPage==='dashboard')R(false);
}

// ============================================================
// JS STRATEGY EVALUATOR (client-side)
// ============================================================
var activeJSStrategy=null;
function loadActiveJSStrategy(){
  if(!activeStratFile) return;
  fetch('/api/strategy/'+activeStratFile+'/code').then(function(r){return r.json();}).then(function(d){
    eval('activeJSStrategy='+d.code.replace(/\n/g,'\n'));
  }).catch(function(){});
}
function evaluateJSStrategy(ticker){
  // Use live price buffer (from WS) instead of HTTP-polled sparklines
  var closes=_priceBuffer[ticker.id]||[],price=ticker.price;
  var rsi=calcRSI(closes,14),sma20=calcSMA(closes,20),ema12=calcEMA(closes,12),ema26=calcEMA(closes,26);
  var volSurge=ticker.volume>0;
  var macd=calcMACD(closes),bb=calcBB(closes);
  var w=wsPrices[ticker.id+'USDT']||{};
  var b=wsBook[ticker.id+'USDT']||ticker.book; // WS book may be unavailable; fall back to 1s poll
  var book=b?{best_bid:b.best_bid,best_ask:b.best_ask,bid_qty:b.bid_qty,ask_qty:b.ask_qty,
    spread_pct:b.best_ask&&b.best_bid?((b.best_ask-b.best_bid)/((b.best_ask+b.best_bid)/2)*100):0,
    imbalance:(b.bid_qty+b.ask_qty)?(b.bid_qty-b.ask_qty)/(b.bid_qty+b.ask_qty):0}:null;
  try{
    if(activeJSStrategy&&typeof activeJSStrategy.evaluate==='function'){
      return activeJSStrategy.evaluate({
        id:ticker.id,name:ticker.name,price:price,volume:ticker.volume_m,change_pct:ticker.change_pct,
        high_24h:w.high||ticker.high_24h,low_24h:w.low||ticker.low_24h,
        pct_from_high:w.high?((price-w.high)/w.high*100):0,
        pct_from_low:w.low?((price-w.low)/w.low*100):0,
        book:book,
        position:ticker.position,      // {side,quantity,entry_price} or null (1s poll)
        portfolio:ticker.portfolio     // {cash,total_equity} (1s poll)
      },{rsi:rsi,sma20:sma20,sma50:calcSMA(closes,50),ema12:ema12,ema26:ema26,ema50:calcEMA(closes,50),
        macd_line:macd[0],macd_signal:macd[1],macd_hist:macd[2],
        bb_upper:bb[0],bb_middle:bb[1],bb_lower:bb[2],
        atr14:calcATRApprox(closes,14),
        rsi_4h:rsi,sma_4h:sma20, // approximate: client buffer is live ticks, not 4h candles
        volSurge:volSurge,closes:closes});
    }
  }catch(e){}
  return {signal:"HOLD",confidence:50};
}
function calcRSI(c,p){p=p||14;if(c.length<p+1)return 50;var g=0,l=0;for(var i=1;i<=p;i++){var d=c[c.length-i]-c[c.length-i-1];if(d>0)g+=d;else l-=d;}if(l===0)return 100;return 100-(100/(1+g/l));}
function calcSMA(c,p){p=p||20;if(!c.length)return 0;var s=0,n=Math.min(c.length,p);for(var i=0;i<n;i++)s+=c[c.length-1-i];return s/n;}
function calcEMA(c,p){p=p||12;if(c.length<2)return c[c.length-1]||0;var m=2/(p+1),e=c[0];for(var i=1;i<c.length;i++)e=(c[i]-e)*m+e;return e;}
function emaSeries(c,p){if(!c.length)return[];var m=2/(p+1),o=[c[0]];for(var i=1;i<c.length;i++)o.push((c[i]-o[o.length-1])*m+o[o.length-1]);return o;}
function calcMACD(c,f,s,g){f=f||12;s=s||26;g=g||9;if(c.length<s)return[0,0,0];var ef=emaSeries(c,f),es=emaSeries(c,s),ms=[];for(var i=0;i<ef.length;i++)ms.push(ef[i]-es[i]);var ss=emaSeries(ms,g),line=ms[ms.length-1],sig=ss[ss.length-1];return[line,sig,line-sig];}
function calcBB(c,p,k){p=p||20;k=k||2;var n=Math.min(c.length,p);if(n<2){var lc=c[c.length-1]||0;return[lc,lc,lc];}var win=c.slice(-n),mid=win.reduce(function(a,b){return a+b;},0)/n;var sd=Math.sqrt(win.reduce(function(a,b){return a+(b-mid)*(b-mid);},0)/n);return[mid+k*sd,mid,mid-k*sd];}
function calcATRApprox(c,p){p=p||14;if(c.length<p+1)return 0;var s=0;for(var i=c.length-p;i<c.length;i++)s+=Math.abs(c[i]-c[i-1]);return s/p;}

var PI=1000; // 1s poll — high-frequency strategy evaluation (klines are cached server-side)
function fetchData(){
  var start=Date.now();
  Promise.all([fetch('/api/data').then(function(r){return r.json();}),fetch('/api/positions').then(function(r){return r.json();}),fetch('/api/trades').then(function(r){return r.json();})])
    .then(function(results){var data=results[0],pos=results[1],trades=results[2],latency=Date.now()-start;
      for(var k in data){if(DATA.hasOwnProperty(k))DATA[k]=data[k];}DATA.positions=pos.positions||[];DATA.trades=trades.trades||[];DATA.connected=true;DATA.latency_ms=latency;
      var tc=document.getElementById('tickerCount');if(tc)tc.textContent=DATA.tickers.length;
      var cp=document.getElementById('cliPrompt');if(cp)cp.textContent=I18n.t('cli_scan')+' '+DATA.tickers.map(function(t){return t.id;}).join(' ');
      document.getElementById('statusBar').textContent=I18n.t('updated')+' '+(new Date().toTimeString().slice(0,8))+' | '+latency+'ms';document.getElementById('statusBar').className='text-[#00FF66]';
      if(currentPage==='dashboard'){
        updateRailPrices();
        spawnOrbsFromData();
      }else{
        // Pipeline is paused off-dashboard: drop queued orbs so switching back
        // doesn't burst-release a backlog of stored orbs.
        pipeOrbs=[];
      }
      if(DATA.tickers.length&&currentPage==='dashboard'){
        // First data arrival: full render so the ticker rail gets built (R(false)
        // skips renderTickerRail). Later updates stay surgical to avoid flicker.
        if(!document.getElementById('tickerRail').querySelector('.panel')){R();}else{R(false);}
      }setTimeout(fetchData,PI);
    }).catch(function(err){document.getElementById('statusBar').textContent=I18n.t('api_error')+': '+err.message;document.getElementById('statusBar').className='text-[#FF2A6D]';DATA.connected=false;renderConnection();setTimeout(fetchData,PI);});
}

// ============================================================
// ACCOUNT PAGE
// ============================================================
function loadAccount(){
  Promise.all([
    fetch('/api/portfolio').then(function(r){return r.json();}),
    fetch('/api/positions').then(function(r){return r.json();}),
    fetch('/api/trades').then(function(r){return r.json();})
  ]).then(function(results){
    var pf=results[0],pos=results[1],trades=results[2];
    var initCap=pf.initial_capital||10000;
    var pnl=pf.total_equity-initCap,pnlCl=pnl>=0?'text-[#00FF66]':'text-[#FF2A6D]',pnlSg=pnl>=0?'+':'';

    // Summary cards
    document.getElementById('accountSummary').innerHTML=
      '<div class="panel p-3 text-center"><div class="text-[15px] text-[#5A6275]">CASH</div><div class="text-[#00E5FF] text-base">$'+pf.cash.toLocaleString()+'</div></div>'+
      '<div class="panel p-3 text-center"><div class="text-[15px] text-[#5A6275]">POSITIONS</div><div class="text-[#FFCC00] text-base">$'+pf.position_value.toLocaleString()+'</div></div>'+
      '<div class="panel p-3 text-center"><div class="text-[15px] text-[#5A6275]">TOTAL EQUITY</div><div class="'+pnlCl+' text-base">$'+pf.total_equity.toLocaleString()+'</div></div>'+
      '<div class="panel p-3 text-center"><div class="text-[15px] text-[#5A6275]">P&L</div><div class="'+pnlCl+' text-base">'+pnlSg+'$'+Math.abs(pnl).toLocaleString()+'</div></div>';

    // Positions
    var posEl=document.getElementById('accountPositions');
    if(!pos.positions||!pos.positions.length){posEl.innerHTML='<div class="text-[#5A6275] text-[15px]">No open positions</div>';}
    else{
      posEl.innerHTML='<table class="bt-table w-full"><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Current</th><th>P&L</th><th>Strategy</th></tr></thead><tbody>'+
        pos.positions.map(function(p){var pnlCl2=p.unrealized_pnl>=0?'text-[#00FF66]':'text-[#FF2A6D]',pnlSg2=p.unrealized_pnl>=0?'+':'';return '<tr><td class="text-[#00E5FF]">'+p.symbol+'</td><td class="'+(p.side==='BUY'?'text-[#00FF66]':'text-[#FF2A6D]')+'">'+p.side+'</td><td>'+Number(p.quantity).toFixed(4)+'</td><td>$'+Number(p.entry_price).toFixed(2)+'</td><td>$'+(p.current_price?Number(p.current_price).toFixed(2):'--')+'</td><td class="'+pnlCl2+'">'+pnlSg2+'$'+Math.abs(p.unrealized_pnl||0).toFixed(2)+'</td><td class="text-[#5A6275]">'+(p.strategy||'')+'</td></tr>';}).join('')+
        '</tbody></table>';
    }

    // Trades
    var trEl=document.getElementById('accountTrades');
    if(!trades.trades||!trades.trades.length){trEl.innerHTML='<div class="text-[#5A6275]">No trades yet</div>';}
    else{
      trEl.innerHTML='<table class="bt-table w-full"><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Price</th><th>Qty</th><th>Notional</th><th>Strategy</th></tr></thead><tbody>'+
        trades.trades.map(function(t){var sc=t.side==='BUY'?'text-[#00FF66]':'text-[#FF2A6D]';return '<tr><td class="text-[#5A6275]">'+(t.created_at||'').slice(11,19)+'</td><td class="text-[#00E5FF]">'+t.symbol+'</td><td class="'+sc+'">'+t.side+'</td><td>$'+Number(t.price).toFixed(Number(t.price)<1?4:2)+'</td><td>'+Number(t.quantity).toFixed(4)+'</td><td>$'+Number(t.notional).toFixed(0)+'</td><td class="text-[#5A6275]">'+(t.strategy||'')+'</td></tr>';}).join('')+
        '</tbody></table>';
    }

    // Equity curve from trades
    drawAccountChart(trades.trades||[],initCap);
  });
}

function drawAccountChart(trades,initialCapital){
  var c=document.getElementById('accountChart'),p=c.parentElement;
  c.width=p.clientWidth-8;c.height=p.clientHeight-8;
  var ctx=c.getContext('2d'),w=c.width,h=c.height,pad=40;
  ctx.clearRect(0,0,w,h);
  if(!trades.length){ctx.fillStyle='#5A6275';ctx.font='12px monospace';ctx.textAlign='center';ctx.fillText(I18n.t('no_trades'),w/2,h/2);ctx.textAlign='start';return;}

  // Build equity curve from trade history (cash + position MTM)
  var cash=initialCapital,positions={},equity=[],dates=[],lastPrice={};
  for(var i=trades.length-1;i>=0;i--){
    var t=trades[i];
    lastPrice[t.symbol]=t.price;
    if(t.side==='BUY'){
      cash-=t.notional;
      if(positions[t.symbol]){positions[t.symbol]+=t.quantity;}
      else{positions[t.symbol]=t.quantity;}
    }else{
      cash+=t.notional;
      if(positions[t.symbol]){positions[t.symbol]-=t.quantity;if(positions[t.symbol]<=0)delete positions[t.symbol];}
    }
    // MTM: mark remaining positions to last known price
    var mtm=0;
    for(var sym in positions){mtm+=positions[sym]*(lastPrice[sym]||t.price);}
    equity.push(cash+mtm);
    dates.push((t.created_at||'').slice(11,19));
  }
  equity.reverse();dates.reverse();

  var eqMin=Math.min.apply(null,equity.concat([initialCapital])),eqMax=Math.max.apply(null,equity.concat([initialCapital])),eqRng=eqMax-eqMin||1;

  // Axes
  ctx.strokeStyle='#1E222D';ctx.lineWidth=0.5;
  ctx.beginPath();ctx.moveTo(pad,pad);ctx.lineTo(pad,h-pad);ctx.lineTo(w-10,h-pad);ctx.stroke();
  ctx.fillStyle='#5A6275';ctx.font='11px monospace';ctx.textAlign='right';
  for(var i=0;i<=4;i++){var y=h-pad-(h-2*pad)*i/4,val=eqMin+eqRng*i/4;ctx.fillText('$'+(val/1000).toFixed(1)+'k',pad-4,y+3);}

  // Baseline
  var baseY=h-pad-(h-2*pad)*(initialCapital-eqMin)/eqRng;
  ctx.strokeStyle='#5A6275';ctx.setLineDash([3,5]);ctx.beginPath();ctx.moveTo(pad,baseY);ctx.lineTo(w-10,baseY);ctx.stroke();ctx.setLineDash([]);

  // Equity line
  ctx.strokeStyle='#00E5FF';ctx.lineWidth=1.5;ctx.beginPath();
  for(var j=0;j<equity.length;j++){var x=pad+(w-pad-10)*j/(equity.length-1),yV=h-pad-(h-2*pad)*(equity[j]-eqMin)/eqRng;j===0?ctx.moveTo(x,yV):ctx.lineTo(x,yV);}
  ctx.stroke();

  // Fill
  ctx.fillStyle='rgba(0,229,255,0.08)';ctx.lineTo(w-10,h-pad);ctx.lineTo(pad,h-pad);ctx.closePath();ctx.fill();

  // Final value
  ctx.fillStyle='#00E5FF';ctx.font='12px monospace';ctx.textAlign='start';
  var finalVal=equity[equity.length-1],finalCl=finalVal>=initialCapital?'#00FF66':'#FF2A6D';
  ctx.fillStyle=finalCl;
  ctx.fillText('$'+finalVal.toLocaleString(),w-8,h-pad-8);
  ctx.textAlign='start';
}

// ============================================================
// BOOT
// ============================================================
function loadSymbols(){
  fetch('/api/symbols').then(function(r){return r.json();}).then(function(d){
    var el=document.getElementById('symbolList');el.innerHTML='';
    d.symbols.forEach(function(s){
      el.innerHTML+='<div class="flex items-center justify-between py-0.5 border-b border-[#1E222D20]"><span class="text-[#00E5FF]">'+s.symbol+'</span><span class="text-[#5A6275]">'+s.name+'</span><button class="btn-sm danger" onclick="removeSymbol(this)" data-sym="'+s.symbol+'">✕</button></div>';
    });
  });
}
function addSymbol(){
  var sym=document.getElementById('newSymbol').value.toUpperCase().trim();
  var name=document.getElementById('newSymbolName').value.trim()||sym.replace('USDT','');
  if(!sym)return;
  fetch('/api/symbols/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym,name:name})})
    .then(function(r){return r.json();}).then(function(d){
      if(d.error)alert(d.error);else{document.getElementById('newSymbol').value='';document.getElementById('newSymbolName').value='';loadSymbols();}
    });
}
function resetAccount(){
  if(!confirm('Reset account to $10,000? This clears all trades and positions.'))return;
  fetch('/api/reset',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    alert('Account reset. Capital: $'+d.capital.toLocaleString());
    loadAccount();
  });
}
function removeSymbol(el){
  var sym=el.getAttribute('data-sym');
  if(!confirm('Remove '+sym+'?'))return;
  fetch('/api/symbols/'+sym,{method:'DELETE'}).then(function(){loadSymbols();});
}
// Update loadAccount to call loadSymbols
var _origLoadAccount=loadAccount;
loadAccount=function(){
  _origLoadAccount();loadSymbols();
};

R();animateRadarPulse();
connectBinanceWS();I18n.init().then(function(){loadStrategies();loadActiveJSStrategy();R();fetchData();});