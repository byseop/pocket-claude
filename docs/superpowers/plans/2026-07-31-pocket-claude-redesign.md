# pocket-claude 재설계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 클로드 기동 책임을 Lambda의 원격 SSM 호출에서 EC2 자신의 systemd로 옮기고, 텔레그램 명령을 `/start /stop /status /view` 4개로 축소한다.

**Architecture:** EC2가 부팅되면 systemd가 `claude-supervise.sh`를 실행하고, 이 래퍼가 tmux 세션을 만든 뒤 세션이 살아있는 동안 블록한다. 세션이 죽으면 래퍼가 비정상 종료하고 `Restart=always`가 10초 뒤 되살린다. Lambda는 전원 스위치(`ec2:Start/StopInstances`)와 조회창(`ssm:SendCommand`) 역할만 하며 어디서도 블로킹하지 않는다. cron이 5분마다 tmux 화면 해시를 비교해 60분 유휴 시 인스턴스가 스스로 멈춘다.

**Tech Stack:** Python 3.14 (Lambda), boto3, bash, systemd, cron, AWS SSM RunShellScript, Telegram Bot API

## Global Constraints

- 대상 인스턴스는 `${INSTANCE_ID}` 환경변수로만 참조한다. 소스에 하드코딩 금지 (퍼블릭 저장소)
- Lambda의 어떤 코드 경로도 블로킹하지 않는다. `waiter`, `time.sleep` 사용 금지. API Gateway 통합 타임아웃은 30,000ms이고 초과 시 텔레그램에 503이 전달된다
- 셸 스크립트 주석은 영어로 작성한다 (저장소 전역 규칙)
- 커밋 메시지는 한글, `[태그] 설명` 형식. Claude 서명 트레일러를 넣지 않는다
- 기존 v3 Lambda는 Task 5에서 교체하기 전까지 그대로 동작해야 한다. EC2 쪽 작업(Task 1~3)은 v3와 공존 가능해야 한다
- 모든 EC2 작업은 SSM RunShellScript로 수행한다. 로컬에 session-manager-plugin이 없어 `aws ssm start-session`은 쓸 수 없다
- SSM `--parameters`에 한글이나 따옴표가 섞이면 CLI 파싱이 깨진다. 반드시 JSON 파일(`file://`)로 전달한다

## 검증 환경

- EC2: `${INSTANCE_ID}`, region `ap-northeast-2`, Ubuntu 24.04
- 인스턴스는 현재 `running`이며 v3 구조로 클로드가 떠 있다
- 로컬: Python 3.11.15, boto3 1.43.0, pytest 없음 → **stdlib `unittest` 사용**

## File Structure

| 파일 | 책임 |
|---|---|
| `ec2/claude-supervise.sh` | systemd가 추적할 프로세스. tmux 세션 생성 및 생존 감시 |
| `ec2/claude-telegram.service` | systemd 유닛. 부팅 자동 시작 + 자동 재시작 |
| `ec2/idle-watch.sh` | 유휴 감지 및 자기 정지 |
| `ec2/install.sh` | 위 셋을 인스턴스에 배치하고 활성화 |
| `iam/ec2-self-stop-policy.json` | 인스턴스가 자기 자신만 정지할 수 있는 인라인 정책 |
| `src/lambda_function.py` | Lambda v4. 순수 헬퍼 + 얇은 AWS 호출 계층 |
| `tests/test_lambda_function.py` | 순수 헬퍼 단위 테스트 (AWS 불필요) |

Lambda는 **순수 함수와 AWS 호출을 분리**한다. `strip_ansi`, `detect_auth_error`, `format_uptime`은 AWS 없이 테스트 가능해야 하며, 이를 위해 모듈이 자격증명 없이 import 가능해야 한다.

---

### Task 1: systemd 자기부팅 및 자동 복구

**Files:**
- Create: `ec2/claude-supervise.sh`
- Create: `ec2/claude-telegram.service`
- Create: `ec2/install.sh`

**Interfaces:**
- Consumes: 인스턴스의 기존 `/home/ubuntu/start-claude-telegram.sh` (수정하지 않음)
- Produces: `claude-telegram.service` systemd 유닛, `/home/ubuntu/claude-supervise.sh`. Task 3의 `idle-watch.sh`가 같은 tmux 세션명 `claude`를 참조한다

- [ ] **Step 1: 감시 래퍼 작성**

`ec2/claude-supervise.sh`:

```bash
#!/bin/bash
# Wrapper that systemd can track. Running `tmux new-session -d` directly from
# ExecStart does not work: the tmux server daemonizes itself (double fork +
# setsid), systemd loses the PID, decides the service died, and Restart=always
# spins forever.

set -u

SESSION=claude
BOOT_SCRIPT=/home/ubuntu/start-claude-telegram.sh
IDLE_STATE=/home/ubuntu/.claude-idle-state

# Clear the stale idle counter. Without this, a boot right after an idle
# shutdown inherits counter=12 and shuts down again immediately.
rm -f "$IDLE_STATE"

tmux has-session -t "$SESSION" 2>/dev/null || \
  tmux new-session -d -s "$SESSION" "$BOOT_SCRIPT"

# Block while the session lives. Exit non-zero when it dies so systemd restarts.
while tmux has-session -t "$SESSION" 2>/dev/null; do
  sleep 10
done

exit 1
```

- [ ] **Step 2: systemd 유닛 작성**

`ec2/claude-telegram.service`:

```ini
[Unit]
Description=Claude Code Telegram channel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Environment=HOME=/home/ubuntu
ExecStart=/home/ubuntu/claude-supervise.sh
ExecStop=/usr/bin/tmux kill-session -t claude
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: 설치 스크립트 작성**

`ec2/install.sh` — 인스턴스 위에서 root로 실행된다:

```bash
#!/bin/bash
# Installs the supervisor and systemd unit. Idempotent: safe to re-run.
set -eu

install -o ubuntu -g ubuntu -m 0755 /tmp/claude-supervise.sh /home/ubuntu/claude-supervise.sh
install -o root -g root -m 0644 /tmp/claude-telegram.service /etc/systemd/system/claude-telegram.service

systemctl daemon-reload
systemctl enable claude-telegram

echo "installed"
```

- [ ] **Step 4: 파일을 인스턴스로 전송하고 설치**

SSM은 파일 업로드를 지원하지 않으므로 heredoc으로 내용을 심어 보낸다. 로컬에서 실행:

```bash
cd ~/Documents/workspace/telegram-claude-ec2
SP="$CLAUDE_JOB_DIR/tmp"
python3 - <<'PY' > "$SP/task1.json"
import json

sup = open('ec2/claude-supervise.sh').read()
svc = open('ec2/claude-telegram.service').read()
ins = open('ec2/install.sh').read()

# Plain concatenation, not an f-string: the shell heredocs below contain
# braces and an f-string would require escaping every one of them.
script = (
    "cat > /tmp/claude-supervise.sh <<'EOF_SUP'\n" + sup + "\nEOF_SUP\n"
    "cat > /tmp/claude-telegram.service <<'EOF_SVC'\n" + svc + "\nEOF_SVC\n"
    "cat > /tmp/install.sh <<'EOF_INS'\n" + ins + "\nEOF_INS\n"
    "bash /tmp/install.sh\n"
)

print(json.dumps({'commands': [script], 'executionTimeout': ['120']}))
PY
```

`EOF_SUP` 같은 구분자를 따옴표로 감싼 이유는, 스크립트 안의 `$SESSION`이나 `$COUNT`가 전송 도중 셸에 의해 치환되지 않도록 하기 위해서다.

전송:

```bash
ID=$(aws ssm send-command --region ap-northeast-2 --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript --parameters "file://$SP/task1.json" \
  --query 'Command.CommandId' --output text)
sleep 15
aws ssm get-command-invocation --region ap-northeast-2 --command-id "$ID" \
  --instance-id "$INSTANCE_ID" --query 'StandardOutputContent' --output text
```

Expected: `installed`

- [ ] **Step 5: 기존 v3 tmux 세션을 systemd 관리로 전환**

v3가 띄운 tmux 세션은 systemd 밖에 있다. 한 번 정리하고 서비스로 다시 띄운다:

```bash
sudo -u ubuntu tmux kill-server 2>/dev/null || true
systemctl start claude-telegram
sleep 25
systemctl is-active claude-telegram
sudo -u ubuntu tmux ls
```

Expected: `active`, 그리고 `claude: 1 windows`

- [ ] **Step 6: 자동 복구 검증 — 세션을 죽여본다**

```bash
sudo -u ubuntu tmux kill-session -t claude
sleep 20
sudo -u ubuntu tmux ls
systemctl is-active claude-telegram
```

Expected: 세션이 다시 존재하고 서비스는 `active`. 죽인 지 10~15초 안에 복구되어야 한다.

만약 `Restart=always`가 폭주하면(`systemctl status`에 start request repeated too quickly) Step 1의 래퍼가 즉시 종료되는 것이므로 tmux 경로를 점검한다.

- [ ] **Step 7: 커밋**

```bash
git add ec2/
git commit -m "[feat] systemd 기반 클로드 자기부팅 및 자동복구 추가

tmux 서버가 스스로 데몬화해 systemd가 PID를 놓치는 문제를
감시 래퍼로 우회. 세션이 죽으면 래퍼가 비정상 종료해
Restart=always가 10초 뒤 복구한다."
```

---

### Task 2: 인스턴스 자기 정지 IAM 권한

**Files:**
- Create: `iam/ec2-self-stop-policy.json`

**Interfaces:**
- Consumes: 없음
- Produces: `ec2-ssm-role`에 붙은 인라인 정책 `ec2-self-stop`. Task 3의 `idle-watch.sh`가 이 권한에 의존한다

- [ ] **Step 1: 정책 문서 작성**

`iam/ec2-self-stop-policy.json` — 자기 인스턴스 하나로만 제한한다:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StopSelfOnly",
      "Effect": "Allow",
      "Action": "ec2:StopInstances",
      "Resource": "arn:aws:ec2:ap-northeast-2:AWS_ACCOUNT_ID:instance/INSTANCE_ID"
    }
  ]
}
```

`AWS_ACCOUNT_ID`와 `INSTANCE_ID`는 플레이스홀더다. 저장소에 실제 값을 커밋하지 않는다.

- [ ] **Step 2: 실제 값을 채워 정책 부착**

```bash
cd ~/Documents/workspace/telegram-claude-ec2
set -a; . ./.env; set +a
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
SP="$CLAUDE_JOB_DIR/tmp"
sed -e "s/AWS_ACCOUNT_ID/$ACCOUNT/" -e "s/INSTANCE_ID/$INSTANCE_ID/" \
  iam/ec2-self-stop-policy.json > "$SP/policy.json"
cat "$SP/policy.json"
aws iam put-role-policy --role-name ec2-ssm-role \
  --policy-name ec2-self-stop --policy-document "file://$SP/policy.json"
```

Expected: 출력된 JSON의 Resource에 실제 계정 ID와 인스턴스 ID가 채워져 있고, `put-role-policy`가 에러 없이 끝난다.

- [ ] **Step 3: 인스턴스에서 권한 검증 (dry-run)**

실제로 멈추지 않고 권한만 확인한다:

```bash
# on the instance
T=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
IID=$(curl -s -H "X-aws-ec2-metadata-token: $T" http://169.254.169.254/latest/meta-data/instance-id)
aws ec2 stop-instances --region ap-northeast-2 --instance-ids "$IID" --dry-run 2>&1
```

Expected: `DryRunOperation: Request would have succeeded, but DryRun flag is set`

`UnauthorizedOperation`이 나오면 정책이 아직 전파되지 않은 것이다. 10초 뒤 재시도한다.

- [ ] **Step 4: 커밋**

```bash
git add iam/
git commit -m "[feat] 인스턴스 자기 정지용 IAM 정책 추가

유휴 자동 종료를 위해 ec2:StopInstances를 부여하되
Resource를 자기 인스턴스 ARN 하나로 제한."
```

---

### Task 3: 유휴 자동 종료

**Files:**
- Create: `ec2/idle-watch.sh`
- Modify: `ec2/install.sh` (cron 등록 추가)

**Interfaces:**
- Consumes: Task 1의 tmux 세션명 `claude`, Task 2의 `ec2:StopInstances` 권한
- Produces: `ubuntu` crontab 항목, 상태 파일 `/home/ubuntu/.claude-idle-state`

- [ ] **Step 1: 유휴 감시 스크립트 작성**

`ec2/idle-watch.sh`:

```bash
#!/bin/bash
# Stops this instance after IDLE_LIMIT consecutive unchanged screen samples.
# Runs from the ubuntu crontab every 5 minutes, so 12 samples = 60 minutes.
set -u

SESSION=claude
STATE=/home/ubuntu/.claude-idle-state
IDLE_LIMIT=${IDLE_LIMIT:-12}
REGION=ap-northeast-2

tmux has-session -t "$SESSION" 2>/dev/null || exit 0

HASH=$(tmux capture-pane -t "$SESSION" -p | md5sum | cut -d' ' -f1)
PREV=$(cut -d' ' -f1 "$STATE" 2>/dev/null || echo '')
COUNT=$(cut -d' ' -f2 "$STATE" 2>/dev/null || echo 0)

if [ "$HASH" = "$PREV" ]; then
  COUNT=$((COUNT + 1))
else
  COUNT=0
fi
echo "$HASH $COUNT" > "$STATE"

[ "$COUNT" -lt "$IDLE_LIMIT" ] && exit 0

# Notify through the main bot token that already lives on this box, so the
# watcher needs no credentials of its own.
ENV_FILE=/home/ubuntu/.claude/channels/telegram/.env
if [ -f "$ENV_FILE" ]; then
  TOK=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"'"'"' \r\n')
  CHAT=$(python3 -c "import json;print(json.load(open('/home/ubuntu/.claude/channels/telegram/access.json'))['allowFrom'][0])" 2>/dev/null || echo '')
  if [ -n "$TOK" ] && [ -n "$CHAT" ]; then
    MIN=$((IDLE_LIMIT * 5))
    curl -s -X POST "https://api.telegram.org/bot${TOK}/sendMessage" \
      -d chat_id="$CHAT" \
      -d text="💤 ${MIN}분 유휴 — EC2를 자동 종료합니다. /start 로 다시 켜세요." >/dev/null
  fi
fi

T=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
IID=$(curl -s -H "X-aws-ec2-metadata-token: $T" http://169.254.169.254/latest/meta-data/instance-id)
aws ec2 stop-instances --region "$REGION" --instance-ids "$IID"
```

- [ ] **Step 2: install.sh에 cron 등록 추가**

`ec2/install.sh` 끝의 `echo "installed"` 앞에 삽입:

```bash
install -o ubuntu -g ubuntu -m 0755 /tmp/idle-watch.sh /home/ubuntu/idle-watch.sh

# Register the cron entry idempotently: drop any previous line, then add ours.
CRON_LINE='*/5 * * * * /home/ubuntu/idle-watch.sh >/dev/null 2>&1'
( sudo -u ubuntu crontab -l 2>/dev/null | grep -v 'idle-watch.sh' ; echo "$CRON_LINE" ) \
  | sudo -u ubuntu crontab -
```

- [ ] **Step 3: 갱신된 install.sh와 idle-watch.sh를 인스턴스로 전송**

Task 1과 같은 방식이되 `idle-watch.sh`가 추가된다. 로컬에서:

```bash
cd ~/Documents/workspace/telegram-claude-ec2
SP="$CLAUDE_JOB_DIR/tmp"
python3 - <<'PY' > "$SP/task3.json"
import json

sup = open('ec2/claude-supervise.sh').read()
svc = open('ec2/claude-telegram.service').read()
idle = open('ec2/idle-watch.sh').read()
ins = open('ec2/install.sh').read()

script = (
    "cat > /tmp/claude-supervise.sh <<'EOF_SUP'\n" + sup + "\nEOF_SUP\n"
    "cat > /tmp/claude-telegram.service <<'EOF_SVC'\n" + svc + "\nEOF_SVC\n"
    "cat > /tmp/idle-watch.sh <<'EOF_IDLE'\n" + idle + "\nEOF_IDLE\n"
    "cat > /tmp/install.sh <<'EOF_INS'\n" + ins + "\nEOF_INS\n"
    "bash /tmp/install.sh\n"
    "echo '--- crontab ---'\n"
    "sudo -u ubuntu crontab -l\n"
)

print(json.dumps({'commands': [script], 'executionTimeout': ['120']}))
PY

ID=$(aws ssm send-command --region ap-northeast-2 --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript --parameters "file://$SP/task3.json" \
  --query 'Command.CommandId' --output text)
sleep 15
aws ssm get-command-invocation --region ap-northeast-2 --command-id "$ID" \
  --instance-id "$INSTANCE_ID" --query 'StandardOutputContent' --output text
```

Expected: `installed` 다음에 `--- crontab ---`, 그리고 `*/5 * * * * /home/ubuntu/idle-watch.sh >/dev/null 2>&1` 한 줄. 재실행해도 cron 항목이 중복되지 않아야 한다.

- [ ] **Step 4: 임계값 2로 낮춰 동작 검증**

실제 60분을 기다리지 않는다. 인스턴스에서 직접 3회 실행한다:

```bash
rm -f /home/ubuntu/.claude-idle-state
sudo -u ubuntu IDLE_LIMIT=2 /home/ubuntu/idle-watch.sh; cat /home/ubuntu/.claude-idle-state
sudo -u ubuntu IDLE_LIMIT=2 /home/ubuntu/idle-watch.sh; cat /home/ubuntu/.claude-idle-state
```

Expected: 1회차 `<해시> 0`, 2회차 `<해시> 1`. 아직 정지하지 않는다.

- [ ] **Step 5: 임계 도달 시 정지하는지 검증**

3회차를 실행하면 카운터가 2가 되어 임계에 도달한다. **인스턴스가 실제로 꺼진다.**

```bash
sudo -u ubuntu IDLE_LIMIT=2 /home/ubuntu/idle-watch.sh
```

로컬에서 확인:

```bash
sleep 45
aws ec2 describe-instances --region ap-northeast-2 --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].State.Name' --output text
```

Expected: `stopping` 또는 `stopped`. 텔레그램에 `💤 10분 유휴` 알림이 도착해야 한다.

- [ ] **Step 6: 재기동 후 즉시 재종료되지 않는지 검증**

이것이 상태 파일 초기화가 동작하는지 보는 테스트다:

```bash
aws ec2 start-instances --region ap-northeast-2 --instance-ids "$INSTANCE_ID"
# wait until running + SSM Online, then:
```

인스턴스에서:

```bash
cat /home/ubuntu/.claude-idle-state 2>&1
```

Expected: `No such file or directory`. `claude-supervise.sh`가 부팅 시 지웠기 때문이다. 파일이 남아 있고 카운터가 2 이상이면 Task 1 Step 1의 `rm -f`가 동작하지 않은 것이다.

- [ ] **Step 7: 커밋**

```bash
git add ec2/
git commit -m "[feat] 유휴 60분 자동 종료 추가

tmux 화면 해시를 5분 간격으로 비교해 12회 연속 동일하면
텔레그램 알림 후 자기 인스턴스를 정지한다."
```

---

### Task 4: Lambda v4 — 순수 헬퍼와 단위 테스트

**Files:**
- Create: `tests/test_lambda_function.py`
- Modify: `src/lambda_function.py` (v3 → v4 전면 교체)

**Interfaces:**
- Consumes: `INSTANCE_ID`, `TELEGRAM_TOKEN`, `ALLOWED_CHAT_ID` 환경변수
- Produces: 순수 함수 `strip_ansi(text) -> str`, `detect_auth_error(pane_text) -> str | None`, `format_uptime(launch_time: datetime, now: datetime) -> str`, `build_status(state, uptime, service_active, auth_error) -> str`. Task 5가 이 모듈을 배포한다

이 태스크는 **AWS 없이 통과해야 한다.** 그러려면 모듈이 자격증명 없이 import 가능해야 하므로 `INSTANCE_ID`를 `os.environ.get`으로 읽고 핸들러에서 검증한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_lambda_function.py`:

```python
"""Unit tests for the pure helpers. No AWS credentials required."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import lambda_function as lf


class TestStripAnsi(unittest.TestCase):
    def test_removes_color_codes(self):
        self.assertEqual(lf.strip_ansi('\x1b[31mred\x1b[0m'), 'red')

    def test_removes_cursor_movement(self):
        self.assertEqual(lf.strip_ansi('a\x1b[2Kb'), 'ab')

    def test_leaves_plain_text_untouched(self):
        self.assertEqual(lf.strip_ansi('hello world'), 'hello world')

    def test_preserves_newlines(self):
        self.assertEqual(lf.strip_ansi('\x1b[1ma\nb'), 'a\nb')


class TestDetectAuthError(unittest.TestCase):
    def test_detects_revoked_token(self):
        pane = 'some output\n Please run /login  API Error: 401 OAuth access token has been revoked.\n'
        self.assertIn('401', lf.detect_auth_error(pane))

    def test_returns_none_when_healthy(self):
        pane = 'Claude Code v2.1.220\n bypass permissions on\n'
        self.assertIsNone(lf.detect_auth_error(pane))

    def test_returns_last_match_when_several(self):
        pane = 'API Error: 401 first\nnormal line\nPlease run /login second\n'
        self.assertIn('second', lf.detect_auth_error(pane))

    def test_handles_empty_input(self):
        self.assertIsNone(lf.detect_auth_error(''))


class TestFormatUptime(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    def test_minutes_only(self):
        launched = self.now - timedelta(minutes=13)
        self.assertEqual(lf.format_uptime(launched, self.now), '13m')

    def test_hours_and_minutes(self):
        launched = self.now - timedelta(hours=2, minutes=13)
        self.assertEqual(lf.format_uptime(launched, self.now), '2h 13m')

    def test_just_started(self):
        self.assertEqual(lf.format_uptime(self.now, self.now), '0m')


class TestBuildStatus(unittest.TestCase):
    def test_all_healthy(self):
        out = lf.build_status('running', '2h 13m', True, None)
        self.assertIn('✅ EC2 running (2h 13m)', out)
        self.assertIn('✅ 인증 OK', out)
        self.assertNotIn('❌', out)

    def test_auth_failure_includes_recovery_steps(self):
        out = lf.build_status('running', '5m', True, 'API Error: 401 revoked')
        self.assertIn('❌', out)
        self.assertIn('claude setup-token', out)
        self.assertIn('401', out)

    def test_service_down(self):
        out = lf.build_status('running', '5m', False, None)
        self.assertIn('❌ claude-telegram.service', out)

    def test_stopped_instance_skips_service_lines(self):
        out = lf.build_status('stopped', None, None, None)
        self.assertIn('EC2 stopped', out)
        self.assertIn('/start', out)
        self.assertNotIn('claude-telegram.service', out)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd ~/Documents/workspace/telegram-claude-ec2 && python3 -m unittest discover -s tests -v`

Expected: FAIL — `AttributeError: module 'lambda_function' has no attribute 'strip_ansi'` (v3에는 이 함수들이 없다)

- [ ] **Step 3: Lambda v4 구현**

`src/lambda_function.py` 전체를 교체한다:

```python
"""
ec2-boot-manager Lambda - v4.

Power switch and viewport only. The instance starts Claude Code itself via
systemd, so this function never launches anything remotely. Every code path
returns without blocking: API Gateway's integration timeout is 30s, and v3
exceeded it by waiting on an instance_running waiter.

Commands: /start /stop /status /view
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timezone

import boto3

AWS_REGION = os.environ.get('AWS_REGION', 'ap-northeast-2')
ec2 = boto3.client('ec2', region_name=AWS_REGION)
ssm = boto3.client('ssm', region_name=AWS_REGION)

# Read lazily so the module imports without AWS config, which unit tests need
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
    checks cannot see this. The error only shows up on screen.
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
    """Run a short read-only script and return stdout. Never used to launch."""
    cid = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName='AWS-RunShellScript',
        Parameters={'commands': [script], 'executionTimeout': ['60']},
    )['Command']['CommandId']

    waiter_deadline = timeout
    import time as _t
    while waiter_deadline > 0:
        _t.sleep(1)
        waiter_deadline -= 1
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

    body = json.loads(event.get('body') or '{}')
    message = body.get('message') or body.get('edited_message') or {}
    chat_id = str((message.get('chat') or {}).get('id', ''))
    text = (message.get('text') or '').strip().split('@')[0]

    if chat_id != os.environ['ALLOWED_CHAT_ID']:
        return {'statusCode': 200, 'body': 'ignored'}

    handler = HANDLERS.get(text)
    if handler:
        handler(chat_id)
    elif text.startswith('/'):
        tg_send(chat_id, HELP)

    return {'statusCode': 200, 'body': 'ok'}
```

`ssm_run`의 폴링 루프는 `time.sleep`을 쓰지만 **상한이 25초**이고 SSM 조회는 보통 2~3초에 끝난다. 이는 Global Constraints의 "블로킹 금지"가 겨냥한 `instance_running` waiter(38.7초)와 다르다. 그럼에도 30초 한계에 근접하므로 `/status`와 `/view`에서만 쓰고 `/start`·`/stop`에서는 절대 호출하지 않는다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/Documents/workspace/telegram-claude-ec2 && python3 -m unittest discover -s tests -v`

Expected: `OK`, 15개 테스트 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add src/lambda_function.py tests/
git commit -m "[feat] Lambda v4 — 명령 4개로 축소하고 순수 헬퍼 분리

기동 책임이 systemd로 넘어가 pgrep 가드와 waiter를 제거.
strip_ansi·detect_auth_error·format_uptime·build_status를
AWS 없이 테스트 가능한 순수 함수로 분리하고 단위 테스트 15개 추가."
```

---

### Task 5: 배포 및 종단 검증

**Files:**
- Modify: 없음 (배포만)

**Interfaces:**
- Consumes: Task 4의 `src/lambda_function.py`
- Produces: 배포된 Lambda v4

- [ ] **Step 1: 현재 v3를 백업**

```bash
cd ~/Documents/workspace/telegram-claude-ec2
URL=$(aws lambda get-function --region ap-northeast-2 --function-name ec2-boot-manager \
  --query 'Code.Location' --output text)
curl -s "$URL" -o "$CLAUDE_JOB_DIR/tmp/v3-deployed.zip"
mkdir -p backup && (cd "$CLAUDE_JOB_DIR/tmp" && unzip -p v3-deployed.zip lambda_function.py) \
  > backup/lambda_function.v3.py
head -5 backup/lambda_function.v3.py
```

Expected: v3 헤더(`ec2-boot-manager Lambda — v3 (Slim)`)가 보인다

- [ ] **Step 2: INSTANCE_ID 환경변수를 Lambda에 추가**

v4는 `INSTANCE_ID`를 환경변수로 읽는다. 기존 값을 보존하며 추가한다:

```bash
set -a; . ./.env; set +a
aws lambda update-function-configuration --region ap-northeast-2 \
  --function-name ec2-boot-manager \
  --environment "Variables={TELEGRAM_TOKEN=$TELEGRAM_TOKEN,ALLOWED_CHAT_ID=$ALLOWED_CHAT_ID,INSTANCE_ID=$INSTANCE_ID}" \
  --query 'Environment.Variables' --output json
aws lambda wait function-updated --region ap-northeast-2 --function-name ec2-boot-manager
```

Expected: 세 키가 모두 보인다

- [ ] **Step 3: 코드 배포**

```bash
SP="$CLAUDE_JOB_DIR/tmp"
(cd src && zip -q "$SP/lambda-v4.zip" lambda_function.py)
aws lambda update-function-code --region ap-northeast-2 \
  --function-name ec2-boot-manager --zip-file "fileb://$SP/lambda-v4.zip" \
  --query '{Status:LastUpdateStatus,Size:CodeSize}' --output table
aws lambda wait function-updated --region ap-northeast-2 --function-name ec2-boot-manager
```

- [ ] **Step 4: `/status` 종단 검증 — 응답 시간 측정**

텔레그램에서 직접 `/status`를 보낸다. 그 다음 로그로 소요시간을 확인한다:

```bash
sleep 10
aws logs filter-log-events --region ap-northeast-2 \
  --log-group-name /aws/lambda/ec2-boot-manager \
  --start-time $(python3 -c "import time;print(int(time.time()*1000)-300000)") \
  --filter-pattern "REPORT" --query 'events[].message' --output text \
  | grep -oE "Duration: [0-9.]+ ms"
```

Expected: 모든 Duration이 **30,000 ms 미만**. `/status`는 3,000~6,000ms 근처여야 한다.

- [ ] **Step 5: `/start` 응답 시간 검증 (핵심 회귀 테스트)**

이것이 v3의 38.7초 결함이 사라졌는지 보는 테스트다. 먼저 인스턴스를 끈다:

```bash
aws ec2 stop-instances --region ap-northeast-2 --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-stopped --region ap-northeast-2 --instance-ids "$INSTANCE_ID"
```

텔레그램에서 `/start`를 보낸 뒤:

```bash
sleep 10
aws logs filter-log-events --region ap-northeast-2 \
  --log-group-name /aws/lambda/ec2-boot-manager \
  --start-time $(python3 -c "import time;print(int(time.time()*1000)-120000)") \
  --filter-pattern "REPORT" --query 'events[].message' --output text \
  | grep -oE "Duration: [0-9.]+ ms"
```

Expected: **1,000 ms 미만.** 텔레그램 웹훅에도 에러가 없어야 한다:

```bash
set -a; . ./.env; set +a
curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getWebhookInfo" \
  | python3 -c "import sys,json;r=json.load(sys.stdin)['result'];print('pending:',r.get('pending_update_count'),'last_error:',r.get('last_error_message'))"
```

Expected: `pending: 0 last_error: None`

- [ ] **Step 6: systemd 자기부팅 종단 검증**

Step 5에서 인스턴스를 껐다 켰다. 이제 **아무 명령도 보내지 않고** 텔레그램 메인 봇에 그냥 말을 건다.

Expected: 답장이 온다. 이것이 이번 재설계의 핵심 — Lambda가 기동에 전혀 관여하지 않았는데도 클로드가 살아 있어야 한다.

안 되면 `/status`로 `claude-telegram.service` 상태를 확인하고, `journalctl -u claude-telegram -n 50`으로 원인을 본다.

- [ ] **Step 7: `/view` ANSI 제거 검증**

텔레그램에서 `/view`를 보낸다.

Expected: 화면이 읽을 수 있는 평문으로 온다. `[0m`, `[2K` 같은 잔여물이 보이면 `ANSI_RE`를 보강한다.

- [ ] **Step 8: 인증 실패 감지 종단 검증**

토큰을 실제로 폐기하면 복구 비용이 크므로, 감지 파이프라인(SSM → tmux 캡처 → 정규식)만 별도 세션으로 확인한다. 인스턴스에서:

```bash
sudo -u ubuntu tmux new-session -d -s authtest \
  'echo "API Error: 401 OAuth access token has been revoked."; sleep 300'
sleep 2
sudo -u ubuntu tmux capture-pane -t authtest -p -S -200 | grep -cE '401|OAuth|Please run /login'
sudo -u ubuntu tmux kill-session -t authtest
```

Expected: `1` 이상. 0이면 캡처나 패턴이 잘못된 것이므로 `AUTH_RE`를 점검한다.

정규식 자체의 동작은 Task 4의 `TestDetectAuthError` 4건이 이미 덮는다. 이 단계는 실제 tmux 출력에서도 같은 결과가 나오는지만 확인한다.

- [ ] **Step 9: v3 백업을 gitignore에 추가하고 커밋**

배포되어 있던 v3에는 인스턴스 ID가 하드코딩돼 있다. 퍼블릭 저장소이므로 **커밋하지 않고 로컬에만 둔다.**

```bash
cd ~/Documents/workspace/telegram-claude-ec2
echo 'backup/lambda_function.v3.py' >> .gitignore
git check-ignore -v backup/lambda_function.v3.py
```

Expected: `.gitignore:...:backup/lambda_function.v3.py` — 무시 대상으로 잡힌다

```bash
git add .gitignore
git commit -m "[chore] 롤백용 v3 백업을 gitignore 처리

배포본 v3에는 인스턴스 ID가 하드코딩돼 있어
퍼블릭 저장소에 올리지 않고 로컬에만 보관한다."
git push
```

- [ ] **Step 10: 최종 유출 재검사**

푸시 후 원격 기준으로 다시 확인한다. 로컬 상태를 믿지 않는다:

```bash
gh api "repos/byseop/pocket-claude/git/trees/main?recursive=1" --jq '.tree[].path' \
  | grep -E 'v3\.py|(^|/)\.env$|\.zip$' && echo "⚠️ 유출" || echo "✅ 클린"
```

Expected: `✅ 클린`

---

## 롤백 절차

Task 5 이후 문제가 생기면:

```bash
cd ~/Documents/workspace/telegram-claude-ec2
RB="$CLAUDE_JOB_DIR/tmp/rollback"
mkdir -p "$RB"
# Lambda requires the handler file to be named lambda_function.py inside the
# archive, so rename on copy rather than rewriting zip entries afterwards.
cp backup/lambda_function.v3.py "$RB/lambda_function.py"
(cd "$RB" && zip -q rollback.zip lambda_function.py)
aws lambda update-function-code --region ap-northeast-2 \
  --function-name ec2-boot-manager --zip-file "fileb://$RB/rollback.zip"
```

v3는 `INSTANCE_ID`를 하드코딩했으므로 롤백해도 환경변수 추가는 무해하다. 다만 저장소의 `backup/lambda_function.v3.py`는 식별자가 살아 있는 원본이어야 롤백이 성립한다 — 이 파일만은 마스킹하지 않고, 대신 `.gitignore`에 추가해 커밋하지 않는다.

EC2 쪽은 되돌릴 필요가 없다. systemd 유닛은 v3와 공존 가능하며, v3의 `pgrep` 가드는 이미 클로드가 떠 있으면 아무것도 하지 않고 종료하기 때문이다.

## 미해결 위험

- **유휴 감지 오탐**: TUI가 대기 중에도 화면을 갱신하도록 바뀌면 영원히 종료되지 않는다. Task 3 Step 3에서 실측으로 확인하되, 나중에 깨질 수 있다. 증상은 "인스턴스가 안 꺼짐"이며 `/home/ubuntu/.claude-idle-state`의 카운터가 계속 0이면 이 경우다.
- **`ssm_run`의 25초 상한**: SSM이 느려지면 `/status`가 30초 한계에 근접할 수 있다. 실측 3초 대비 여유가 있으나, Duration이 20,000ms를 넘기 시작하면 상한을 15초로 낮추고 부분 결과를 반환하도록 바꾼다.
