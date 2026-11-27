'use strict';
const groupUI=(()=>{
  let catalog,names,room=null,roomId='',draft=true,choices={},chosen={},timer,pending=false,reading=false,draftText='',version=0,latestRooms=[];
  // Native WebKit transports dictionaries without a stable property order.
  const order=['mimo','deepseek','glm','kimi','gpt','gemini','claude'];
  const statusName={running:'讨论中',paused:'已暂停',human:'等你回答',approval:'等你决定',done:'已总结',error:'等待重试'};
  const label={question:'原题',ask:'提问',answer:'回答',review:'总结',final:'最终答案',decision:'你的决定',extend:'申请加轮',speak:'发言'};
  const avatar=k=>k==='human'?'<span class="room-avatar room-user">我</span>':'<img class="room-avatar" alt="" src="model-icons/'+esc(k)+'.png">';
  const call=(cmd,extra={})=>request('room',{cmd,room:roomId,revision:room?.revision,...extra});
  function list(rows){
    latestRooms=rows;
    $('#roomList').innerHTML='<option value="">新讨论</option>'+rows.map(r=>'<option value="'+r.id+'">'+esc(r.title)+' · '+statusName[r.status]+'</option>').join('');
    $('#roomList').value=draft?'':roomId;
  }
  function members(){
    $('#roomMembers').innerHTML=order.filter(k=>draft||Object.hasOwn(room.members,k)).map(k=>{
      const options=catalog[k];
      const index=draft?choices[k]:room.members[k];
      const editable=draft||['paused','error','human','approval'].includes(room.status);
      return '<div class="roster-row">'+avatar(k)+'<label>'+esc(names[k])+(draft?'<input type="checkbox" data-member="'+k+'" '+(chosen[k]?'checked':'')+' '+(k==='claude'?'disabled':'')+'>':'')+'</label><select aria-label="'+esc(names[k])+' 型号" data-model="'+k+'" '+(editable?'':'disabled')+'>'+options.map((o,i)=>'<option value="'+i+'" '+(i===index?'selected':'')+'>'+esc(o.provider+' · '+o.label)+'</option>').join('')+'</select></div>';
    }).join('');
  }
  const button=(cmd,title,main=false)=>'<button class="'+(main?'primary':'secondary')+'" data-room="'+cmd+'">'+title+'</button>';
  function render(){
    members();
    const feed=$('#roomFeed'),controls=$('#roomControls');
    const nearEnd=feed.scrollHeight-feed.scrollTop-feed.clientHeight<75;
    const prior=controls.querySelector('textarea')?.value||'';
    if(draft){
      feed.innerHTML='<div class="room-intro"><h2>这个问题，你想听听谁的看法？</h2><p>按右侧顺序讨论至少两轮。成员可以点名提问，最后由 Claude 汇总。</p></div>';
      controls.innerHTML='<textarea id="roomInput" aria-label="讨论主题" placeholder="提出一个问题…"></textarea><div class="room-buttons">'+button('create','开始讨论',true)+'</div>';
      $('#roomInput').value=draftText;return;
    }
    let n=0;
    feed.innerHTML=room.messages.map(m=>{
      let heading='';if(m.round!==n){n=m.round;heading='<div class="room-round">第 '+n+' 轮</div>'}
      return heading+'<article class="room-message '+(m.kind==='final'?'room-final':'')+'">'+avatar(m.who)+'<div><header><b>'+esc(names[m.who])+'</b><small>'+esc((m.choice?m.choice.provider+' · '+m.choice.label+' · ':'')+label[m.kind]+(m.target?' @'+names[m.target]:''))+'</small></header><p>'+esc(m.text)+'</p></div></article>';
    }).join('');
    if(nearEnd||room.messages.length<3)feed.scrollTop=feed.scrollHeight;
    const turn=room.stack.at(-1),status=room.status;
    let text=statusName[status],buttons='';
    if(status==='running'){
      text=room.pause_after?'本次发言结束后暂停':'第 '+room.round+' 轮 · '+names[turn.who]+' 正在'+(turn.role==='review'||turn.role==='final'?'总结':'发言');
      if(!room.pause_after)buttons=button('pause','暂停');
    }else if(status==='human'){text=names[room.stack.at(-2).who]+' 正在等你回答';buttons=button('answer','发送回答',true)}
    else if(status==='approval'){buttons=button('finish','直接总结')+button('approve','同意再讨论一轮',true)}
    else if(status==='paused'||status==='error'){text=names[turn.who]+' · '+(room.error||text);buttons=button('resume',status==='error'?'重试这次发言':'继续讨论',true)}
    controls.innerHTML=(status==='human'?'<textarea id="roomInput" aria-label="你的回答" placeholder="回答群里的提问…"></textarea>':'')+
      '<div class="room-buttons"><span class="room-state" role="status">'+esc(text)+'</span>'+buttons+'</div>';
    if(status==='human'&&prior)$('#roomInput').value=prior;
  }
  async function view(initial=false){
    if(pending||!roomOpen)return;
    const current=++version;
    const res=await call('view',{catalog:!catalog});
    if(current!==version||!roomOpen)return;
    if(res.models){catalog=res.models;names=res.names;for(const k in catalog){choices[k]=0;chosen[k]=true}}
    list(res.rooms);
    if(res.room){room=res.room;render()}else if(initial&&catalog){render()}
  }
  async function open(){
    try{await view(true)}catch(e){notify(e.message)}
    clearInterval(timer);timer=setInterval(async()=>{if(!roomOpen||draft||reading)return;reading=true;try{await view()}catch(e){notify(e.message)}finally{reading=false}},1400);
  }
  function newDraft(){
    draftText='';
    ++version;draft=true;room=null;roomId='';list(latestRooms);if(catalog)render();$('#roomInput')?.focus();
  }
  function bind(){
    $('#roomControls').oninput=e=>{if(draft&&e.target.id==='roomInput')draftText=e.target.value};
    $('#roomNew').onclick=()=>{if(!pending)newDraft()};
    $('#roomList').onchange=async e=>{
      if(pending)return;
      const id=e.target.value;if(!id){newDraft();return}
      ++version;roomId=id;room=null;draft=false;
      try{await view()}catch(err){notify(err.message)}
    };
    async function act(cmd,extra={}){
      if(pending)return;
      pending=true;++version;
      try{
        const res=await call(cmd,extra);
        room=res.room;roomId=room.id;draft=false;draftText='';list(res.rooms);render();
      }catch(e){notify(e.message)}finally{pending=false}
    }
    $('#roomMembers').onchange=e=>{
      if(e.target.dataset.member){chosen[e.target.dataset.member]=e.target.checked;return}
      const who=e.target.dataset.model;if(!who)return;
      if(draft)choices[who]=Number(e.target.value);else act('model',{who,index:Number(e.target.value)});
    };
    $('#roomControls').onclick=e=>{
      const cmd=e.target.closest('[data-room]')?.dataset.room;if(!cmd)return;
      const text=$('#roomInput')?.value?.trim();
      if(cmd==='create'){
        if(!text){notify('请输入讨论主题');return}
        const members=Object.fromEntries(order.filter(k=>chosen[k]).map(k=>[k,choices[k]]));
        act(cmd,{question:text,members});
      }else if(cmd==='answer'){if(text)act(cmd,{text});else notify('请输入你的回答')}
      else act(cmd);
    };
  }
  return {open,bind};
})();
