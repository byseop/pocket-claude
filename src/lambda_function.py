"""
ec2-boot-manager Lambda - v4.

Power switch and viewport only. The instance starts Claude Code itself via
systemd, so this function never launches anything remotely. Every command
path returns without waiting on instance state: API Gateway's integration
timeout is 30s, and v3 exceeded it by blocking on an instance_running waiter
for 38.7s, which made Telegram retry and skip the launch entirely.

Commands: /start /stop /status /view
"""

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone

import boto3

AWS_REGION = os.environ.get('AWS_REGION', 'ap-northeast-2')
ec2 = boto3.client('ec2', region_name=AWS_REGION)
ssm = boto3.client('ssm', region_name=AWS_REGION)

# Read with a default so the module imports without AWS config, which the
# unit tests rely on. The handler rejects an empty value explicitly.
INSTANCE_ID = os.environ.get('INSTANCE_ID', '')

TG_MAX = 3900
SESSION = 'claude'
SERVICE = 'claude-telegram'

ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-B]')
AUTH_RE = re.compile(r'401|OAuth|Please run /login')

RECOVERY = (
    '   복구:\n'
    '   1. SSM 접속 후 sudo -u ubuntu -i\n'
    '   2. claude setup-token\n'
    '   3. umask 077\n'
    "      echo 'export CLAUDE_CODE_OAUTH_TOKEN=<토큰>' > ~/.claude/.env\n"
    '   4. sudo systemctl restart claude-telegram'
)


# --- pure helpers (unit tested, no AWS) ----------------------------------

def strip_ansi(text):
    """Remove ANSI escape sequences so tmux output is readable on mobile."""
    return ANSI_RE.sub('', text)


def detect_auth_error(pane_text):
    """Return the most recent auth-failure line, or None if auth looks fine.

    The process stays alive when the OAuth token is revoked, so liveness
    checks cannot see this failure. It only shows up on screen.
    """
    for line in reversed(pane_text.splitlines()):
        if AUTH_RE.search(line):
            return line.strip()
    return None


def format_uptime(launch_time, now):
    total = max(int((now - launch_time).total_seconds()), 0)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    return f'{hours}h {minutes}m' if hours else f'{minutes}m'


def format_error(command, exc):
    """Render an exception for Telegram.

    A crash that only reaches CloudWatch looks identical to a dead bot from
    the phone. Surfacing the exception is what makes the difference between
    "no reply" and a diagnosable failure.
    """
    detail = str(exc) or '(메시지 없음)'
    body = f'⚠️ {command} 처리 실패\n\n{type(exc).__name__}: {detail}'
    return body[:1200]


def build_status(state, uptime, service_active, auth_error):
    if state != 'running':
        return f'⚪ EC2 {state}\n   /start 로 켜세요.'

    lines = [f'✅ EC2 running ({uptime})']
    lines.append(
        f'✅ {SERVICE}.service active' if service_active
        else f'❌ {SERVICE}.service 정지됨'
    )
    if auth_error:
        lines.append(f'❌ 인증 실패\n   "{auth_error}"\n\n{RECOVERY}')
    else:
        lines.append('✅ 인증 OK')
    return '\n'.join(lines)


# --- telegram ------------------------------------------------------------

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


# --- aws -----------------------------------------------------------------

def describe():
    inst = ec2.describe_instances(
        InstanceIds=[INSTANCE_ID]
    )['Reservations'][0]['Instances'][0]
    return inst['State']['Name'], inst['LaunchTime']


def ssm_ready():
    info = ssm.describe_instance_information(
        Filters=[{'Key': 'InstanceIds', 'Values': [INSTANCE_ID]}]
    )['InstanceInformationList']
    return bool(info) and info[0]['PingStatus'] == 'Online'


def ssm_run(script, timeout=25):
    """Run a short read-only script and return stdout.

    Only used by /status and /view. Never used to launch anything: systemd
    owns startup now. The timeout ceiling keeps us well under API Gateway's
    30s integration limit.
    """
    cid = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName='AWS-RunShellScript',
        Parameters={'commands': [script], 'executionTimeout': ['60']},
    )['Command']['CommandId']

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        try:
            res = ssm.get_command_invocation(
                CommandId=cid, InstanceId=INSTANCE_ID
            )
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if res['Status'] in ('Success', 'Failed', 'TimedOut', 'Cancelled'):
            return res.get('StandardOutputContent', '') or res.get(
                'StandardErrorContent', ''
            )
    return ''


# --- commands ------------------------------------------------------------

def cmd_start(chat_id):
    state, _ = describe()
    if state == 'running':
        tg_send(chat_id, '⚠️ 이미 켜져 있어요. /status 로 확인하세요.')
        return
    if state != 'stopped':
        tg_send(chat_id, f'⚠️ 현재 상태: {state}. 잠시 후 다시 시도하세요.')
        return
    ec2.start_instances(InstanceIds=[INSTANCE_ID])
    tg_send(
        chat_id,
        '🔄 EC2를 켰습니다. 1분쯤 뒤 대화할 수 있어요.\n'
        '클로드는 systemd가 자동으로 띄웁니다.',
    )


def cmd_stop(chat_id):
    state, _ = describe()
    if state == 'stopped':
        tg_send(chat_id, '⚠️ 이미 꺼져 있어요.')
        return
    ec2.stop_instances(InstanceIds=[INSTANCE_ID])
    tg_send(chat_id, '🔄 EC2를 끕니다.')


def cmd_status(chat_id):
    state, launch = describe()
    if state != 'running':
        tg_send(chat_id, build_status(state, None, None, None))
        return
    if not ssm_ready():
        tg_send(chat_id, '🔄 부팅 중 — SSM 에이전트 대기 중입니다.')
        return

    out = ssm_run(
        f'systemctl is-active {SERVICE} || true\n'
        f'echo "---"\n'
        f'sudo -u ubuntu tmux capture-pane -t {SESSION} -p -S -200 2>/dev/null || true\n'
    )
    head, _, pane = out.partition('---')
    uptime = format_uptime(launch, datetime.now(timezone.utc))
    tg_send(
        chat_id,
        build_status(
            state, uptime, head.strip() == 'active', detect_auth_error(pane)
        ),
    )


def cmd_view(chat_id):
    state, _ = describe()
    if state != 'running':
        tg_send(chat_id, f'⚪ EC2 {state} — /start 로 켜세요.')
        return
    if not ssm_ready():
        tg_send(chat_id, '🔄 부팅 중 — SSM 에이전트 대기 중입니다.')
        return
    out = ssm_run(
        f'sudo -u ubuntu tmux capture-pane -t {SESSION} -p 2>/dev/null || echo "(세션 없음)"'
    )
    tg_send(chat_id, strip_ansi(out).rstrip() or '(빈 화면)')


HELP = (
    'pocket-claude\n\n'
    '/start   EC2 켜기\n'
    '/stop    EC2 끄기\n'
    '/status  상태 + 인증 확인\n'
    '/view    클로드 화면 보기'
)

HANDLERS = {
    '/start': cmd_start,
    '/stop': cmd_stop,
    '/status': cmd_status,
    '/view': cmd_view,
}


def lambda_handler(event, context):
    if not INSTANCE_ID:
        return {'statusCode': 500, 'body': 'INSTANCE_ID is not configured'}

    # Parse defensively: a malformed body must not raise, or Telegram retries
    # the same update forever against a handler that will never accept it.
    try:
        body = json.loads(event.get('body') or '{}')
        message = body.get('message') or body.get('edited_message') or {}
        chat_id = str((message.get('chat') or {}).get('id', ''))
        text = (message.get('text') or '').strip().split('@')[0]
    except (ValueError, AttributeError):
        return {'statusCode': 200, 'body': 'unparseable'}

    if chat_id != os.environ['ALLOWED_CHAT_ID']:
        return {'statusCode': 200, 'body': 'ignored'}

    handler = HANDLERS.get(text)
    if not handler:
        if text.startswith('/'):
            tg_send(chat_id, HELP)
        return {'statusCode': 200, 'body': 'ok'}

    # Any failure below reaches the phone. Returning 200 regardless keeps
    # Telegram from redelivering an update we already answered.
    try:
        handler(chat_id)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all
        try:
            tg_send(chat_id, format_error(text, exc))
        except Exception:  # noqa: BLE001 - Telegram itself is down
            pass
        return {'statusCode': 200, 'body': 'error reported'}

    return {'statusCode': 200, 'body': 'ok'}
