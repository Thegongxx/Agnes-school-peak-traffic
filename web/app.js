'use strict';
const $ = id => document.getElementById(id);
const phaseNames = {ns_green:'南北方向放行',ns_yellow:'南北黄灯清空',ew_green:'东西方向放行',ew_yellow:'东西黄灯清空',pedestrian_walk:'行人独立过街',all_red:'全红 / 清空'};
let data = null, latest = null, runId = '', activePlan = 'baseline', replay = null, playing = false, playTime = 0, lastTick = 0;
const replays = new Map();
let traceKey = "", approvalKey = "";
const canvas = $('intersection'), ctx = canvas.getContext('2d');
function element(tag, text, className) {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function number(value, digits=1) {return typeof value==='number'?value.toFixed(digits):'—';}
function notice(text, error=false) {$('notice').textContent=text;$('notice').classList.toggle('error',error);}
function pair(dl,key,value) {dl.append(element('dt',key),element('dd',String(value)));}
async function refresh() {
  try {
    const response=await fetch('/api/state?scenario_id='+encodeURIComponent($('scenario').value||'peak'));
    if(!response.ok)throw new Error('读取本地状态失败');
    data=await response.json();
    if(!$('scenario').options.length){
      for(const s of data.scenarios.scenarios){const option=element('option',s.name);option.value=s.id;$('scenario').append(option);}
      $('scenario').value='peak';
    }
    const runtime=data.runtime;
    renderApproval(runtime);
    $('runtime-status').textContent=runtime.installed?'AGH 已构建 · 本地工作台':'AGH 尚待构建';
    $('runtime-status').classList.toggle('ready',runtime.installed);
    const running=runtime.current_job?.status==='running';
    $('run').disabled=running||!runtime.installed;
    $('run').textContent=running?'◌ AGH 正在执行…':'▶ 在 AGH 运行优化';
    if(runtime.current_job?.scenario_id===$('scenario').value)notice(runtime.current_job.message,runtime.current_job.status==='failed');
    latest=data.latest;
    const oldRun=runId;runId=latest?.run_id||'';
    renderMetrics();renderTrace();renderAssumptions();renderAghTrace();
    if(runId!==oldRun){replay=null;playTime=0;replays.clear();await loadReplay();}
    else if(!replay&&runId){await loadReplay();}
    $('updated').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
  } catch(error){notice(error.message,true);}
}
function renderApproval(runtime){
  const pending=runtime.approval;
  $('approval').hidden=!pending;
  if(!pending){approvalKey='';return;}
  const key=runtime.current_job.job_id+'-'+pending.id;
  if(key===approvalKey)return;approvalKey=key;
  $('approval-title').textContent=String(pending.title||'请检查工具参数后决定');
  $('approval-input').textContent=JSON.stringify(pending.input||{},null,2);
  $('approval-options').replaceChildren();
  for(const option of pending.options.filter(o=>['allow_once','reject_once'].includes(o.kind))){
    const button=element('button',option.kind==='allow_once'?'允许本次调用':'拒绝本次调用',option.kind==='allow_once'?'primary':'secondary');
    button.addEventListener('click',async()=>{button.disabled=true;try{const response=await fetch('/api/approval',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:runtime.current_job.job_id,approval_id:pending.id,option_id:option.optionId})});const result=await response.json();if(!response.ok)throw new Error(result.error);await refresh();}catch(error){notice(error.message,true);button.disabled=false;}});
    $('approval-options').append(button);
  }
}
function renderMetrics(){
  const b=latest?.baseline?.metrics,c=latest?.selected?.metrics,m=c||b,comparison=latest?.comparison;
  $('delay').textContent=number(m?.mean_vehicle_delay);$('queue').textContent=number(m?.max_queue,0);
  $('ped-wait').textContent=number(m?.mean_pedestrian_wait);$('throughput').textContent=number(m?.throughput,0);
  $('delay-change').textContent=comparison?`相对基线 ${comparison.improvement_percent>=0?'减少':'增加'} ${Math.abs(comparison.improvement_percent).toFixed(1)}%`:'等待候选方案';
  $('delay-change').classList.toggle('improved',!!comparison?.passed);
  $('queue-change').textContent=b&&c?`基线 ${b.max_queue} 辆 → 候选 ${c.max_queue} 辆`:'四个进口的最大值';
  $('ped-change').textContent=b&&c?`基线 ${number(b.mean_pedestrian_wait)}s → 候选 ${number(c.mean_pedestrian_wait)}s`:'SUMO 步行对象等待';
  $('throughput-change').textContent=b&&c?`基线 ${b.throughput} 辆 → 候选 ${c.throughput} 辆`:'需求时段内完成车辆';
  const body=$('comparison-body');body.replaceChildren();
  const rows=[['车辆平均延误','mean_vehicle_delay','s'],['行人平均等待','mean_pedestrian_wait','s'],['行人最大等待','max_pedestrian_wait','s'],['最大排队长度','max_queue','辆'],['高峰通过量','throughput','辆'],['未完成车辆','unfinished_vehicles','辆'],['未完成行人','unfinished_persons','人']];
  for(const[label,key,unit]of rows){const tr=element('tr');tr.append(element('td',label),element('td',`${number(b?.[key],unit==='s'?1:0)} ${unit}`),element('td',`${number(c?.[key],unit==='s'?1:0)} ${unit}`));body.append(tr);}
  const verdict=$('verdict');verdict.className='verdict'+(comparison?(comparison.passed?' passed':' failed'):'');
  verdict.textContent=comparison?(comparison.passed?'方案通过 · 满足项目改善目标':'校验未通过 · 建议保留基线（上方为候选指标）'):'尚未完成方案验证';
  $('verdict-detail').textContent=comparison?(comparison.reasons.join('；')||`车辆平均延误降低 ${comparison.improvement_percent.toFixed(1)}%，行人等待与各方向等待满足约束。`):'候选必须通过相位、绿灯、周期、行人等待、公平性和改善目标检查。';
  const bars=$('bar-chart');bars.replaceChildren();
  for(const[label,key]of rows.slice(0,3)){
    const group=element('div',undefined,'metric-bar');const h=element('header');h.append(element('span',label),element('span',b&&c?`${number(b[key])} → ${number(c[key])}s`:'—'));group.append(h);
    const max=Math.max(b?.[key]||0,c?.[key]||0,1);
    for(const[v,cls]of[[b,''],[c,' candidate']]){const rail=element('div',undefined,'rail'+cls),i=element('i');i.style.width=((v?.[key]||0)/max*100)+'%';rail.append(i);group.append(rail);}bars.append(group);
  }
  const plans=$('plan-bars');plans.replaceChildren();
  for(const[label,p]of[['基线',latest?.baseline?.plan],['候选',latest?.selected?.plan]]){
    if(!p)continue;const row=element('div',undefined,'timing-row'),track=element('div',undefined,'timing-track');row.append(element('span',label),track);
    const rule=data.constraints;const parts=[['ns',p.ns_green,'南北'],['clear',rule.yellow_seconds+rule.all_red_seconds,''],['ew',p.ew_green,'东西'],['clear',rule.yellow_seconds+rule.all_red_seconds,''],['ped',p.pedestrian_walk,'行人'],['clear',rule.pedestrian_clearance_seconds,'清空']];
    for(const[cls,duration,text]of parts){const part=element('span',text?`${text}${duration}s`:'',cls);part.style.flex=duration;part.title=`${text||'黄灯/全红'} ${duration}秒`;track.append(part);}plans.append(row);
  }
  $('export').href=runId?'/api/export?run_id='+runId:'#';$('export').classList.toggle('disabled',!runId);
}
function renderTrace(){
  const calls=data.calls||[];const key=runId+'-'+calls.length+'-'+(calls.at(-1)?.time||'');if(key===traceKey)return;traceKey=key;const list=$('trace-list');list.replaceChildren();$('trace-count').textContent=calls.length+' 步';
  const local=calls.some(c=>c.caller==='local_validation');
  $('trace-source').textContent=local?'本地验算工具记录 · 不计为 AGH 会话证据':data.agh_trace?'MCP 工具回执 · 本页同时显示 AGH 原生会话':'MCP 工具回执 · AGH 原生会话见下方';
  if(!calls.length){list.append(element('div','运行任务后显示工具输入、结果与失败原因。AGH 原生轨迹可在工作台展开查看。','empty-state'));return;}
  const labels={run_baseline:'建立固定配时基线',search_timing_plan:'搜索合法候选',run_candidate:'运行候选仿真',verify_and_compare:'校验与比较',load_scenario:'读取场景'};
  for(const call of calls){const result=call.result||{};const failed=!!result.error||result.passed===false;const item=element('details',undefined,'trace-item'+(failed?' error':''));item.append(element('summary',labels[call.tool]||call.tool));const info=result.error||(result.reasons?.join('；'))||(call.tool==='run_candidate'?`实际评估 ${result.evaluated} 个方案，待校验`:call.tool==='search_timing_plan'?`${result.candidates?.length||0} 个合法候选，搜索轮次 ${result.round??0}`:call.tool==='verify_and_compare'?`改善 ${result.improvement_percent}% · 约束通过`:result.baseline?'SUMO 基线已保存':call.tool);
    item.append(element('div',info,'trace-note'),element('pre',JSON.stringify({tool:call.tool,caller:call.caller,input:call.arguments,result:call.result},null,2).slice(0,14000)));list.append(item);}
}
function renderAghTrace(){
  const trace=data.agh_trace;$('agh-session').hidden=!trace;if(!trace)return;
  $('agh-model').textContent=trace.model||'等待模型事件';
  $('agh-session-id').textContent='会话 '+(trace.session_id||'创建中')+' · '+trace.job.status+' · 原生事件 '+trace.events.length+' 条';
  const names={'tool/call':'调用工具','approval/asked':'请求审批','approval/decided':'审批决定','verifier/signal':'框架校验反馈','turn/end':'任务结束'};
  $('agh-event-list').replaceChildren();
  for(const e of trace.events){const item=element('div',undefined,'agh-event');item.append(element('b','#'+e.seq),element('span',names[e.type]||e.type),element('small',(e.name||e.reason||'').replace(/^mcp_campus_signal_[a-f0-9]+_/,'')));$('agh-event-list').append(item);}
  $('agh-summary').textContent=trace.summary||'模型报告生成中';
}
function renderAssumptions(){
  const s=data.scenarios.scenarios.find(s=>s.id===$('scenario').value);if(!s)return;
  $('flows').replaceChildren();for(const d of ['N','S','E','W']){const span=element('span',({N:'北进口',S:'南进口',E:'东进口',W:'西进口'})[d]);span.append(element('b',s.vehicles_per_hour[d]+' 辆/h'));$('flows').append(span);}
  $('scenario-desc').textContent=s.description+`。行人需求 ${s.pedestrians_per_hour} 人/h，需求时段 ${data.scenarios.duration_seconds}s。`;
  const dl=$('constraints');dl.replaceChildren();const r=data.constraints;
  pair(dl,'车辆绿灯范围',`${r.min_green_seconds}–${r.max_green_seconds}s`);pair(dl,'黄灯 / 全红',`${r.yellow_seconds}s / ${r.all_red_seconds}s`);pair(dl,'行人放行 / 最小清空',`${r.pedestrian_walk_seconds}s / ${r.pedestrian_clearance_seconds}s`);pair(dl,'总周期范围',`${r.min_cycle_seconds}–${r.max_cycle_seconds}s`);pair(dl,'行人最大等待上限',`${r.max_pedestrian_wait_seconds}s`);pair(dl,'进口最大等待上限',`${r.max_approach_wait_seconds}s`);
  const meta=$('run-meta');meta.replaceChildren();pair(meta,'运行编号',runId||'尚未运行');pair(meta,'随机种子',data.scenarios.seed);pair(meta,'仿真引擎',latest?.baseline?.sumo_version||'SUMO / TraCI');pair(meta,'需求文件 SHA256',latest?.baseline?.demand_sha256?.slice(0,16)||'待生成');pair(meta,'重复修正上限',r.max_retries+' 次');pair(meta,'改善开发目标',r.target_delay_improvement_percent+'%');
}
async function loadReplay(){
  if(!runId||!latest?.[activePlan]){replay=null;$('replay-source').textContent='该方案尚无轨迹';return;}
  const key=runId+'-'+activePlan;
  try{if(!replays.has(key)){const response=await fetch('/api/replay?run_id='+runId+'&plan='+activePlan);if(!response.ok)throw new Error('轨迹读取失败');replays.set(key,await response.json());}replay=replays.get(key);$('seek').max=Math.max(0,(replay.snapshots?.length||0)-1);$('replay-source').textContent=`${replay.snapshots.length} 帧 · SUMO`;playTime=Math.min(playTime,replay.simulation_seconds||0);}
  catch(error){notice(error.message,true);}
}
function roundedRect(x,y,w,h,r,fill){ctx.beginPath();ctx.roundRect(x,y,w,h,r);ctx.fillStyle=fill;ctx.fill();}
function draw(timeStamp){
  if(lastTick&&playing&&replay){playTime+=(timeStamp-lastTick)/1000*Number($('speed').value);if(playTime>replay.simulation_seconds)playTime=0;}lastTick=timeStamp;
  const ratio=window.devicePixelRatio||1,width=canvas.clientWidth,height=canvas.clientHeight;
  if(canvas.width!==Math.round(width*ratio)||canvas.height!==Math.round(height*ratio)){canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);}
  ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);ctx.fillStyle='#e9efeb';ctx.fillRect(0,0,width,height);
  const scale=Math.min(width/135,height/80),cx=width/2,cy=height/2;const x=m=>cx+m*scale,y=m=>cy-m*scale;
  const buildings=[[-51,25,30,14,'教学楼'],[20,25,29,13,'图书馆'],[-53,-24,30,15,'宿舍区'],[22,-23,28,15,'校门区域']];
  for(const[bx,by,bw,bh,label]of buildings){roundedRect(x(bx)+2,y(by)+3,bw*scale,bh*scale,5,'#cad6cb');roundedRect(x(bx),y(by),bw*scale,bh*scale,5,'#d9e4da');ctx.fillStyle='#9baa9c';ctx.font='9px "Microsoft YaHei"';ctx.fillText(label,x(bx)+10,y(by)+bh*scale/2+3);for(let i=0;i<4;i++)roundedRect(x(bx)+9+i*bw*scale/5,y(by)+10,4,4,1,'#c1d0c3');}
  for(const[tx,ty]of[[-16,32],[-16,22],[-16,-26],[-17,-33],[16,31],[16,20],[18,-27],[40,16],[-41,-16],[-29,15],[47,-16]]){ctx.beginPath();ctx.arc(x(tx),y(ty),4*scale,0,Math.PI*2);ctx.fillStyle='#cbdec8';ctx.fill();ctx.beginPath();ctx.arc(x(tx)-1,y(ty)-2,2.5*scale,0,Math.PI*2);ctx.fillStyle='#bad0b6';ctx.fill();}
  ctx.fillStyle='#d7ded3';ctx.fillRect(cx-6.2*scale,0,12.4*scale,height);ctx.fillRect(0,cy-6.2*scale,width,12.4*scale);
  ctx.fillStyle='#637471';ctx.fillRect(cx-3.2*scale,0,6.4*scale,height);ctx.fillRect(0,cy-3.2*scale,width,6.4*scale);
  ctx.strokeStyle='#c1c9b4';ctx.lineWidth=1;ctx.setLineDash([7,8]);ctx.beginPath();ctx.moveTo(cx,0);ctx.lineTo(cx,y(8));ctx.moveTo(cx,y(-8));ctx.lineTo(cx,height);ctx.moveTo(0,cy);ctx.lineTo(x(-8),cy);ctx.moveTo(x(8),cy);ctx.lineTo(width,cy);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle='#f4f4e9';for(const offset of[-6,6])for(let stripe=-2.8;stripe<3;stripe+=1.1){ctx.fillRect(x(stripe),y(offset)-scale*1.2,scale*.55,scale*2.4);ctx.fillRect(x(offset)-scale*1.2,y(stripe)-scale*.55,scale*2.4,scale*.55);}
  const frames=replay?.snapshots||[];let index=Math.min(frames.length-1,Math.max(0,Math.floor((playTime-(frames[0]?.time||0))/2)));let frame=frames[index],next=frames[Math.min(index+1,frames.length-1)];
  if(frame){const alpha=Math.min(1,Math.max(0,(playTime-frame.time)/2)),nextVehicles=new Map(next.vehicles.map(v=>[v.id,v]));
    for(const v of frame.vehicles){const n=nextVehicles.get(v.id);const vx=v.x+(n?(n.x-v.x)*alpha:0),vy=v.y+(n?(n.y-v.y)*alpha:0);if(Math.abs(vx)>width/scale/2+5||Math.abs(vy)>height/scale/2+5)continue;let angle=n&&Math.abs(n.x-v.x)+Math.abs(n.y-v.y)>.01?Math.atan2(-(n.y-v.y),n.x-v.x):({N:Math.PI/2,S:-Math.PI/2,E:Math.PI,W:0})[v.id[0]];ctx.save();ctx.translate(x(vx),y(vy));ctx.rotate(angle);roundedRect(-2.25*scale,-.85*scale,4.5*scale,1.7*scale,.6*scale,v.id[0]==='E'||v.id[0]==='W'?'#d4e7d6':'#b7d1df');ctx.fillStyle='#76978c';ctx.fillRect(.4*scale,-.65*scale,.9*scale,1.3*scale);ctx.restore();}
    for(const p of frame.persons){ctx.beginPath();ctx.arc(x(p.x),y(p.y),Math.max(1.5,.3*scale),0,Math.PI*2);ctx.fillStyle='#edc27c';ctx.fill();}
    $('phase-name').textContent=phaseNames[frame.phase]||frame.phase;$('countdown').replaceChildren(document.createTextNode(String(Math.max(0,Math.ceil(frame.countdown-alpha*2)))),element('small','s'));$('seek').value=index;
  }else{$('phase-name').textContent='等待实际仿真';$('countdown').replaceChildren(document.createTextNode('—'),element('small','s'));}
  const phase=frame?.phase||'all_red';for(const[dx,dy,axis]of[[-5.4,8,'ns'],[5.4,-8,'ns'],[-8,-5.4,'ew'],[8,5.4,'ew']]){roundedRect(x(dx)-3,y(dy)-6,6,12,3,'#354c44');ctx.beginPath();ctx.arc(x(dx),y(dy),2.2,0,Math.PI*2);ctx.fillStyle=phase===axis+'_green'?'#65e9a2':phase===axis+'_yellow'?'#f1cd6e':'#e79680';ctx.fill();}
  $('time-label').textContent=String(Math.floor(playTime/60)).padStart(2,'0')+':'+String(Math.floor(playTime%60)).padStart(2,'0');
  requestAnimationFrame(draw);
}
$('scenario').addEventListener('change',()=>{notice('已选择新场景；通过 AGH 发起优化或检查已有仿真记录。');refresh();});
$('refresh').addEventListener('click',refresh);
$('run').addEventListener('click',async()=>{try{$('run').disabled=true;notice('正在向本地 AGH 提交优化任务…');const response=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario_id:$('scenario').value})});const result=await response.json();if(!response.ok)throw new Error(result.error);notice('AGH 任务已启动。模型与 MCP 工具配置在 AGH 工作台中管理。');await refresh();}catch(error){notice(error.message,true);$('run').disabled=false;}});
for(const button of document.querySelectorAll('[data-plan]'))button.addEventListener('click',()=>{activePlan=button.dataset.plan;document.querySelectorAll('[data-plan]').forEach(b=>b.classList.toggle('selected',b===button));loadReplay();});
$('play').addEventListener('click',()=>{playing=!playing;$('play').textContent=playing?'Ⅱ':'▶';});
$('seek').addEventListener('input',()=>{playTime=(replay?.snapshots?.[Number($('seek').value)]?.time)||0;});
for(const nav of document.querySelectorAll('.nav'))nav.addEventListener('click',()=>{document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('active',n===nav));});
refresh();setInterval(refresh,2500);requestAnimationFrame(draw);
