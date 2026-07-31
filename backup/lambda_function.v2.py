"""
Telegram → API Gateway → Lambda → EC2 (claude background agents) controller.

v2 changes from v1:
- Keeps legacy commands: /start_ec2, /stop_ec2, /status, /logs (main session), /cmd (main session)
- Adds multi-session background agents:
    /new <prompt>             — claude --bg
    /new -n <name> <prompt>   — claude --bg --name <name>
    /list                     — claude agents --json + state.json
    /sess <id>                — full state.json one entry
    /logs <id>                — timeline.jsonl last text (clean, no ANSI)
    /raw <id>                 — claude logs <id> (ANSI raw, debug)
    /cmd <id> <text>          — tmux wrapper: attach + send-keys
    /stop <id>                — claude stop
    /rm <id>                  — claude rm
    /respawn <id|all>         — claude respawn
    /diag                     — auth/daemon + every session's state.needs
    /help                     — command list
"""

import boto3
import json
import os
import re
import shlex
import time
import urllib.request

ec2 = boto3.client('ec2', region_name='ap-northeast-2')
ssm = boto3.client('ssm', region_name='ap-northeast-2')

INSTANCE_ID = os.environ['INSTANCE_ID']  # sanitized for public repo
CLAUDE_BIN = '/home/ubuntu/.local/bin/claude'
JOBS_DIR = '/home/ubuntu/.claude/jobs'
DEFAULT_FLAGS = '--dangerously-skip-permissions'
LEGACY_TMUX = 'claude'  # main interactive session name (boot script tmux)

TG_MAX = 3900  # leave headroom under 4096


# ─── Telegram helpers ──────────────────────────────────────────────────────

def tg_send(chat_id, text, parse_mode=None):
    token = os.environ['TELEGRAM_TOKEN']
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    for i in range(0, max(len(text), 1), TG_MAX):
        chunk = text[i:i + TG_MAX] or ' '
        payload = {'chat_id': chat_id, 'text': chunk}
        if parse_mode:
            payload['parse_mode'] = parse_mode
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data, headers={'Content-Type': 'application/json'}
        )
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            # Retry without parse_mode if formatting was the issue
            if parse_mode:
                payload.pop('parse_mode', None)
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    url, data=data, headers={'Content-Type': 'application/json'}
                )
                urllib.request.urlopen(req, timeout=10)
            else:
                raise


# ─── EC2 helpers ───────────────────────────────────────────────────────────

def ec2_state():
    desc = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    return desc['Reservations'][0]['Instances'][0]['State']['Name']


# ─── SSM helpers ───────────────────────────────────────────────────────────

def ssm_run(commands, timeout=30, poll_interval=2):
    """Run shell commands on EC2 via SSM RunShellScript, blocking until done.

    Returns dict with status / stdout / stderr.
    """
    cmd = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName='AWS-RunShellScript',
        Parameters={
            'commands': commands,
            'executionTimeout': [str(timeout)],
        },
    )
    cid = cmd['Command']['CommandId']
    # SSM needs ~1s before invocation appears
    time.sleep(1)
    deadline = time.time() + timeout + 10
    last = None
    while time.time() < deadline:
        try:
            last = ssm.get_command_invocation(
                CommandId=cid, InstanceId=INSTANCE_ID
            )
        except Exception:
            time.sleep(poll_interval)
            continue
        if last['Status'] in ('Success', 'Failed', 'Cancelled', 'TimedOut'):
            return {
                'status': last['Status'],
                'stdout': (last.get('StandardOutputContent') or '').strip(),
                'stderr': (last.get('StandardErrorContent') or '').strip(),
            }
        time.sleep(poll_interval)
    return {
        'status': last['Status'] if last else 'Timeout',
        'stdout': (last.get('StandardOutputContent') or '').strip() if last else '',
        'stderr': (last.get('StandardErrorContent') or '').strip() if last else '',
    }


def ubuntu_cmd(inner):
    """Wrap a shell command to run as ubuntu with login shell + bun/PATH."""
    return f'sudo -u ubuntu bash -lc {shlex.quote(inner)}'


# ─── Background agent operations ───────────────────────────────────────────

SHORT_ID_RE = re.compile(r'^[0-9a-f]{8}$')


def is_short_id(s):
    return bool(SHORT_ID_RE.match(s or ''))


def bg_new(prompt, name=None):
    name_arg = f'--name {shlex.quote(name)} ' if name else ''
    inner = (
        f'cd /tmp && '
        f'{CLAUDE_BIN} --bg {name_arg}{DEFAULT_FLAGS} '
        f'{shlex.quote(prompt)} 2>&1'
    )
    res = ssm_run([ubuntu_cmd(inner)], timeout=40)
    out = res['stdout']
    short = None
    for line in out.splitlines():
        m = re.search(r'backgrounded\s*[··]\s*([0-9a-f]{8})', line)
        if m:
            short = m.group(1)
            break
    return short, out


def bg_list():
    inner = f'{CLAUDE_BIN} agents --json 2>&1'
    res = ssm_run([ubuntu_cmd(inner)], timeout=15)
    try:
        return json.loads(res['stdout']), None
    except Exception:
        return None, res['stdout'] or res['stderr']


def bg_state(short_id):
    """Read state.json for a session — clean structured info."""
    inner = f'cat {JOBS_DIR}/{shlex.quote(short_id)}/state.json 2>&1'
    res = ssm_run([ubuntu_cmd(inner)], timeout=10)
    try:
        return json.loads(res['stdout']), None
    except Exception:
        return None, res['stdout']


def bg_last_text(short_id, tail=1):
    """Read the last `text` field from timeline.jsonl — clean, no ANSI."""
    inner = (
        f'tail -n {tail} {JOBS_DIR}/{shlex.quote(short_id)}/timeline.jsonl '
        f'2>/dev/null | jq -r ".text // .detail // empty" 2>/dev/null'
    )
    res = ssm_run([ubuntu_cmd(inner)], timeout=10)
    return res['stdout']


def bg_raw_logs(short_id):
    """`claude logs <id>` raw terminal output. Strips most ANSI for readability."""
    inner = f'{CLAUDE_BIN} logs {shlex.quote(short_id)} 2>&1 | tail -c 8000'
    res = ssm_run([ubuntu_cmd(inner)], timeout=15)
    return strip_ansi(res['stdout'])


def bg_stop(short_id):
    return ssm_run(
        [ubuntu_cmd(f'{CLAUDE_BIN} stop {shlex.quote(short_id)} 2>&1')],
        timeout=15,
    )['stdout']


def bg_rm(short_id):
    return ssm_run(
        [ubuntu_cmd(f'{CLAUDE_BIN} rm {shlex.quote(short_id)} 2>&1')],
        timeout=15,
    )['stdout']


def bg_respawn(target):
    arg = '--all' if target == 'all' else shlex.quote(target)
    return ssm_run(
        [ubuntu_cmd(f'{CLAUDE_BIN} respawn {arg} 2>&1')],
        timeout=30,
    )['stdout']


def bg_cmd(short_id, text):
    """Send a follow-up input to a running bg session via tmux wrapper.

    Each bg session gets its own tmux session named bg-<id> with
    `claude attach <id>` inside, then send-keys + capture.
    """
    tname = f'bg-{short_id}'
    inner = (
        f'tmux has-session -t {tname} 2>/dev/null || '
        f'tmux new-session -d -s {tname} '
        f'{shlex.quote(CLAUDE_BIN + " attach " + short_id)} ; '
        f'sleep 2 ; '
        f'tmux send-keys -t {tname} {shlex.quote(text)} Enter ; '
        f'sleep 5 ; '
        f'tmux capture-pane -t {tname} -p | tail -40'
    )
    res = ssm_run([ubuntu_cmd(inner)], timeout=25)
    return strip_ansi(res['stdout'])


# ─── ANSI stripping ────────────────────────────────────────────────────────

ANSI_RE = re.compile(
    r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()=]|[\x00-\x08\x0e-\x1f]'
)


def strip_ansi(s):
    return ANSI_RE.sub('', s or '')


# ─── Auth / daemon diagnostics ─────────────────────────────────────────────

def diag_all():
    """Auth status + daemon status + all sessions' needs/state."""
    inner = (
        'set +e ; '
        'echo ===AUTH=== ; '
        f'jq -r ".claudeAiOauth | \\"expiresAt=\\(.expiresAt) (\\(now*1000 - .expiresAt | tostring) ms ago)\\"" '
        '~/.claude/.credentials.json 2>/dev/null ; '
        f'{CLAUDE_BIN} auth status 2>&1 | head -10 ; '
        'echo ===DAEMON=== ; '
        f'{CLAUDE_BIN} daemon status 2>&1 | head -12 ; '
        'echo ===SESSIONS=== ; '
        f'for d in {JOBS_DIR}/*/ ; do '
        '  id=$(basename "$d") ; '
        '  jq -r --arg id "$id" '
        '    "[$id, .name, .state, (.needs // .detail // .intent // \\"\\")] | @tsv" '
        '    "$d/state.json" 2>/dev/null ; '
        'done'
    )
    res = ssm_run([ubuntu_cmd(inner)], timeout=25)
    return res['stdout']


# ─── Formatting ────────────────────────────────────────────────────────────

STATE_EMOJI = {
    'working': '⚙️',
    'needs_input': '⚠️',
    'blocked': '🚫',
    'done': '✅',
    'idle': '💤',
    'completed': '✅',
    'failed': '❌',
    'stopped': '⏹',
}


def fmt_list(rows):
    if not rows:
        return '🗒 background agents: 없음\n\n새 세션 시작: /new <프롬프트>'
    lines = [f'🗒 background agents ({len(rows)}개)\n']
    for r in rows:
        sid = (r.get('sessionId') or '')[:8]
        name = r.get('name') or '(unnamed)'
        status = r.get('status') or '?'
        emoji = STATE_EMOJI.get(status, '·')
        lines.append(f'{emoji} {sid} · {name} · {status}')
    lines.append('\n/logs <id> · /sess <id> · /cmd <id> <텍스트> · /stop <id> · /rm <id>')
    return '\n'.join(lines)


def fmt_sess(st):
    sid = st.get('daemonShort', '????????')
    name = st.get('name', '(unnamed)')
    state = st.get('state', '?')
    emoji = STATE_EMOJI.get(state, '·')
    intent = st.get('intent', '')
    detail = st.get('detail', '')
    needs = st.get('needs', '')
    output = ''
    if isinstance(st.get('output'), dict):
        output = st['output'].get('result', '') or ''
    lines = [
        f'{emoji} {sid} · {name}',
        f'state: {state}',
        f'intent: {intent}' if intent else '',
        f'needs: ⚠️ {needs}' if needs else '',
        f'detail: {detail}' if detail else '',
        f'\nresult:\n{output}' if output else '',
    ]
    return '\n'.join(l for l in lines if l)


HELP_TEXT = """🤖 명령어 도움말

[EC2]
/start_ec2 · /stop_ec2 · /status

[메인 세션 (telegram plugin)]
/logs            메인 세션 tmux 화면
/cmd <텍스트>     메인 세션에 입력 전달

[Background agents]
/new <프롬프트>            새 bg 세션
/new -n <이름> <프롬프트>   이름 지정
/list                     모든 세션 목록
/sess <id>                세션 상세 (state.json)
/logs <id>                세션 최근 응답 (clean)
/raw <id>                 원본 터미널 로그 (디버그)
/cmd <id> <텍스트>         세션에 입력
/stop <id>                세션 정지
/rm <id>                  세션 삭제
/respawn <id|all>         세션 재시작

[진단]
/diag           인증/데몬/모든 세션 상태
/help           이 도움말"""


# ─── Command parsing ───────────────────────────────────────────────────────

def parse_new(args):
    """Returns (name, prompt) from `[-n <name>] <prompt>`."""
    name = None
    if len(args) >= 3 and args[0] == '-n':
        name = args[1]
        prompt = ' '.join(args[2:])
    else:
        prompt = ' '.join(args)
    return name, prompt.strip()


# ─── Main handler ──────────────────────────────────────────────────────────

def lambda_handler(event, context):
    body = json.loads(event.get('body', '{}'))
    message = body.get('message') or body.get('edited_message') or {}
    chat_id = str(message.get('chat', {}).get('id', ''))
    text = (message.get('text') or '').strip()

    allowed_id = os.environ['ALLOWED_CHAT_ID']
    if chat_id != allowed_id or not text:
        return {'statusCode': 200, 'body': 'ok'}

    try:
        handle(chat_id, text)
    except Exception as e:
        tg_send(chat_id, f'❌ 핸들러 에러: {type(e).__name__}: {e}')

    return {'statusCode': 200, 'body': 'ok'}


def handle(chat_id, text):
    if text == '/help' or text == '/start':
        tg_send(chat_id, HELP_TEXT)
        return

    # EC2 control ─────────────────────────────────────────────────────────
    if text == '/start_ec2':
        st = ec2_state()
        if st == 'running':
            tg_send(chat_id, '⚠️ 이미 실행 중')
            return
        if st != 'stopped':
            tg_send(chat_id, f'⚠️ 현재 상태: {st}')
            return
        tg_send(chat_id, '🔄 EC2 시작 중...')
        ec2.start_instances(InstanceIds=[INSTANCE_ID])
        ec2.get_waiter('instance_running').wait(InstanceIds=[INSTANCE_ID])
        time.sleep(20)
        ssm.send_command(
            InstanceIds=[INSTANCE_ID],
            DocumentName='AWS-RunShellScript',
            Parameters={
                'commands': [
                    'pgrep -f "claude" >/dev/null && exit 0',
                    "sudo -u ubuntu tmux new-session -d -s claude '/home/ubuntu/start-claude-telegram.sh'",
                ],
                'executionTimeout': ['60'],
            },
        )
        tg_send(chat_id, '✅ EC2 켜졌어! Claude Code 실행 중...')
        return

    if text == '/stop_ec2':
        st = ec2_state()
        if st == 'stopped':
            tg_send(chat_id, '⚠️ 이미 꺼져있음')
            return
        if st != 'running':
            tg_send(chat_id, f'⚠️ 현재 상태: {st}')
            return
        tg_send(chat_id, '🔄 종료 중...')
        try:
            ssm.send_command(
                InstanceIds=[INSTANCE_ID],
                DocumentName='AWS-RunShellScript',
                Parameters={
                    'commands': [
                        'pkill -f claude || true',
                        'sudo -u ubuntu tmux kill-server || true',
                    ],
                    'executionTimeout': ['15'],
                },
            )
            time.sleep(5)
        except Exception:
            pass
        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        tg_send(chat_id, '✅ EC2 종료됨')
        return

    if text == '/status':
        st = ec2_state()
        emoji = {'running': '🟢', 'stopped': '🔴', 'pending': '🟡', 'stopping': '🟡'}.get(st, '⚪')
        tg_send(chat_id, f'{emoji} EC2: {st}')
        return

    # Background-agent ops require EC2 running ────────────────────────────
    ec2_st = ec2_state() if text != '/diag' else None
    needs_ec2 = text.startswith(('/new', '/list', '/sess', '/logs', '/raw', '/cmd', '/stop', '/rm', '/respawn', '/diag'))
    if needs_ec2 and ec2_state() != 'running':
        tg_send(chat_id, f'⚠️ EC2가 켜져있지 않음. /start_ec2')
        return

    # Background agent commands ───────────────────────────────────────────
    parts = text.split(None, 1)
    cmd = parts[0]
    rest = parts[1] if len(parts) > 1 else ''

    if cmd == '/new':
        if not rest:
            tg_send(chat_id, '사용법: /new [-n <이름>] <프롬프트>')
            return
        try:
            args = shlex.split(rest)
        except ValueError:
            args = rest.split()
        name, prompt = parse_new(args)
        if not prompt:
            tg_send(chat_id, '프롬프트가 비었음')
            return
        tg_send(chat_id, f'🔄 dispatching{(" (" + name + ")") if name else ""}...')
        short, raw = bg_new(prompt, name)
        if short:
            tg_send(chat_id, f'✅ {short}{(" · " + name) if name else ""}\n\n/logs {short} · /sess {short} · /cmd {short} <텍스트>')
        else:
            tg_send(chat_id, f'❌ dispatch 실패\n\n{raw[-1000:]}')
        return

    if cmd == '/list':
        rows, err = bg_list()
        if rows is None:
            tg_send(chat_id, f'❌ /list 실패\n{err[-1500:]}')
            return
        tg_send(chat_id, fmt_list(rows))
        return

    if cmd == '/sess':
        if not is_short_id(rest.strip()):
            tg_send(chat_id, '사용법: /sess <8자리 id>')
            return
        st, err = bg_state(rest.strip())
        if st is None:
            tg_send(chat_id, f'❌ 세션 없음\n{err[-500:]}')
            return
        tg_send(chat_id, fmt_sess(st))
        return

    if cmd == '/logs':
        arg = rest.strip()
        if not arg:
            # legacy: main session tmux
            out = ssm_run(
                [ubuntu_cmd('tmux capture-pane -t claude -p 2>&1 | tail -30')],
                timeout=10,
            )['stdout']
            tg_send(chat_id, f'📋 메인 세션 로그:\n\n{strip_ansi(out)[-3500:]}' if out else '⚠️ 로그 없음')
            return
        if not is_short_id(arg):
            tg_send(chat_id, '사용법: /logs [<id>]')
            return
        txt = bg_last_text(arg)
        if not txt:
            tg_send(chat_id, '⚠️ 텍스트 응답 없음. /raw 또는 /sess 시도')
            return
        tg_send(chat_id, f'📋 {arg}\n\n{txt[-3500:]}')
        return

    if cmd == '/raw':
        if not is_short_id(rest.strip()):
            tg_send(chat_id, '사용법: /raw <id>')
            return
        out = bg_raw_logs(rest.strip())
        tg_send(chat_id, f'📺 raw {rest.strip()}\n\n{out[-3500:]}' if out else '⚠️ 비어있음')
        return

    if cmd == '/cmd':
        # /cmd <id> <text>  OR  legacy /cmd <text>
        sub = text[5:].strip()
        if not sub:
            tg_send(chat_id, '사용법: /cmd [<id>] <텍스트>')
            return
        first, *rest_words = sub.split(None, 1)
        if is_short_id(first) and rest_words:
            short, body = first, rest_words[0]
            out = bg_cmd(short, body)
            tg_send(chat_id, f'⌨️ → {short}: {body}\n\n📋 {out[-3500:]}' if out else f'⌨️ 전송됨: {short}')
            return
        # legacy main-session cmd
        user_input = sub
        escaped = user_input.replace("'", "'\\''")
        cmd_resp = ssm_run(
            [ubuntu_cmd(
                f"tmux send-keys -t {LEGACY_TMUX} '{escaped}' Enter ; "
                f"sleep 4 ; tmux capture-pane -t {LEGACY_TMUX} -p | tail -20"
            )],
            timeout=20,
        )['stdout']
        cmd_resp = strip_ansi(cmd_resp)
        tg_send(chat_id, f'⌨️ 메인 세션 입력: {user_input}\n\n📋 {cmd_resp[-3500:]}')
        return

    if cmd == '/stop':
        if not is_short_id(rest.strip()):
            tg_send(chat_id, '사용법: /stop <id>')
            return
        out = bg_stop(rest.strip())
        tg_send(chat_id, f'⏹ {out[-300:]}')
        return

    if cmd == '/rm':
        if not is_short_id(rest.strip()):
            tg_send(chat_id, '사용법: /rm <id>')
            return
        out = bg_rm(rest.strip())
        tg_send(chat_id, f'🗑 {out[-300:]}')
        return

    if cmd == '/respawn':
        arg = rest.strip()
        if arg != 'all' and not is_short_id(arg):
            tg_send(chat_id, '사용법: /respawn <id|all>')
            return
        out = bg_respawn(arg)
        tg_send(chat_id, f'🔁 {out[-500:]}')
        return

    if cmd == '/diag':
        out = diag_all()
        tg_send(chat_id, f'🩺 진단\n\n{out[-3500:]}' if out else '⚠️ 진단 정보 없음')
        return

    # Unknown
    tg_send(chat_id, f'❓ 모르는 명령: {cmd}\n\n/help')
