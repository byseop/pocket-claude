"""
ec2-boot-manager Lambda - v5.

Power switch plus session-unit manager. The instance runs Claude Code
Remote Control server sessions under systemd (claude-rc@<name>); the phone
talks to those sessions through the Claude app, never through this bot.
SSM is used only to flip units on and off and to read state, never to
launch Claude directly: process lifetime belongs to systemd (see the v3
post-mortem in docs/OPERATIONS.md). Nothing here blocks for long, because
API Gateway's integration timeout is 30s.

Commands: /start /stop /status /sessions /new <name> /kill <name> /rm <name>
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
OPS = 'ops'
UNIT_PREFIX = 'claude-rc@'
SESSION_PREFIX = 'gamer4-'
REPO = '/home/ubuntu/gamer4info'
WORKTREES = '/home/ubuntu/worktrees'
PROJECTS = '/home/ubuntu/.claude/projects'
CLAUDE_ENV = '/home/ubuntu/.claude/.env'
CLAUDE_CREDS = '/home/ubuntu/.claude/.credentials.json'

# Session names become unit names, tmux session names, branch names and
# shell words inside SSM scripts. The character class is the injection guard.
NAME_RE = re.compile(r'^[a-z0-9-]{1,24}$')

ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-B]')
AUTH_RE = re.compile(r'401|OAuth|Please run /login')

# v4 leftovers. Task 4 rewrites /status and deletes these two lines and the
# old RECOVERY text; until then the existing build_status tests must pass.
SESSION = 'claude'
SERVICE = 'claude-telegram'

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


def parse_command(text):
    """Split '/new@bot feat-x' into ('/new', 'feat-x')."""
    head, _, arg = text.strip().partition(' ')
    return head.split('@')[0], arg.strip()


def validate_name(name):
    """Return an error message for a bad session name, or None when fine."""
    if not name:
        return '세션 이름이 필요해요. 예: /new feat-x'
    if not NAME_RE.match(name):
        return '이름은 소문자·숫자·하이픈 1~24자만 돼요.'
    if name == OPS:
        return f'{OPS} 는 예약된 이름이에요. 부팅 시 자동으로 뜹니다.'
    return None


def workdir(name):
    return REPO if name == OPS else f'{WORKTREES}/{name}'


def as_ubuntu(body):
    """Wrap a POSIX-sh body so SSM (root, /bin/sh) runs it as ubuntu.

    A quoted heredoc keeps the body out of the outer shell's quoting rules,
    so the script text is exactly what the tests see.
    """
    return f"sudo -u ubuntu -H sh <<'EOF'\n{body}\nEOF\n"


def new_session_script(name):
    d = workdir(name)
    return as_ubuntu(
        'set -e\n'
        f'if [ ! -d {d} ]; then\n'
        f'  git -C {REPO} fetch -q origin main || true\n'
        f'  if git -C {REPO} show-ref --verify --quiet refs/heads/{name}; then\n'
        f'    git -C {REPO} worktree add {d} {name}\n'
        '  else\n'
        f'    git -C {REPO} worktree add {d} -b {name} origin/main\n'
        '  fi\n'
        'fi\n'
        f'[ -f {d}/.env ] || cp {REPO}/.env {d}/.env\n'
        f'sudo systemctl start {UNIT_PREFIX}{name}\n'
        f'systemctl is-active {UNIT_PREFIX}{name} || true'
    )


def kill_session_script(name):
    return as_ubuntu(
        f'sudo systemctl stop {UNIT_PREFIX}{name}\n'
        f'systemctl is-active {UNIT_PREFIX}{name} || true'
    )


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

def cmd_start(chat_id, arg):
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


def cmd_stop(chat_id, arg):
    state, _ = describe()
    if state == 'stopped':
        tg_send(chat_id, '⚠️ 이미 꺼져 있어요.')
        return
    ec2.stop_instances(InstanceIds=[INSTANCE_ID])
    tg_send(chat_id, '🔄 EC2를 끕니다.')


def cmd_status(chat_id, arg):
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


def cmd_view(chat_id, arg):
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


def require_running(chat_id):
    """Common gate for unit commands. Returns True when SSM can be used."""
    state, _ = describe()
    if state != 'running':
        tg_send(chat_id, f'⚪ EC2 {state} — /start 로 먼저 켜세요.')
        return False
    if not ssm_ready():
        tg_send(chat_id, '🔄 부팅 중 — SSM 에이전트 대기 중입니다. 잠시 후 다시.')
        return False
    return True


def cmd_new(chat_id, arg):
    err = validate_name(arg)
    if err:
        tg_send(chat_id, f'⚠️ {err}')
        return
    if not require_running(chat_id):
        return
    # Wait only for `systemctl start` to return. Whether the session is
    # online is /sessions' job; API Gateway gives us 30s in total.
    out = ssm_run(new_session_script(arg), timeout=20)
    tg_send(
        chat_id,
        f'🔄 {SESSION_PREFIX}{arg} 기동 중 ({out.strip() or "?"}).\n'
        '30초 뒤 앱 Code 탭에서 확인하세요. /sessions 로 상태 조회.',
    )


def cmd_kill(chat_id, arg):
    err = validate_name(arg)
    if err:
        tg_send(chat_id, f'⚠️ {err}')
        return
    if not require_running(chat_id):
        return
    out = ssm_run(kill_session_script(arg), timeout=20)
    tg_send(
        chat_id,
        f'⏹ {SESSION_PREFIX}{arg} 정지 ({out.strip() or "?"}).\n'
        '워크트리와 브랜치는 남아 있어요. 지우려면 /rm.',
    )


HELP = (
    'pocket-claude v5\n\n'
    '/start          EC2 켜기 (ops 세션 자동 기동)\n'
    '/stop           EC2 끄기\n'
    '/status         EC2·유닛·인증 상태\n'
    '/sessions       세션 목록 (브랜치·마지막 대화)\n'
    '/new <name>     워크트리 세션 만들기\n'
    '/kill <name>    세션 정지 (워크트리 유지)\n'
    '/rm <name>      세션 정지 + 워크트리 삭제'
)

HANDLERS = {
    '/start': cmd_start,
    '/stop': cmd_stop,
    '/status': cmd_status,
    '/view': cmd_view,
    '/new': cmd_new,
    '/kill': cmd_kill,
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
        text = message.get('text') or ''
    except (ValueError, AttributeError):
        return {'statusCode': 200, 'body': 'unparseable'}

    if chat_id != os.environ['ALLOWED_CHAT_ID']:
        return {'statusCode': 200, 'body': 'ignored'}

    command, arg = parse_command(text)
    handler = HANDLERS.get(command)
    if not handler:
        if command.startswith('/'):
            tg_send(chat_id, HELP)
        return {'statusCode': 200, 'body': 'ok'}

    # Any failure below reaches the phone. Returning 200 regardless keeps
    # Telegram from redelivering an update we already answered.
    try:
        handler(chat_id, arg)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all
        try:
            tg_send(chat_id, format_error(command, exc))
        except Exception:  # noqa: BLE001 - Telegram itself is down
            pass
        return {'statusCode': 200, 'body': 'error reported'}

    return {'statusCode': 200, 'body': 'ok'}
