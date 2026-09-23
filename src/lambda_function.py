"""
ec2-boot-manager Lambda - v6.

Power switch plus a thin caller for `pocket`, the CLI on the box that owns
the box's layout (projects under ~/work, systemd units, worktrees). This
Lambda never builds git or systemd commands itself: it validates the
project name, runs one `pocket` verb over SSM, parses the JSON envelope
between the ===POCKET-BEGIN===/===POCKET-END=== sentinel lines, and turns
it into Korean text. SSM is used only to call `pocket` and read state,
never to launch Claude directly: process lifetime belongs to systemd (see
the v3 post-mortem in docs/OPERATIONS.md). Nothing here blocks for long,
because API Gateway's integration timeout is 30s.

Commands: /start /stop /status /projects /up <name> /down <name> /trees <name>
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

POCKET = '/home/ubuntu/bin/pocket'
BEGIN, END = '===POCKET-BEGIN===', '===POCKET-END==='
SSM_STDOUT_LIMIT = 24000

# A project name becomes a systemd instance name, a directory name and a
# shell word inside SSM scripts. The character class is the injection
# guard; `pocket` checks the same rule again once the value reaches the box.
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,23}$')

RECOVERY_TOKEN = (
    '   복구 (SSM 셸, ubuntu 사용자):\n'
    '   1. ~/.claude/.env 에서 CLAUDE_CODE_OAUTH_TOKEN 줄 제거\n'
    '   2. sudo systemctl restart claude-rc@<프로젝트>.service'
)
RECOVERY_LOGIN = (
    '   복구 (SSM 셸, ubuntu 사용자):\n'
    '   1. claude auth login   (claude.ai 선택)\n'
    '   2. pocket up <프로젝트>'
)


# --- pure helpers (unit tested, no AWS) ----------------------------------

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
    """Split '/up@bot feat-x' into ('/up', 'feat-x')."""
    head, _, arg = text.strip().partition(' ')
    return head.split('@')[0], arg.strip()


def validate_name(name):
    """Return an error message for a bad project name, or None when fine."""
    if not name:
        return '프로젝트 이름이 필요해요. 예: /up feat-x, /down feat-x'
    if not NAME_RE.fullmatch(name):
        return '이름은 소문자·숫자·하이픈 1~24자, 첫 글자는 소문자나 숫자여야 해요.'
    return None


def format_age(epoch, now):
    secs = max(int(now - epoch), 0)
    if secs < 60:
        return '방금'
    if secs < 3600:
        return f'{secs // 60}분 전'
    if secs < 86400:
        return f'{secs // 3600}시간 전'
    return f'{secs // 86400}일 전'


def pocket_script(verb, *args):
    """POSIX sh that runs one pocket verb as ubuntu and prints its envelope."""
    words = ' '.join([verb, *args]).strip()
    return f'sudo -u ubuntu -H {POCKET} {words} --json 2>&1 || true\n'


def parse_pocket(out):
    """Last complete envelope from a mixed stdout, or an error envelope."""
    if len(out) >= SSM_STDOUT_LIMIT:
        return {'ok': False, 'error': '박스 응답이 잘렸어요. 잠시 후 다시 시도하세요.'}
    if BEGIN not in out or END not in out:
        tail = out.strip()[-300:]
        return {'ok': False, 'error': f'박스에서 응답을 읽지 못했어요.\n{tail}'}
    body = out.rsplit(BEGIN, 1)[1].split(END, 1)[0]
    try:
        return json.loads(body)
    except ValueError:
        return {'ok': False, 'error': '박스 응답을 해석하지 못했어요.'}


def format_projects(rows):
    if not rows:
        return '프로젝트가 없어요. 박스의 ~/work 아래에 리포 폴더나 링크를 두세요.'
    now = time.time()
    lines = []
    for p in rows:
        dot = '🟢' if p['unit'] == 'active' else '⚪'
        last = format_age(p['last_activity'], now) if p['last_activity'] else '대화 없음'
        line = f"{dot} {p['name']}  [{p['branch']}]  {last}"
        if p['worktrees']:
            line += f"  워크트리 {p['worktrees']}"
        if not p['trusted']:
            line += '\n   ⚠️ 신뢰 필요 — SSM 셸에서 pocket trust ' + p['name']
        lines.append(line)
    return '\n'.join(lines)


def format_status(data, uptime):
    mem, disk, auth = data['mem'], data['disk'], data['auth']
    lines = [f"✅ EC2 running ({uptime})"]
    servers = data.get('servers') or []
    if servers:
        for s in servers:
            dot = '🟢' if s['unit'] == 'active' else '⚪'
            lines.append(f"{dot} {s['name']} {s['unit']}")
    else:
        lines.append('⚪ 켜진 서버 없음')
    lines.append(f"프로젝트 {data['projects']}개 · 동시 한도 {data['max_servers']}")
    lines.append(f"메모리 여유 {mem['available_mb']}MB / {mem['total_mb']}MB · 디스크 여유 {disk['free_gb']}GB")
    if auth['token_in_env']:
        lines.append('❌ ~/.claude/.env 에 CLAUDE_CODE_OAUTH_TOKEN 이 남아 있어요 (Remote Control 차단)')
        lines.append(RECOVERY_TOKEN)
    elif not auth['creds']:
        lines.append('❌ claude.ai 로그인이 없어요')
        lines.append(RECOVERY_LOGIN)
    else:
        lines.append('✅ 인증 OK')
    return '\n'.join(lines)


def format_trees(data):
    trees = data.get('trees') or []
    if not trees:
        return f"{data['name']}: 세션 워크트리가 없어요."
    lines = [f"{data['name']} 워크트리 {len(trees)}개"]
    for t in trees:
        marks = []
        if t['dirty']:
            marks.append('미커밋')
        if t['unpushed']:
            marks.append('미푸시')
        # Same order as pocket's keep reasons, so a tree the phone shows
        # marked is a tree prune will refuse for the first reason listed.
        if t.get('locked'):
            marks.append('잠김')
        if t['in_use']:
            marks.append('사용 중')
        lines.append(f"· {t['branch']}  {' '.join(marks) or '정리 가능'}")
    return '\n'.join(lines)


def keyboard_for(rows):
    """One row of buttons per project. callback_data stays well under the
    64-byte limit because names are at most 24 characters."""
    keys = []
    for p in rows:
        if p['unit'] == 'active':
            first = {'text': f"⏹ {p['name']}", 'callback_data': f"down:{p['name']}"}
        else:
            first = {'text': f"▶ {p['name']}", 'callback_data': f"up:{p['name']}"}
        row = [first]
        if p['worktrees']:
            row.append({'text': '🌳', 'callback_data': f"trees:{p['name']}"})
        keys.append(row)
    return {'inline_keyboard': keys}


# --- telegram ------------------------------------------------------------

def tg_api(method, payload):
    token = os.environ['TELEGRAM_TOKEN']
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    urllib.request.urlopen(req, timeout=10)


def tg_send(chat_id, text, keyboard=None):
    # A long reply arrives on the phone as stacked messages, and Telegram
    # scrolls to the newest one — so the keyboard belongs on the last chunk,
    # not the first, or it ends up off-screen.
    chunks = [text[i:i + TG_MAX] or ' ' for i in range(0, max(len(text), 1), TG_MAX)]
    for i, chunk in enumerate(chunks):
        payload = {'chat_id': chat_id, 'text': chunk}
        if keyboard and i == len(chunks) - 1:
            payload['reply_markup'] = keyboard
        tg_api('sendMessage', payload)


def tg_answer_callback(callback_id):
    """Telegram shows a spinner on the button until this is called, so it runs
    before the SSM round trip, not after."""
    try:
        tg_api('answerCallbackQuery', {'callback_query_id': callback_id})
    except Exception:  # noqa: BLE001 - a missing ack must not fail the command
        pass


def tg_edit_markup(chat_id, message_id, keyboard):
    try:
        tg_api('editMessageReplyMarkup', {'chat_id': chat_id, 'message_id': message_id,
                                          'reply_markup': keyboard})
    except Exception:  # noqa: BLE001 - the list message may be gone
        pass


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
    """Run a short script and return stdout.

    Used to call `pocket`. Never used to launch Claude itself: systemd owns
    startup. The timeout ceiling keeps us well under API Gateway's 30s
    integration limit.
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
    tg_send(chat_id, '🔄 EC2를 켰습니다. 1분쯤 뒤 /status 로 확인하세요.')


def cmd_stop(chat_id, arg):
    state, _ = describe()
    if state == 'stopped':
        tg_send(chat_id, '⚠️ 이미 꺼져 있어요.')
        return
    ec2.stop_instances(InstanceIds=[INSTANCE_ID])
    tg_send(chat_id, '🔄 EC2를 끕니다.')


def require_running(chat_id):
    """Common gate for pocket commands. Returns True when SSM can be used."""
    state, _ = describe()
    if state != 'running':
        tg_send(chat_id, f'⚪ EC2 {state} — /start 로 먼저 켜세요.')
        return False
    if not ssm_ready():
        tg_send(chat_id, '🔄 부팅 중 — SSM 에이전트 대기 중입니다. 잠시 후 다시.')
        return False
    return True


def run_pocket(chat_id, verb, *args):
    """Run one pocket verb on the box. Returns the envelope, or None when the
    instance is not reachable (the user was already told why)."""
    if not require_running(chat_id):
        return None
    out = ssm_run(pocket_script(verb, *args), timeout=20)
    return parse_pocket(out)


def reply_envelope(chat_id, env, formatter, keyboard=None):
    if env is None:
        return
    if not env.get('ok'):
        tg_send(chat_id, f"⚠️ {env.get('error') or '알 수 없는 오류'}")
        return
    tg_send(chat_id, formatter(env['data']), keyboard)


def cmd_status(chat_id, arg):
    state, launch = describe()
    if state != 'running':
        tg_send(chat_id, f'⚪ EC2 {state}\n   /start 로 켜세요.')
        return
    if not ssm_ready():
        tg_send(chat_id, '🔄 부팅 중 — SSM 에이전트 대기 중입니다.')
        return
    env = parse_pocket(ssm_run(pocket_script('status'), timeout=20))
    if not env.get('ok'):
        tg_send(chat_id, f"⚠️ {env.get('error')}")
        return
    uptime = format_uptime(launch, datetime.now(timezone.utc))
    tg_send(chat_id, format_status(env['data'], uptime))


def cmd_projects(chat_id, arg):
    env = run_pocket(chat_id, 'list')
    if env is None:
        return
    if not env.get('ok'):
        tg_send(chat_id, f"⚠️ {env.get('error')}")
        return
    rows = env['data']['projects']
    tg_send(chat_id, format_projects(rows), keyboard_for(rows))


def cmd_up(chat_id, arg):
    err = validate_name(arg)
    if err:
        tg_send(chat_id, f'⚠️ {err}')
        return
    env = run_pocket(chat_id, 'up', arg)
    reply_envelope(chat_id, env, lambda d: f"🔄 {d['name']} 기동 ({d['unit']}). 앱 Code 탭에서 확인하세요.")


def cmd_down(chat_id, arg):
    err = validate_name(arg)
    if err:
        tg_send(chat_id, f'⚠️ {err}')
        return
    env = run_pocket(chat_id, 'down', arg)
    reply_envelope(chat_id, env, lambda d: f"⏹ {d['name']} 정지 ({d['unit']}). 부팅 자동 기동도 해제했어요.")


def cmd_trees(chat_id, arg):
    err = validate_name(arg)
    if err:
        tg_send(chat_id, f'⚠️ {err}')
        return
    env = run_pocket(chat_id, 'trees', arg)
    reply_envelope(chat_id, env, format_trees)


CALLBACK_VERBS = {'up': cmd_up, 'down': cmd_down, 'trees': cmd_trees}

# Seconds of the API Gateway 30s window we allow the whole callback to use.
# The command's own SSM round trip already spends up to ~20s; a second SSM
# call to refresh the keyboard must not be allowed to push the total past
# the gateway's limit, or Telegram redelivers the button press.
CALLBACK_BUDGET = 25


def handle_callback(cb):
    """Handle an inline-button press.

    Answers the callback first so Telegram clears the button spinner before
    the SSM round trip runs, then re-checks chat/from ID the same way the
    message path checks ALLOWED_CHAT_ID. Any unknown verb, malformed
    payload, or name that fails validate_name is ignored silently.
    """
    started = time.monotonic()
    tg_answer_callback(cb.get('id'))
    chat_id = str(((cb.get('message') or {}).get('chat') or {}).get('id', ''))
    from_id = str((cb.get('from') or {}).get('id', ''))
    allowed = os.environ['ALLOWED_CHAT_ID']
    if chat_id != allowed or from_id != allowed:
        return
    verb, _, name = (cb.get('data') or '').partition(':')
    handler = CALLBACK_VERBS.get(verb)
    if handler is None or validate_name(name):
        return
    handler(chat_id, name)
    message_id = (cb.get('message') or {}).get('message_id')
    if message_id and verb in ('up', 'down'):
        # Refresh the list markup only if the command left us time. A second
        # SSM round trip can otherwise push the invocation past the gateway's
        # 30s limit, and Telegram then redelivers the press.
        left = CALLBACK_BUDGET - (time.monotonic() - started)
        if left >= 5:
            env = parse_pocket(ssm_run(pocket_script('list'), timeout=int(min(left, 8))))
            if env.get('ok'):
                tg_edit_markup(chat_id, message_id, keyboard_for(env['data']['projects']))


HELP = (
    'pocket-claude v6\n\n'
    '/start          EC2 켜기\n'
    '/stop           EC2 끄기\n'
    '/status         EC2·프로젝트·인증 상태\n'
    '/projects       프로젝트 목록\n'
    '/up <name>      프로젝트 서버 켜기\n'
    '/down <name>    프로젝트 서버 끄기\n'
    '/trees <name>   세션 워크트리 목록'
)

HANDLERS = {
    '/start': cmd_start,
    '/stop': cmd_stop,
    '/status': cmd_status,
    '/projects': cmd_projects,
    '/up': cmd_up,
    '/down': cmd_down,
    '/trees': cmd_trees,
}


def lambda_handler(event, context):
    if not INSTANCE_ID:
        return {'statusCode': 500, 'body': 'INSTANCE_ID is not configured'}

    # Parse defensively: a malformed body must not raise, or Telegram retries
    # the same update forever against a handler that will never accept it.
    try:
        body = json.loads(event.get('body') or '{}')
        callback = body.get('callback_query')
        if callback:
            # A failing button press must be reported the same way a failing
            # command is: the callback was already answered inside
            # handle_callback (spinner cleared), so a swallowed exception
            # here would leave the user with total silence — indistinguishable
            # from a dead bot.
            try:
                handle_callback(callback)
            except Exception as exc:  # noqa: BLE001 - deliberate catch-all
                cb_chat_id = str(((callback.get('message') or {}).get('chat') or {}).get('id', ''))
                try:
                    tg_send(cb_chat_id, format_error('버튼', exc))
                except Exception:  # noqa: BLE001 - Telegram itself is down
                    pass
                return {'statusCode': 200, 'body': 'error reported'}
            return {'statusCode': 200, 'body': 'ok'}
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
