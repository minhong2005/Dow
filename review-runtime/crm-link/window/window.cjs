'use strict';
// Share one locally selected work window using the workstation's existing identity.
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const { setTimeout: delay } = require('node:timers/promises');

async function main() {
  if (process.platform !== 'win32') throw new Error('请在 China Windows 上打开。');
  const root = path.join(process.env.LOCALAPPDATA, 'Dow CRM');
  const installed = path.join(root, 'Workstation');
  const { identity, post } = require(path.join(installed, 'device.cjs'));
  const cfg = JSON.parse(fs.readFileSync(path.join(installed, 'device.json'), 'utf8'));
  const token = identity(cfg.seed, fs.readFileSync(path.join(root, 'device-id'), 'utf8').trim());
  let host = cfg.address;
  async function send(msg) {
    let failure;
    for (const address of new Set([host, cfg.serverName, cfg.address])) {
      try {
        const reply = await post(cfg, address, token, msg);
        host = address;
        return reply;
      } catch (e) {
        if (e.status === 400 || e.status === 403) throw e;
        failure = e;
      }
    }
    throw failure;
  }
  const panel = spawn(process.env.DOW_WINDOW_PANEL || path.join(__dirname,'WindowShare.exe'), ['--panel'], {stdio:['pipe','pipe','inherit']});
  let session = null, state = 'unknown', open = true, waiting = null, lastMessage = '';
  function notice(message) {
    if (lastMessage !== message) { console.log(message); lastMessage = message; }
  }
  function release() {
    const previous = session;
    session = null;
    state = 'unknown';
    if (waiting) waiting.finish(null);
    if (previous) send({op:'windowStop',session:previous}).catch(()=>{});
  }
  const closed = () => { open = false; release(); };
  panel.on('exit',closed);
  panel.on('error',e=>{console.error(e.message); closed();});
  panel.stdin.on('error',closed);
  const lines = readline.createInterface({input:panel.stdout});
  lines.on('line', line => {
    try {
      const m = JSON.parse(line);
      if (m.kind === 'share' && /^[0-9a-f]{32}$/.test(m.session)) {
        release(); session = m.session;
      } else if (m.kind === 'windowState' && m.session === session) {
        state = m.state;
      } else if (m.kind === 'stop' && m.session === session) {
        release(); notice('Windows 用户已接手。');
      } else if (m.kind === 'reply' && waiting && m.id === waiting.id && m.session === session) {
        const text = String(m.text);
        waiting.finish(Buffer.byteLength(text) <= 60000 ? {ok:m.ok===true,text}
                      : {ok:false,text:'窗口内容过多，请缩小窗口或折叠部分内容，再查看。'});
      }
    } catch { notice('窗口面板返回的数据无法识别。'); }
  });
  async function act(task, lease, remaining) {
    if (!/^[0-9a-f]{32}$/.test(task.id) || lease !== session || remaining < 100) return null;
    return new Promise(resolve => {
      let timer;
      function finish(value) { clearTimeout(timer); waiting=null; resolve(value); }
      waiting={id:task.id,finish}; timer=setTimeout(()=>finish(null),remaining);
      panel.stdin.write(JSON.stringify({session:lease,id:task.id,args:task.args,deadline:Date.now()+remaining})+'\n');
    });
  }
  try {
    while(open) {
      const lease = session;
      if (!lease) { await delay(180); continue; }
      try {
        const started = performance.now();
        const response = await send({op:'windowPoll',session:lease,version:2,state});
        if (lease !== session || !open) continue;
        if (response.busy) { notice('另一个窗口正在共享，稍后重试。'); await delay(900); continue; }
        notice('窗口已交给 Dow，可以随时点「我来接手」。');
        if (!response.task) { await delay(500); continue; }
        const remaining=Math.floor(response.task.remainingMs - (performance.now()-started));
        const reply=await act(response.task,lease,remaining);
        if (reply && lease===session) await send({op:'windowResult',session:lease,id:response.task.id,reply});
      } catch(e) {
        notice('连接 Dow 失败：'+String(e.code || e.message)+'。请保持 Mac 上的 Dow 打开。');
        await delay(1200);
      }
    }
  } finally { panel.stdin.end(); lines.close(); }
}
main().catch(e=>{console.error(e.message);process.exitCode=1;});
