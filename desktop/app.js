'use strict';
const $=s=>document.querySelector(s);
const tools={claude:{name:'Claude Code',icon:'claude'},codex:{name:'Codex',icon:'codex'},agy:{name:'Antigravity',icon:'antigravity'}};
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon=n=>'<i class="icon i-'+n+'" aria-hidden="true"></i>';
const mark=(tool,mini=false)=>'<img class="mark'+(mini?' mini':'')+'" alt="" draggable="false" src="icons/'+tools[tool].icon+'.png">';
const state=j=>'<span class="status"><i class="dot '+(j.exited?'':'live')+'"></i>'+(j.exited?'已退出':'运行中')+'</span>';
const post=msg=>window.webkit.messageHandlers.workbench.postMessage(msg);
let sessions=[],snapshot='',online=false,loaded=false,selected=[],sequence=0,modal=null,toastTimer;
const panes=new Map(), requests=new Map();
let tabs=[];
const collapsedProjects=new Set();
let quickMatches=[],quickIndex=0;
const exitSamples = {};
function renderExit(kind) {
  const sample = exitSamples[kind];
  if (!sample) return;
  const button = $(kind === 'browser' ? '#browserExit' : '#terminalExit');
  const action = kind === 'browser' ? '点击用专用 Chrome 打开临时检测页' : '点击重新检测终端出口';
  const age = sample.at ? Date.now()/1000 - sample.at : 0;
  const expired = age > (kind === 'browser' ? 300 : 120);
  const when = sample.at ? new Date(sample.at*1000).toLocaleTimeString('zh-CN', {hour12:false}) : '';
  let dot = '', value, status;
  switch (sample.state) {
    case 'verified':
      dot = expired ? '' : 'live';
      status = expired ? '待复查' : '已验证';
      value = (expired ? '上次 ' : '已验证 ') + sample.ip;
      break;
    case 'partial': dot='waiting'; status='仅一个网站返回出口'; value='单站 '+sample.ip; break;
    case 'mismatch': dot='mismatch'; status='与固定出口不符'; value='不符 '+sample.ip; break;
    case 'checking': dot='waiting'; status=value='检测中'; break;
    case 'closed': status=value='Chrome 未打开'; break;
    case 'untested': status=value='点此检测'; break;
    default: dot='waiting'; status=value='未能验证';
  }
  const label = kind === 'browser' ? '浏览器出口' : '终端出口';
  button.querySelector('.dot').className='dot '+dot;
  button.querySelector('.exit-value').textContent=value;
  button.title=[label+' · '+status, when && '检测时间 '+when, sample.detail, action].filter(Boolean).join('\n');
  button.setAttribute('aria-label',[label,status,sample.ip,when,action].filter(Boolean).join('，'));
  button.setAttribute('aria-busy',String(sample.state==='checking'));
}
setInterval(()=>Object.keys(exitSamples).forEach(renderExit), 15000);
const byID=id=>sessions.find(j=>j.id===id);
const keyOf=ids=>ids.join(',');
function tabStrip(){
  tabs=tabs.filter(ids=>ids.every(byID));
  const current=keyOf(selected), root='<button class="editor-tab '+(!current?'chosen':'')+'" data-action="home">'+icon('home')+'会话总览</button>';
  $('#tabs').innerHTML=root+tabs.map(ids=>{
    const key=keyOf(ids),j=byID(ids[0]),title=ids.length===1?j.title:'并排 · '+ids.length;
    return '<div class="tab-group '+(key===current?'chosen':'')+'"><button class="editor-tab" data-action="tab" data-key="'+key+'">'+(ids.length===1?mark(j.tool,true):icon('split'))+'<span class="name">'+esc(title)+'</span></button><button class="tab-close" data-action="close-tab" data-key="'+key+'" aria-label="收起 '+esc(title)+'" title="收起视图，任务继续运行">'+icon('close')+'</button></div>';
  }).join('');
}
function notify(message){const el=$('#toast');el.textContent=message;el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,4200)}
function request(op,fields={}){
  const id=String(++sequence);
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{requests.delete(id);reject(new Error('操作超时，请检查工作台连接。'))},55000);
    requests.set(id,{resolve,reject,timer});post({op,request:id,...fields});
  });
}
function renderNav(){
  const groups=new Map();
  for(const j of sessions){
    if(!groups.has(j.project))groups.set(j.project,[]);
    groups.get(j.project).push(j);
  }
  let html='';
  for(const [project,jobs] of groups){
    const expanded=!collapsedProjects.has(project);
    html+='<button class="project" data-action="project" data-project="'+esc(project)+'" aria-expanded="'+expanded+'">'+icon('chevron')+icon('folder')+'<span class="name">'+esc(project)+'</span></button>';
    if(!expanded)continue;
    html+='<div class="branches">';
    for(const j of jobs)html+='<button class="side-job '+(selected.includes(j.id)?'active':'')+'" data-action="open" data-id="'+esc(j.id)+'" title="'+esc(j.project+' / '+j.title+' · '+tools[j.tool].name)+'">'+mark(j.tool,true)+'<span class="name">'+esc(j.title)+'</span><i class="dot '+(j.exited?'':'live')+'"></i></button>';
    html+='</div>';
  }
  $('#nav').innerHTML=html||(loaded?'<p class="empty">暂无会话</p>':'');
  $('#homeNav').classList.toggle('active',selected.length===0);
}
function renderHome(){
  const table=$('#table');
  table.innerHTML=sessions.map(j=>'<div class="record"><button class="job-name" data-action="open" data-id="'+esc(j.id)+'" title="'+esc(j.project+' / '+j.title+' · '+tools[j.tool].name)+'">'+mark(j.tool,true)+'<span class="name">'+esc(j.title)+'</span><small>'+esc(j.project)+'</small>'+state(j)+'</button><button class="square" data-action="more" data-id="'+esc(j.id)+'" aria-label="'+esc(j.title)+' 的操作">'+icon('more')+'</button></div>').join('')||'<p class="empty">'+(loaded?'选择工具开始第一个会话。':'正在读取会话…')+'</p>';
  $('#total').textContent=sessions.length||'';
}
function head(){
  const active=selected.map(byID).filter(Boolean);
  $('#home').hidden=!!active.length;$('#desk').hidden=!active.length;
  const n=sessions.filter(j=>!j.exited).length;
  $('#summary').textContent=loaded?n+' 个运行中'+(sessions.length>n?' · '+(sessions.length-n)+' 个已退出':''):'正在读取会话…';
  $('#compareButton').disabled=!online;
}
function paneHead(j){
  return icon('folder')+'<small>'+esc(j.project)+'</small>'+icon('chevron')+mark(j.tool,true)+'<b class="name">'+esc(j.title)+'</b><span class="grow"></span>'+(
    j.exited?'<button class="restart" data-action="restart" data-id="'+j.id+'">重新启动</button>':''
  )+'<button class="square" data-action="more" data-id="'+j.id+'" aria-label="会话操作">'+icon('more')+'</button>';
}
function browse(url){
  if(url&&!/^https?:\/\//i.test(url)){notify('工作台 Chrome 只支持网页链接。');return}
  request('browser',url?{url}:{}).catch(e=>notify(e.message));
}
// Mouse-aware programs keep ordinary clicks and wheel events. Give an unmodified
// drag to xterm's local selection, which understands wrapped lines and wide cells.
function bindTerminalSelection(box, terminal) {
  let gesture;
  function cancel() { gesture?.abort(); gesture = undefined; }
  box.addEventListener('mousedown', down => {
    if (!down.isTrusted || down.button !== 0 || down.altKey || down.ctrlKey || down.metaKey || down.shiftKey ||
        terminal.modes.mouseTrackingMode === 'none' || !down.target.closest('.xterm-screen')) return;
    cancel();
    down.preventDefault();
    down.stopPropagation();
    terminal.focus();
    terminal.clearSelection();
    gesture = new AbortController();
    const options = {capture:true, signal:gesture.signal};
    // Single clicks belong to the running tool. Text gestures belong to xterm.
    let selecting = down.detail >= 2;
    function press(localSelection) {
      down.target.dispatchEvent(new MouseEvent('mousedown', {
        view:window, bubbles:true, cancelable:true, button:0, buttons:1,
        detail:down.detail, clientX:down.clientX, clientY:down.clientY, altKey:localSelection
      }));
    }
    if (selecting) press(true);
    window.addEventListener('mousemove', move => {
      if (!move.isTrusted) return;
      if (!selecting) {
        if (Math.hypot(move.clientX-down.clientX, move.clientY-down.clientY) < 3) return;
        selecting = true;
        press(true);
      }
      // xterm tracks selection on document. Its terminal-level hover reporter
      // would also send the same move to the tool and clear the local selection.
      move.stopPropagation();
      document.dispatchEvent(new MouseEvent('mousemove', {
        view:window, bubbles:true, clientX:move.clientX, clientY:move.clientY, buttons:1
      }));
    }, options);
    window.addEventListener('mouseup', up => {
      if (up.button !== 0) return;
      if (!selecting) press(false);
      cancel();
    }, options);
    window.addEventListener('blur', cancel, {signal:gesture.signal});
  }, true);
  box.addEventListener('mousemove', move => {
    if (!gesture && !move.buttons && terminal.hasSelection() && terminal.modes.mouseTrackingMode === 'any') {
      move.stopPropagation();
    }
  }, true);
  return cancel;
}
window.selectedTerminalText = () => {
  for (const {terminal} of panes.values()) {
    if (terminal.element.contains(document.activeElement)) return terminal.getSelection();
  }
  return null;
};
// Codex draws its login URL on separate rows, even when xterm marks none as wrapped.
function codexLoginLinks(terminal, activate) {
  const prefix = 'https://auth.openai.com/oauth/authorize?';
  return {provideLinks(row, done) {
    const buffer = terminal.buffer.active;
    const line = y => buffer.getLine(y - 1)?.translateToString(true) || '';
    const continuation = /^[A-Za-z0-9%._~!$&()*+,;=:@/?#-]+$/;
    let start = row;
    while (start > 1 && row - start < 40 && !line(start).trimStart().startsWith('https://') &&
           continuation.test(line(start))) start--;
    const first = line(start);
    const column = first.length - first.trimStart().length;
    let url = first.slice(column);
    if (!url.startsWith('https://') || /\s/.test(url)) return done(undefined);
    let end = start;
    while (end - start < 40 && continuation.test(line(end + 1))) url += line(++end);
    if (row > end || !url.startsWith(prefix)) return done(undefined);
    const params = new URL(url).searchParams;
    const complete = ['client_id','state','code_challenge','redirect_uri','response_type'].every(k => params.get(k));
    const open = complete ? activate : () => notify('登录链接显示不完整，请在 Codex 中重新发起登录。');
    done([{text:url, range:{start:{x:column+1,y:start},end:{x:line(end).length,y:end}}, activate:open}]);
  }};
}
function mount(j){
  const view='view'+(++sequence),section=document.createElement('article');
  section.className='pane';section.dataset.job=j.id;section.setAttribute('aria-label',j.title);
  const top=document.createElement('div');top.className='pane-head';top.innerHTML=paneHead(j);
  const box=document.createElement('div');box.className='terminal-box';
  section.append(top,box);$('#desk').append(section);
  const openLink=(_event,url)=>{if(!terminal.hasSelection())browse(url)};
  const terminal=new Terminal({cursorBlink:true,macOptionClickForcesSelection:true,fontFamily:'Menlo, Monaco, "PingFang SC", monospace',fontSize:13,lineHeight:1.18,scrollback:5000,
    theme:{background:'#181818',foreground:'#f0f0f0',cursor:'#e4e4e4',selectionBackground:'#42464c',black:'#252525',red:'#e58383',green:'#72bc91',yellow:'#d4b179',blue:'#81a1c1',magenta:'#ba9fc3',cyan:'#8bb8ba',white:'#f0f0f0'},
    linkHandler:{activate:openLink}});
  const fit=new FitAddon.FitAddon();terminal.loadAddon(fit);
  terminal.registerLinkProvider(codexLoginLinks(terminal, openLink));
  terminal.loadAddon(new WebLinksAddon.WebLinksAddon(openLink));
  terminal.open(box);
  const p={j:j.id,view,section,top,terminal,fit,attached:false,frame:0};
  p.cancelSelectionGesture=bindTerminalSelection(box,terminal);
  panes.set(j.id,p);
  terminal.onData(data=>{if(p.attached)post({op:'input',view,data})});
  terminal.attachCustomKeyEventHandler(event=>!event.metaKey);
  function measure(){
    if(!section.isConnected)return;
    const dims=fit.proposeDimensions();
    if(!dims||dims.cols<2||dims.rows<2)return;
    const changed=dims.cols!==terminal.cols||dims.rows!==terminal.rows;
    if(changed)terminal.resize(dims.cols,dims.rows);
    if(!p.attached){
      p.attached=true;
      post({op:'attach',view,id:j.id,cols:terminal.cols,rows:terminal.rows});
    }else if(changed)post({op:'resize',view,cols:terminal.cols,rows:terminal.rows});
  }
  p.observer=new ResizeObserver(()=>{cancelAnimationFrame(p.frame);p.frame=requestAnimationFrame(measure)});
  p.observer.observe(box);
  measure();
  return p;
}
function unmount(id){
  const p=panes.get(id);if(!p)return;
  p.cancelSelectionGesture();p.observer.disconnect();cancelAnimationFrame(p.frame);
  if(p.attached)post({op:'detach',view:p.view});
  panes.delete(id);p.terminal.dispose();p.section.remove();
}
function setView(ids){
  selected=[...new Set(ids)].filter(byID);
  if(selected.length&&!tabs.some(t=>keyOf(t)===keyOf(selected)))tabs.push([...selected]);
  tabStrip();
  const currentTab=$('#tabs .chosen');if(currentTab)currentTab.scrollIntoView({block:'nearest',inline:'nearest'});
  for(const id of [...panes.keys()])if(!selected.includes(id))unmount(id);
  head();renderNav();$('#menu').hidden=true;
  const desk=$('#desk'),n=selected.length;
  const cols=n<2?1:n<5?2:3;
  desk.style.gridTemplateColumns='repeat('+cols+',minmax(0,1fr))';
  desk.style.gridTemplateRows=n?'repeat('+Math.ceil(n/cols)+',minmax('+(n>4?'205px':'0')+',1fr))':'';
  for(const id of selected){
    const j=byID(id),p=panes.get(id)||mount(j);p.top.innerHTML=paneHead(j);
    if(desk.children[selected.indexOf(id)]!==p.section)desk.insertBefore(p.section,desk.children[selected.indexOf(id)]||null);
  }
  if(n)panes.get(selected[0]).terminal.focus();
}
window.overview=()=>setView([]);
window.toggleSidebar=()=>{
  const hidden=document.body.classList.toggle('hide-sidebar');
  $('#explorer').setAttribute('aria-expanded',String(!hidden));
};
function renderQuick(){
  const search=$('#filter').value.trim().toLowerCase();
  quickMatches=sessions.filter(j=>[j.title,j.project,tools[j.tool].name].some(s=>s.toLowerCase().includes(search)));
  quickIndex=Math.max(0,Math.min(quickIndex,quickMatches.length-1));
  $('#results').innerHTML=quickMatches.map((j,i)=>'<button id="match-'+i+'" role="option" tabindex="-1" aria-selected="'+(i===quickIndex)+'" data-action="match" data-id="'+esc(j.id)+'">'+mark(j.tool,true)+'<span class="name">'+esc(j.title)+'</span><small>'+esc(j.project)+'</small></button>').join('')||'<p class="empty">没有匹配的会话</p>';
  const active=$('#results [aria-selected="true"]');
  if(active){
    $('#filter').setAttribute('aria-activedescendant',active.id);
    active.scrollIntoView({block:'nearest'});
  }else $('#filter').removeAttribute('aria-activedescendant');
}
window.quickOpen=()=>{
  if($('#dialog').open)return;
  if(!$('#quick').open)$('#quick').showModal();
  $('#filter').value='';quickIndex=0;renderQuick();$('#filter').focus();
};
function openMatch(j){
  if(!j)return;
  $('#quick').close();
  if(online)setView([j.id]);else notify('请先重新连接工作台。');
}
function chooserTool(tool){
  return Object.keys(tools).map(t=>'<label class="choice"><input type="radio" name="tool" value="'+t+'" '+(t===tool?'checked':'')+'>'+mark(t,true)+esc(tools[t].name)+'</label>').join('');
}
function formField(label,name,value,placeholder=''){
  return '<label class="field">'+label+'<input type="text" name="'+name+'" maxlength="80" value="'+esc(value)+'" placeholder="'+esc(placeholder)+'" autocomplete="off"></label>';
}
function dialog(type,title,fields,submit,arg=null){
  const d=$('#dialog');if(d.open)d.close();
  modal={type,arg};$('#modalTitle').textContent=title;$('#fields').innerHTML=fields;
  $('#submit').textContent=submit;$('#submit').disabled=false;$('#formError').hidden=true;d.showModal();
}
window.newSession=(tool='claude')=>{
  if(!online){notify('请先连接固定出口。');return}
  const project=selected.length?byID(selected[0]).project:'main';
  dialog('new','新建会话','<div class="choices">'+chooserTool(tool)+'</div>'+formField('项目','project',project)+formField('任务名称','title','','留空自动命名'),'创建并打开');
  $('#fields input[name=title]').focus();
};
function compare(){
  const checked=new Set(selected);
  const project=selected.length?byID(selected[0]).project:'main';
  const fields='<button type="button" class="preset" data-action="preset" data-project="'+esc(project)+'">'+icon('split')+'每种工具各开一个<span>'+esc(project)+'</span></button><div class="compare-list">'+sessions.map(j=>'<label class="check-row"><input type="checkbox" name="jobs" value="'+j.id+'" '+(checked.has(j.id)?'checked':'')+'>'+mark(j.tool,true)+'<span class="name">'+esc(j.title)+'</span><small>'+esc(j.project)+' · '+esc(tools[j.tool].name)+'</small></label>').join('')+'</div>';
  dialog('compare','选择要并排查看的会话',fields,'并排打开');
}
function more(el,j){
  const box=$('#menu');
  box.innerHTML=(j.exited?'<button data-action="restart" data-id="'+j.id+'">重新启动</button>':'')+'<button data-action="rename" data-id="'+j.id+'">重命名</button><button class="danger" data-action="end" data-id="'+j.id+'">'+(j.exited?'移除已退出会话':'结束会话')+'</button>';
  box.hidden=false;const r=el.getBoundingClientRect();
  box.style.left=Math.max(5,Math.min(innerWidth-box.offsetWidth-7,r.right-box.offsetWidth))+'px';
  box.style.top=Math.min(innerHeight-box.offsetHeight-5,r.bottom+3)+'px';
}
document.addEventListener('click',async event=>{
  const el=event.target.closest('[data-action]');if(!el){$('#menu').hidden=true;return}
  const f=el.dataset.action,j=byID(el.dataset.id);
  if(f!=='more')$('#menu').hidden=true;
  if(f==='new')newSession(el.dataset.tool||'claude');
  if(f==='home')overview();
  if(f==='explorer')toggleSidebar();
  if(f==='search')quickOpen();
  if(f==='match')openMatch(j);
  if(f==='project'){
    const project=el.dataset.project;
    if(collapsedProjects.has(project))collapsedProjects.delete(project);else collapsedProjects.add(project);
    renderNav();
    [...$('#nav').querySelectorAll('.project')].find(b=>b.dataset.project===project)?.focus();
  }
  if(f==='tab')setView(el.dataset.key.split(','));
  if(f==='close-tab'){
    const key=el.dataset.key;tabs=tabs.filter(t=>keyOf(t)!==key);
    if(keyOf(selected)===key)setView(tabs[tabs.length-1]||[]);
    else tabStrip();
  }
  if(f==='preset'){
    el.disabled=true;
    try{const result=await request('preset',{project:el.dataset.project});$('#dialog').close();setView(result.ids)}
    catch(e){el.disabled=false;notify(e.message)}
  }
  if(f==='open'&&j){if(online)setView([j.id]);else notify('请先重新连接工作台。')}
  if(f==='compare')compare();
  if(f==='browser')browse();
  if(f==='more'&&j)more(el,j);
  if(f==='dismiss')$('#dialog').close();
  if(f==='reconnect' && exitSamples.terminal?.state!=='checking'){
    exitSamples.terminal={state:'checking'};renderExit('terminal');post({op:'startup'});
  }
  if(f==='check-browser' && exitSamples.browser?.state!=='checking'){
    exitSamples.browser={state:'checking'};renderExit('browser');post({op:'checkBrowser'});
  }
  if(f==='rename'&&j)dialog('rename','重命名会话',formField('任务名称','title',j.title),'保存',j.id);
  if(f==='end'&&j)dialog('end',j.exited?'移除已退出会话':'结束会话','<p class="message">'+(j.exited?'从列表移除「':'将结束「')+esc(j.title)+(j.exited?'」。':'」正在运行的进程。项目文件会保留。')+'</p>',j.exited?'移除':'结束会话',j.id);
  if(f==='restart'&&j){try{await request('restart',{id:j.id});setView([j.id])}catch(e){notify(e.message)}}
});
$('#form').addEventListener('submit',async event=>{
  event.preventDefault();const data=new FormData(event.target);let id=null;
  const err=$('#formError');err.hidden=true;$('#submit').disabled=true;
  try{
    if(modal.type==='compare'){const ids=data.getAll('jobs');if(ids.length<2)throw new Error('请勾选至少两个会话。');setView(ids)}
    if(modal.type==='new')id=(await request('create',{tool:data.get('tool'),project:data.get('project'),title:data.get('title')})).id;
    if(modal.type==='rename')await request('rename',{id:modal.arg,title:data.get('title')});
    if(modal.type==='end')await request('end',{id:modal.arg});
    $('#dialog').close();if(id)setView([id]);
  }catch(e){err.textContent=e.message;err.hidden=false}finally{$('#submit').disabled=false}
});
$('#dialog').addEventListener('close',()=>{modal=null});
$('#filter').addEventListener('input',()=>{quickIndex=0;renderQuick()});
$('#filter').addEventListener('keydown',e=>{
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){
    e.preventDefault();quickIndex+=e.key==='ArrowDown'?1:-1;renderQuick();
  }else if(e.key==='Enter'){e.preventDefault();openMatch(quickMatches[quickIndex])}
});
document.addEventListener('keydown',e=>{if(e.key==='Escape')$('#menu').hidden=true});
window.acceptEvents=events=>{
  for(const e of events){
    if(e.type==='output'||e.type==='disconnected'){
      const p=[...panes.values()].find(p=>p.view===e.view);if(!p)continue;
      if(e.type==='output'){const raw=atob(e.data);p.terminal.write(Uint8Array.from(raw,c=>c.charCodeAt(0)))}
      else if(p.attached){p.attached=false;p.terminal.writeln('\r\n[会话显示已断开，返回总览后可重新打开。]')}
    }else if(e.type==='sessions'){
      const sig=JSON.stringify(e.rows.map(j=>[j.id,j.project,j.tool,j.title,j.exited]));if(sig===snapshot&&loaded)continue;
      snapshot=sig;sessions=e.rows;loaded=true;renderHome();if($('#quick').open)renderQuick();
      if(selected.some(id=>!byID(id)))setView(selected.filter(byID));
      else{renderNav();head();tabStrip();for(const p of panes.values())p.top.innerHTML=paneHead(byID(p.j))}
    }else if(e.type==='network'){
      online=e.online;exitSamples.terminal=e;renderExit('terminal');
      head();
    }else if(e.type==='browserNetwork'){
      exitSamples.browser=e;renderExit('browser');
    }else if(e.type==='notice')notify(e.message);
    else if(e.type==='result'){
      const req=requests.get(e.request);if(!req)continue;
      requests.delete(e.request);clearTimeout(req.timer);e.ok?req.resolve(e):req.reject(new Error(e.message));
    }
  }
};
$('#tools').innerHTML=Object.keys(tools).map(t=>'<button class="tool" data-action="new" data-tool="'+t+'" aria-label="新建 '+esc(tools[t].name)+' 会话">'+mark(t)+'<span>'+esc(tools[t].name)+'</span>'+icon('plus')+'</button>').join('');
head();tabStrip();post({op:'startup'});
