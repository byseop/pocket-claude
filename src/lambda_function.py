"""
ec2-boot-manager Lambda — v3 (Slim).

Role: SYSTEM LAYER ONLY.
  - EC2 lifecycle (on/off, status)
  - Main session resurrection (restart_main)
  - System diagnostics (auth/daemon/sessions)
  - Help

Application-layer work (background agent spawn/list/cmd/stop/...)
is handled by the MAIN claude session via the telegram MCP channel plugin
(natural-language interaction). See ~/.claude/CLAUDE.md on the EC2 host.

Endpoints kept from v1/v2:
  /start_ec2  /stop_ec2  /status  /help

New:
  /restart_main   — kill+respawn the main tmux 'claude' session (EC2 stays on)
  /diag           — auth + ec2 + main-session + daemon + bg-sessions summary
"""

import boto3
import json
import os
import re
import shlex
import time
import urllib.request

# AWS_REGION is injected by the Lambda runtime; the fallback covers local runs
AWS_REGION = os.environ.get('AWS_REGION', 'ap-northeast-2')

ec2 = boto3.client('ec2', region_name=AWS_REGION)
ssm = boto3.client('ssm', region_name=AWS_REGION)

INSTANCE_ID = os.environ['INSTANCE_ID']
CLAUDE_BIN = '/home/ubuntu/.local/bin/claude'
JOBS_DIR = '/home/ubuntu/.claude/jobs'
BOOT_SCRIPT = '/home/ubuntu/start-claude-telegram.sh'
MAIN_TMUX = 'claude'

TG_MAX = 3900


# ─── Telegram ──────────────────────────────────────────────────────────────

def tg_send(chat_id, text):
    token = os.environ['TELEGRAM_TOKEN']
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    for i in range(0, max(len(text), 1), TG_MAX):
        chunk = text[i:i + TG_MAX] or ' '
        data = json.dumps({'chat_id': chat_id, 'text': chunk}).encode()
        req = urllib.request.Request(
            url, data=data, headers={'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req, timeout=10)


# ─── EC2 ──────────────────────────────────────────────────────────────────

def ec2_state():
    desc = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    return desc['Reservations'][0]['Instances'][0]['State']['Name']


def ec2_public_ip():
    desc = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    return desc['Reservations'][0]['Instances'][0].get('PublicIpAddress')


# ─── SSM ──────────────────────────────────────────────────────────────────

def ssm_run(commands, timeout=30, poll_interval=2):
    cmd = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName='AWS-RunShellScript',
        Parameters={
            'commands': commands,
            'executionTimeout': [str(timeout)],
        },
    )
    cid = cmd['Command']['CommandId']
    time.sleep(1)
    deadline = time.time() + timeout + 10
    last = None
    while time.time() < deadline:
        try:
            last = ssm.get_command_invocation(CommandId=cid, InstanceId=INSTANCE_ID)
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
    return f'sudo -u ubuntu bash -lc {shlex.quote(inner)}'


# ─── ANSI strip ────────────────────────────────────────────────────────────

ANSI_RE = re.compile(
    r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()=]|[\x00-\x08\x0e-\x1f]'
)


def strip_ansi(s):
    return ANSI_RE.sub('', s or '')


# ─── Main-session restart ─────────────────────────────────────────────────

def restart_main_session():
    inner = (
        f'tmux kill-session -t {MAIN_TMUX} 2>/dev/null || true ; '
        f'tmux new-session -d -s {MAIN_TMUX} {shlex.quote(BOOT_SCRIPT)} ; '
        'sleep 2 ; tmux ls 2>&1'
    )
    return ssm_run([ubuntu_cmd(inner)], timeout=30)


# ─── Diagnostics ──────────────────────────────────────────────────────────

def collect_diag():
    inner = (
        'set +e ; '
        'echo "===AUTH===" ; '
        'jq -r ".claudeAiOauth | '
        '\\"expiresAt=\\(.expiresAt) (now=\\((now*1000)|floor)) '
        'subscription=\\(.subscriptionType)\\"" '
        '~/.claude/.credentials.json 2>/dev/null || echo "creds: missing" ; '
        f'{CLAUDE_BIN} auth status 2>&1 | head -8 ; '
        'echo ; echo "===MAIN_SESSION===" ; '
        f'tmux ls 2>&1 | head -10 ; '
        f'echo "--- main tail ---" ; '
        f'tmux capture-pane -t {MAIN_TMUX} -p 2>/dev/null | tail -8 || echo "(no main tmux)" ; '
        'echo ; echo "===DAEMON===" ; '
        f'{CLAUDE_BIN} daemon status 2>&1 | head -10 ; '
        'echo ; echo "===BG_SESSIONS===" ; '
        f'BG=$({CLAUDE_BIN} agents --json 2>/dev/null) ; '
        'echo "count=$(echo $BG | jq length 2>/dev/null || echo ?)" ; '
        f'for d in {JOBS_DIR}/*/ ; do '
        '  id=$(basename "$d") ; '
        '  jq -r --arg id "$id" '
        '    "\\"[\\($id)] \\(.name // \\"?\\") · \\(.state // \\"?\\") · \\(.needs // .detail // .intent // \\"\\")\\"" '
        '    "$d/state.json" 2>/dev/null ; '
        'done'
    )
    res = ssm_run([ubuntu_cmd(inner)], timeout=30)
    return strip_ansi(res['stdout'])


# ─── Handler ───────────────────────────────────────────────────────────────

HELP_TEXT = """🤖 EC2 Boot Manager (시스템 레이어)

[EC2 라이프사이클]
/start_ec2     EC2 시작 + 메인 세션 부트
/stop_ec2      메인 세션 종료 + EC2 정지
/status        EC2 상태

[복구]
/restart_main  메인 클로드 tmux 세션 재시작 (EC2는 켜진 채)
/diag          인증·EC2·메인·데몬·BG 세션 진단

/help          이 도움말

— Background agent 관리·일상 대화는 메인 클로드 봇과 대화하세요."""


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
    if text in ('/help', '/start'):
        tg_send(chat_id, HELP_TEXT)
        return

    if text == '/status':
        st = ec2_state()
        emoji = {'running': '🟢', 'stopped': '🔴', 'pending': '🟡', 'stopping': '🟡'}.get(st, '⚪')
        ip = ec2_public_ip()
        msg = f'{emoji} EC2: {st}'
        if ip:
            msg += f'\nPublic IP: {ip}'
        tg_send(chat_id, msg)
        return

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
                    f"sudo -u ubuntu tmux new-session -d -s {MAIN_TMUX} '{BOOT_SCRIPT}'",
                ],
                'executionTimeout': ['60'],
            },
        )
        tg_send(chat_id, '✅ EC2 켜졌어. 메인 클로드 부팅 중...')
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

    if text == '/restart_main':
        if ec2_state() != 'running':
            tg_send(chat_id, '⚠️ EC2가 꺼져있음. /start_ec2 먼저')
            return
        tg_send(chat_id, '🔄 메인 클로드 세션 재시작 중...')
        res = restart_main_session()
        out = strip_ansi(res['stdout'])[-1500:]
        tg_send(chat_id, f'✅ 재시작 완료\n\n{out}' if res['status'] == 'Success' else f'⚠️ 재시작 결과 {res["status"]}\n\n{out}')
        return

    if text == '/diag':
        if ec2_state() != 'running':
            tg_send(chat_id, f'🩺 EC2: {ec2_state()} — 켜져있어야 진단 가능')
            return
        out = collect_diag()
        tg_send(chat_id, f'🩺 진단\n\n{out[-3500:]}' if out else '⚠️ 진단 데이터 없음')
        return

    tg_send(chat_id, f'❓ 모르는 명령: {text.split()[0]}\n\n/help')
