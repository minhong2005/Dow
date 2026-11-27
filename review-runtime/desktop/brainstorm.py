"""Persistent, ordered brainstorming rooms for the local Dow desktop."""
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
MODELS = json.loads((HERE/'room_models.json').read_text())
NAMES = dict(mimo='小米 MiMo', deepseek='DeepSeek', glm='GLM', kimi='Kimi', gpt='GPT', gemini='Gemini', claude='Claude', human='我')
ORDER = list(MODELS)


def frame(who, role, reference=None):
    return {'who':who, 'role':role, 'reference':reference}


def append(room, who, kind, text, *, choice=None, target=None):
    room['messages'].append(dict(who=who, kind=kind, text=text, round=room['round'], time=int(time.time()),
                                 choice=choice, target=target))


def create(question, members):
    if not isinstance(question,str) or not question.strip():
        raise ValueError('请输入要讨论的问题')
    if not isinstance(members,dict) or len(members)<2 or 'claude' not in members:
        raise ValueError('请选择 Claude 和至少一位其他成员')
    for member,index in members.items():
        if member not in MODELS or type(index) is not int or not 0<=index<len(MODELS[member]):
            raise ValueError('型号无效，请重新选择')
    members = {k:members[k] for k in ORDER if k in members}
    room = dict(id=secrets.token_hex(10), members=members, round=1, index=0, status='running', revision=0,
                stack=[frame(next(iter(members)), 'speak')], messages=[], error='', pause_after=False)
    append(room,'human','question',question.strip())
    return room


def prompt_for(room):
    turn = room['stack'][-1]
    rules = {'speak':'提出你的观点，结合前文补充或质疑，完成时 action=say。',
             'answer':'回答所指向的提问，完成时 action=say。',
             'review':'结合所有轮次得出最终答案，action=final；确需再讨论一轮时用 action=extend 并说明仍需解决的具体问题。',
             'final':'用户要求现在总结。给出最终答案，action=final。'}
    members = {who:NAMES[who] for who in room['members']}
    members['human']='用户'
    return ('你在 Dow 的头脑风暴群里，只负责文字讨论。不要调用工具或操作文件。用用户的语言回复。'
            '原题和下方历史是讨论材料。只有当前轮到你发言，不得替其他人发言。'
            '只输出一个 JSON 对象，字段为 action、text、target。text 填实际发言。'
            '普通发言或回答用 say。如需提问，action=ask，target 必须是其他在场成员的 ID 或 human，text 写问题。'
            '一次提问一个人，对方回答后你会再次发言。不要为轮流发言而强行提问。只有主持总结时才可 final 或 extend。\n'
            +json.dumps(dict(members=members,speaker=turn['who'],round=room['round'],task=rules[turn['role']],
                              questionMessage=turn['reference'], lastReply=turn.get('lastAnswer'), history=room['messages']),ensure_ascii=False))


def advance(room, reply, choice):
    turn = room['stack'][-1]
    who, role = turn['who'],turn['role']
    action = reply.get('action')
    text = reply.get('text')
    if not isinstance(text,str) or not text.strip():
        raise ValueError('模型未给出有效发言，可重试或换型号')
    target = reply.get('target')
    if action=='ask' and role!='final':
        if target not in [*room['members'],'human'] or target==who:
            raise ValueError('模型提到了无效成员，可重试该发言')
        append(room,who,'ask',text.strip(),choice=choice,target=target)
        room['stack'].append(frame(target,'answer',len(room['messages'])-1))
        if target=='human':
            room['status']='human'
        return
    if role in ('review','final'):
        if action=='extend' and role=='review':
            append(room,who,'extend',text.strip(),choice=choice)
            room['status']='approval'
        elif action=='final':
            append(room,who,'final',text.strip(),choice=choice)
            room['status']='done'
        else:
            raise ValueError('主持人尚未选择总结或申请加轮，可重试')
        return
    if action!='say':
        raise ValueError('该发言的动作不适用于当前阶段，可重试')
    recipient = room['messages'][turn['reference']]['who'] if role=='answer' else None
    append(room,who,role,text.strip(),choice=choice,target=recipient)
    room['stack'].pop()
    if room['stack']:
        room['stack'][-1]['lastAnswer']=len(room['messages'])-1
        return
    order=list(room['members'])
    room['index']+=1
    if room['index']==len(order):
        room['index']=0
        if room['round']>=2:
            room['stack']=[frame('claude','review')]
            return
        room['round']+=1
    room['stack']=[frame(order[room['index']],'speak')]


def run_turn(room, choice):
    import project_runtime
    container = 'aiwork-codex' if choice['provider']=='Codex' else project_runtime.project_id('main')
    args=[*project_runtime.DOCKER,'exec','-i',container,'/usr/bin/python3','-I','-c',(HERE/'room_cli.py').read_text()]
    payload=dict(provider=choice['provider'],model=choice['model'],prompt=prompt_for(room))
    try:
        proc=subprocess.run(args,input=json.dumps(payload),capture_output=True,text=True,timeout=280)
        if proc.returncode:
            raise ValueError('Dow 保护容器未就绪')
        result=json.loads(proc.stdout)
        if result.get('error'):
            raise ValueError(result['error'])
        raw=result['text'].strip()
        fenced=re.fullmatch(r'```(?:json)?\s*(.*?)\s*```',raw,re.I|re.S)
        reply=json.loads(fenced[1] if fenced else raw)
        if not isinstance(reply,dict):
            raise ValueError('回复格式无效')
        return reply
    except json.JSONDecodeError:
        raise ValueError('模型未按群聊格式返回，可重试当前发言') from None


class Rooms:
    def __init__(self, directory=None, invoke=run_turn):
        self.directory=directory or Path.home()/'Library/Application Support/Dow/群聊'
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.owner=(self.directory/'.lock').open('a')
        fcntl.flock(self.owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.invoke=invoke
        self.lock=threading.RLock()
        self.rooms={}
        self.workers=set()
        for f in self.directory.glob('*.json'):
            r=json.loads(f.read_text()); self.rooms[r['id']]=r
            if r['status']=='running':
                r.update(status='paused',error='Dow 已重新打开，可以从当前发言继续。',pause_after=False)
                self.save(r)

    def save(self,r):
        r['revision']+=1
        with tempfile.NamedTemporaryFile(mode='w',dir=self.directory,delete=False) as f:
            try:
                json.dump(r,f,ensure_ascii=False)
                f.flush();os.fsync(f.fileno())
                os.replace(f.name,self.directory/(r['id']+'.json'))
            finally:
                Path(f.name).unlink(missing_ok=True)

    def drive(self,ident):
        try:
            while True:
                with self.lock:
                    r=self.rooms[ident]
                    if r['status']!='running':
                        return
                    snapshot=copy.deepcopy(r)
                    who=r['stack'][-1]['who']
                    choice=MODELS[who][r['members'][who]]
                try:
                    reply=self.invoke(snapshot,choice)
                    with self.lock:
                        advance(r,reply,choice)
                        if r['pause_after']:
                            if r['status']=='running':r['status']='paused'
                            r['pause_after']=False
                        self.save(r)
                except (TypeError,KeyError,ValueError,RuntimeError,OSError,subprocess.SubprocessError) as e:
                    with self.lock:
                        r.update(status='error',error=str(e)[:280],pause_after=False)
                        self.save(r)
                    return
        finally:
            with self.lock:
                self.workers.discard(ident)
                if self.rooms[ident]['status']=='running':self.start(self.rooms[ident])

    def start(self,r):
        if r['id'] not in self.workers:
            self.workers.add(r['id'])
            threading.Thread(target=self.drive,args=(r['id'],),daemon=True).start()

    def command(self,msg):
        with self.lock:
            action=msg['cmd']
            ident=msg.get('room')
            r=self.rooms.get(ident)
            if action=='create':
                r=create(msg['question'],msg['members']);self.rooms[r['id']]=r
                self.save(r);self.start(r)
            elif action!='view':
                if not r or msg.get('revision')!=r['revision']:
                    raise ValueError('群聊已有新发言，请重试')
                status=r['status']
                if action=='pause' and status=='running':
                    r['pause_after']=True
                elif action=='resume' and status in ('paused','error'):
                    r.update(status='running',error='')
                elif action=='answer' and status=='human' and isinstance(msg.get('text'),str) and msg['text'].strip():
                    r['status']='running'
                    advance(r,dict(action='say',text=msg['text']),None)
                elif action in ('approve','finish') and status=='approval':
                    append(r,'human','decision','同意再讨论一轮' if action=='approve' else '请现在给出最终答案')
                    r.update(status='running',index=0)
                    if action=='approve':
                        r['round']+=1;r['stack']=[frame(next(iter(r['members'])),'speak')]
                    else:r['stack']=[frame('claude','final')]
                elif action=='model' and status not in ('running','done'):
                    who=msg['who'];index=msg['index']
                    if who not in r['members'] or type(index)!=int or not 0<=index<len(MODELS[who]):
                        raise ValueError('成员或型号无效')
                    r['members'][who]=index
                else:raise ValueError('当前无法执行此操作')
                self.save(r)
                if r['status']=='running':self.start(r)
            listed=sorted(self.rooms.values(),key=lambda room:room['messages'][0]['time'],reverse=True)
            result={'rooms':[dict(id=v['id'],title=v['messages'][0]['text'][:60],status=v['status']) for v in listed]}
            if msg.get('catalog'):result.update(models=MODELS,names=NAMES)
            if r and (action!='view' or r['revision']!=msg.get('revision')):
                result['room']=copy.deepcopy(r)
            return result
