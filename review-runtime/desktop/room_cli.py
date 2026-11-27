"""One isolated text turn. Executed inside the selected Dow CLI container."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def invoke(provider, model, prompt):
    data = prompt
    if provider == 'Claude Code':
        cmd = ['claude', '-p', '--tools', '',
               '--strict-mcp-config', '--no-session-persistence', '--model', model, '--output-format', 'json']
    elif provider == 'Antigravity':
        denied = [action+'(*)' for action in ('command','mcp','read_file','write_file','read_url','execute_url')]
        cmd = ['agy', '--model', model, '--mode', 'plan', '--settings', json.dumps({'permissions':{'deny':denied}}), '--disable-slash-commands', '--print-timeout', '240s',
               '--input-format','stream-json','--output-format','stream-json']
        data = json.dumps({'event':'user','message':{'content':prompt}})+'\n'
    elif provider == 'Codex':
        cmd = ['codex','exec','--ephemeral','--skip-git-repo-check','--ignore-user-config','-s','read-only',
               '-c','web_search="disabled"','-c','features.shell_tool=false','-m',model,'-o','answer.txt','-']
    else:
        raise ValueError('unknown provider')
    with tempfile.TemporaryDirectory(prefix='dow-discussion-') as work:
        p = subprocess.run(cmd, input=data, text=True, cwd=work, capture_output=True, timeout=260)
        if provider == 'Codex' and p.returncode == 0:
            return (Path(work)/'answer.txt').read_text().strip()
        if provider == 'Antigravity':
            rows = [json.loads(line) for line in p.stdout.splitlines() if line.startswith('{')]
            result = next((r['result'] for r in reversed(rows) if r.get('event')=='result'), {})
            if p.returncode == 0 and result.get('status')=='SUCCESS':
                return result['response'].strip()
            detail = result.get('error','')
        else:
            try:
                result = json.loads(p.stdout)
                if isinstance(result,list):
                    result = next((r for r in reversed(result) if r.get('type')=='result'),{})
                if not p.returncode and not result.get('is_error') and result.get('type')=='result':
                    return result['result'].strip()
                detail = str(result.get('result',''))
            except (ValueError, AttributeError):
                detail = ''
        details = (detail+' '+p.stderr).lower()
        for pattern, reason in [('401','登录凭据无效'),('not logged in','需要登录'),('authentication','需要核对登录'),
                ('429','额度或速率受限'),('quota','额度受限'),('rate limit','速率受限'),
                ('403','服务拒绝访问'),('context','上下文或配置错误'),('model','当前型号不可用或模型请求失败')]:
            if pattern in details:
                raise RuntimeError(reason)
        raise RuntimeError('CLI 未返回有效发言，请检查该工具的登录和连接')


if __name__ == '__main__':
    try:
        args=json.load(sys.stdin)
        print(json.dumps({'text':invoke(args['provider'],args['model'],args['prompt'])}))
    except subprocess.TimeoutExpired:
        print(json.dumps({'error':'模型响应超时'}))
    except (OSError,RuntimeError,ValueError,KeyError,TypeError) as error:
        print(json.dumps({'error':str(error) if isinstance(error, RuntimeError) else 'CLI 输出或运行环境异常'}))
