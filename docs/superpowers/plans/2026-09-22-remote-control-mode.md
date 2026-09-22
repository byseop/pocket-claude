# pocket-claude v5 Remote Control 모드 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 텔레그램 `/start`로 EC2를 켜면 systemd가 Claude Code Remote Control 서버 세션(`gamer4-ops`)을 자동으로 띄우고, 텔레그램 봇은 전원 스위치 + 세션 유닛 관리(`/sessions` `/new` `/kill` `/rm`)만 맡도록 바꾼다. 텔레그램 채널 플러그인(메인 클로드 봇)은 폐기한다.

**Architecture:** EC2에는 systemd 템플릿 유닛 `claude-rc@<name>`이 있고, 각 인스턴스는 tmux 세션 `rc-<name>` 안에서 `claude remote-control --name gamer4-<name>`을 돌린다. `ops`는 메인 checkout(`~/gamer4info`)에서 부팅 시 자동 기동되고, 그 외 이름은 `~/worktrees/<name>` git 워크트리에서 `/new`로 켠다. Lambda v5는 `ec2:Start/StopInstances`와 짧은 SSM 스크립트(유닛 on/off, 상태 조회)만 실행하며 프로세스 수명은 전적으로 systemd가 갖는다. `idle-watch.sh`는 tmux 화면 해시 대신 트랜스크립트 mtime·bg 작업·CPU 증가분으로 유휴를 판정한다.

**Tech Stack:** Python 3.11 (Lambda, stdlib `unittest`), boto3, bash, systemd 템플릿 유닛, tmux, cron, AWS SSM RunShellScript(`/bin/sh` = dash), Telegram Bot API, Claude Code CLI 2.1.278 (`claude remote-control`)

**Spec:** `docs/superpowers/specs/2026-09-22-remote-control-mode-design.md` (조사 보고서 `docs/research/2026-09-22-ec2-remote-control-report.md`)

## Global Constraints

- 인스턴스 ID·계정 ID는 소스에 넣지 않는다. Lambda는 `INSTANCE_ID` 환경변수로만 참조하고, IAM 템플릿은 `AWS_ACCOUNT_ID`/`INSTANCE_ID` 자리표시자를 쓴다 (퍼블릭 저장소).
- Lambda의 어떤 코드 경로도 길게 블로킹하지 않는다. `waiter` 금지. `ssm_run`의 폴링 상한은 25초 이하. API Gateway 통합 타임아웃은 30,000ms.
- SSM RunShellScript는 `/bin/sh`(dash)로 실행된다. SSM에 보내는 스크립트에 bash 전용 문법(`[[ ]]`, `<(...)`, 배열)을 쓰지 않는다. SSM은 마지막 명령의 종료 코드로 성공/실패를 판정하므로 실패해도 되는 마지막 줄에는 `|| true`를 붙인다.
- 세션 실행 명령은 정확히 `claude remote-control --name "gamer4-<name>" --spawn same-dir --capacity 2 --permission-mode default --no-chrome`.
- 래퍼는 `CLAUDE_CODE_OAUTH_TOKEN`을 절대 export하지 않는다. `~/.claude/.env`를 source하지 않는다.
- `claude-supervise.sh`의 만료-credentials 가드와 낡은-데몬 가드 코드는 새 래퍼로 **옮겨서 유지**한다 (지우지 않는다).
- `<name>` 검증 규칙: `^[a-z0-9-]{1,24}$`, `ops`는 예약. `/kill ops`·`/rm ops`는 거부.
- sudoers에는 `systemctl start|stop claude-rc@*`만 NOPASSWD로 허용한다. `sudo systemctl` 전체 허용 금지.
- 셸 스크립트·JSON 주석은 영어. 텔레그램 응답 문구와 문서는 한글.
- 커밋 메시지는 한글, `[태그] 설명` 형식. **Co-Authored-By 등 Claude 서명 트레일러를 넣지 않는다** (사용자 메모리 규칙).
- 로컬 환경: Python 3.11, `pytest`·`bats` 없음 → Python은 `python3 -m unittest`, 셸은 bash 단정문 스크립트로 테스트한다. 셸 테스트는 macOS(BSD 도구)와 Ubuntu 양쪽에서 돌아야 하므로 GNU 전용 옵션(`find -printf`, `date -d`, `touch -d`)을 쓰지 않는다.
- `claude doctor`는 TTY 없이 실행하면 출력 후 키 입력을 기다리며 블록된다(2.1.278 실측). SSM에서 호출하지 않는다.

## File Structure

| 파일 | 상태 | 책임 |
|---|---|---|
| `ec2/claude-rc@.service` | 신규 | systemd 템플릿 유닛. `%i`가 세션 이름 |
| `ec2/claude-rc-wrap.sh` | 신규 (`claude-supervise.sh` 대체) | tmux 세션 `rc-<name>` 생성·감시, 작업 디렉터리 결정, credentials·데몬 가드 |
| `ec2/claude-rc.sudoers` | 신규 | ubuntu가 `claude-rc@*` 유닛만 start/stop 가능 |
| `ec2/idle-watch.sh` | 재작성 | 트랜스크립트 mtime·bg 작업·CPU로 유휴 판정, 텔레그램 알림 후 self-stop |
| `ec2/install.sh` | 재작성 | 위 파일 배치, 구 `claude-telegram` 유닛 폐기, cron 등록 |
| `ec2/claude-settings.json` | 신규 | EC2 `~/.claude/settings.json` 템플릿 (allow/ask/deny 게이트) |
| `ec2/secrets.env.example` | 신규 | `~/.config/gamer4/secrets.env` 템플릿 |
| `ec2/claude-supervise.sh`, `ec2/claude-telegram.service`, `ec2/start-claude-telegram.sh` | 삭제 | v4 텔레그램 채널 봇 |
| `iam/gamer4-operator-policy.json` | 신규 | 인스턴스 역할 인라인 정책 (sam deploy·lambda·logs·s3 최소권한) |
| `src/lambda_function.py` | 수정 (v5) | 인자 있는 명령 라우팅, `/sessions` `/new` `/kill` `/rm`, `/status` 재작성, `/view` 제거 |
| `tests/test_lambda_function.py` | 수정 | 이름 검증·스크립트 문자열·상태 포맷·라우팅 테스트 |
| `tests/test_idle_watch.sh` | 신규 | 가짜 `~/.claude` 트리로 유휴 판정 분기 테스트 |
| `tests/test_claude_rc_wrap.sh` | 신규 | 래퍼의 경로·명령 결정 함수 테스트 |
| `ec2-claude-md-patch.md` | 재작성 | EC2 `~/.claude/CLAUDE.md`에 붙일 Remote Control 운영 규칙 |
| `docs/SETUP.md` | 신규 | 사용자 1회 작업 체크리스트(스펙 §3-6·§3-7) |
| `README.md`, `docs/OPERATIONS.md` | 수정 | v5 구조·명령·진단 반영 |

Lambda는 v4와 같이 **순수 함수(테스트 대상)와 AWS 호출을 분리**한다. SSM 스크립트는 순수 함수가 문자열로 만들고, 테스트는 그 문자열을 검증한다.

---

### Task 1: systemd 템플릿 유닛 + tmux 래퍼 + sudoers + 설치 스크립트

**Files:**
- Create: `ec2/claude-rc@.service`
- Create: `ec2/claude-rc-wrap.sh`
- Create: `ec2/claude-rc.sudoers`
- Create: `tests/test_claude_rc_wrap.sh`
- Modify: `ec2/install.sh` (전체 교체)
- Delete: `ec2/claude-supervise.sh`, `ec2/claude-telegram.service`, `ec2/start-claude-telegram.sh`

**Interfaces:**
- Consumes: 없음 (EC2 파일 시스템 규약만 — `/home/ubuntu/gamer4info`, `/home/ubuntu/worktrees/<name>`, `/home/ubuntu/.config/gamer4/secrets.env`)
- Produces:
  - systemd 유닛 이름 `claude-rc@<name>` (Task 3·4·5의 SSM 스크립트, Task 2의 문서가 참조)
  - tmux 세션 이름 `rc-<name>` (Task 4의 `/status` 화면 스캔이 `rc-ops`를 참조)
  - 래퍼 함수 `rc_workdir <name>` → 경로 문자열, `rc_command <name>` → 실행 명령 문자열
  - 래퍼는 기동 시 `/home/ubuntu/.claude-idle-state`를 지운다 (Task 2의 상태 파일)

- [ ] **Step 1: 래퍼 함수 테스트 작성 (RED)**

`tests/test_claude_rc_wrap.sh`:

```bash
#!/bin/bash
# Exercises the pure helpers of ec2/claude-rc-wrap.sh. The script is sourced,
# not run, so no tmux or systemd is involved.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=../ec2/claude-rc-wrap.sh
. "$HERE/../ec2/claude-rc-wrap.sh"

FAILS=0
assert_eq() {
  if [ "$1" = "$2" ]; then
    echo "ok   $3"
  else
    echo "FAIL $3"; echo "     expected: $2"; echo "     got:      $1"
    FAILS=$((FAILS + 1))
  fi
}
assert_contains() {
  case "$1" in
    *"$2"*) echo "ok   $3" ;;
    *) echo "FAIL $3"; echo "     missing:  $2"; echo "     in:       $1"; FAILS=$((FAILS + 1)) ;;
  esac
}

assert_eq "$(rc_workdir ops)" "/home/ubuntu/gamer4info" "ops runs in the main checkout"
assert_eq "$(rc_workdir feat-x)" "/home/ubuntu/worktrees/feat-x" "other names run in a worktree"

CMD=$(rc_command feat-x)
assert_contains "$CMD" 'claude remote-control' "command is the server mode"
assert_contains "$CMD" '--name "gamer4-feat-x"' "session name carries the gamer4 prefix"
assert_contains "$CMD" '--spawn same-dir' "spawn mode is same-dir"
assert_contains "$CMD" '--capacity 2' "capacity is capped at 2"
assert_contains "$CMD" '--permission-mode default' "permission mode is default"
assert_contains "$CMD" '--no-chrome' "chrome is off"

case "$(cat "$HERE/../ec2/claude-rc-wrap.sh")" in
  *'CLAUDE_CODE_OAUTH_TOKEN='*|*'. /home/ubuntu/.claude/.env'*|*'source /home/ubuntu/.claude/.env'*)
    echo "FAIL wrapper must never export or source the OAuth token"; FAILS=$((FAILS + 1)) ;;
  *) echo "ok   wrapper never exports the OAuth token" ;;
esac

[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }
```

- [ ] **Step 2: 실패 확인**

Run: `bash tests/test_claude_rc_wrap.sh`
Expected: `No such file or directory` (래퍼가 없음) 또는 `rc_workdir: command not found` 로 실패

- [ ] **Step 3: 래퍼 작성**

`ec2/claude-rc-wrap.sh`:

```bash
#!/bin/bash
# Wrapper that systemd can track for one Remote Control server session.
# Running `tmux new-session -d` directly from ExecStart does not work: the
# tmux server daemonizes itself (double fork + setsid), systemd loses the
# PID, decides the service died, and Restart= spins forever.
#
# Usage: claude-rc-wrap.sh <name>     (systemd passes %i)
#   ops    -> /home/ubuntu/gamer4info        (main checkout, enabled at boot)
#   other  -> /home/ubuntu/worktrees/<name>  (git worktree, started by /new)
#
# The OAuth token in ~/.claude/.env is deliberately NOT sourced here. Remote
# Control refuses to start when CLAUDE_CODE_OAUTH_TOKEN is set ("API-key auth
# takes precedence"); the session must run on the claude.ai login stored in
# ~/.claude/.credentials.json.

set -u

REPO=/home/ubuntu/gamer4info
WORKTREES=/home/ubuntu/worktrees
IDLE_STATE=/home/ubuntu/.claude-idle-state
CREDS=/home/ubuntu/.claude/.credentials.json
ENV_FILE=/home/ubuntu/.claude/.env

rc_workdir() {
  if [ "$1" = ops ]; then echo "$REPO"; else echo "$WORKTREES/$1"; fi
}

rc_command() {
  printf 'claude remote-control --name "gamer4-%s" --spawn same-dir --capacity 2 --permission-mode default --no-chrome' "$1"
}

# An expired .credentials.json used to mask CLAUDE_CODE_OAUTH_TOKEN in the
# --channels path. That token is gone in v5, so this guard only fires if
# someone puts the token back into .env. Kept on purpose: the failure mode
# was hard to diagnose and cheap to prevent.
guard_expired_credentials() {
  [ -f "$CREDS" ] || return 0
  grep -q 'CLAUDE_CODE_OAUTH_TOKEN' "$ENV_FILE" 2>/dev/null || return 0
  if ! python3 - "$CREDS" <<'PY'
import json, sys, time
try:
    exp = json.load(open(sys.argv[1]))['claudeAiOauth']['expiresAt'] / 1000
except Exception:
    sys.exit(0)          # unreadable or unexpected shape: treat as valid, leave it
sys.exit(0 if exp > time.time() else 1)
PY
  then
    mv "$CREDS" "$CREDS.expired"
    logger -t claude-rc-wrap "moved expired .credentials.json aside; using CLAUDE_CODE_OAUTH_TOKEN"
  fi
}

# `claude --bg` hands work to an already-running supervisor daemon. A daemon
# that came up while auth was broken keeps that state and fails every
# dispatch after it. Drop a daemon older than the current credentials; leave
# a newer one alone so in-flight background work survives a restart.
guard_stale_daemon() {
  local ref
  if [ -f "$CREDS" ]; then ref=$CREDS; elif [ -f "$ENV_FILE" ]; then ref=$ENV_FILE; else return 0; fi
  local pid elapsed started ref_mtime
  pid=$(pgrep -u "$(id -u)" -f 'claude daemon run' 2>/dev/null | head -1)
  [ -n "$pid" ] || return 0
  elapsed=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
  [ -n "$elapsed" ] || return 0
  started=$(( $(date +%s) - elapsed ))
  ref_mtime=$(stat -c %Y "$ref" 2>/dev/null || echo 0)
  if [ "$ref_mtime" -gt "$started" ]; then
    pkill -u "$(id -u)" -f 'claude daemon run' 2>/dev/null || true
    rm -rf "/tmp/cc-daemon-$(id -u)"
    logger -t claude-rc-wrap "dropped supervisor daemon older than current credentials"
  fi
}

main() {
  local name=${1:?usage: claude-rc-wrap.sh <name>}
  local session="rc-$name"
  local dir
  dir=$(rc_workdir "$name")

  export HOME=/home/ubuntu
  export NVM_DIR=/home/ubuntu/.nvm
  [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
  export BUN_INSTALL=/home/ubuntu/.bun
  export PATH="/home/ubuntu/.local/bin:/home/ubuntu/.bun/bin:$PATH"

  if [ ! -d "$dir" ]; then
    logger -t claude-rc-wrap "workdir $dir does not exist for $name"
    exit 1
  fi

  # Clear the stale idle counter. Without this, a boot right after an idle
  # shutdown inherits the old timestamp and shuts down again immediately.
  rm -f "$IDLE_STATE"

  guard_expired_credentials
  guard_stale_daemon

  tmux has-session -t "$session" 2>/dev/null || \
    tmux new-session -d -s "$session" -c "$dir" "$(rc_command "$name")"

  # Block while the session lives. Exit non-zero when it dies so systemd
  # restarts it (Restart=on-failure).
  while tmux has-session -t "$session" 2>/dev/null; do
    sleep 10
  done
  exit 1
}

# Run main only when executed, so tests can source the helpers above.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `bash tests/test_claude_rc_wrap.sh && bash -n ec2/claude-rc-wrap.sh`
Expected: 모든 줄 `ok`, 마지막 `all passed`

- [ ] **Step 5: systemd 템플릿 유닛 작성**

`ec2/claude-rc@.service`:

```ini
[Unit]
Description=Claude Code Remote Control session (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Environment=HOME=/home/ubuntu
# Optional: GH_TOKEN, VERCEL_TOKEN, SUPABASE_ACCESS_TOKEN, TELEGRAM_* for the
# session's tools. The leading "-" keeps the unit bootable when the file is
# missing. Never put CLAUDE_CODE_OAUTH_TOKEN here.
EnvironmentFile=-/home/ubuntu/.config/gamer4/secrets.env
ExecStart=/home/ubuntu/bin/claude-rc-wrap.sh %i
ExecStop=/usr/bin/tmux kill-session -t rc-%i
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

`WorkingDirectory=`는 쓰지 않는다. `ops`만 경로가 다르므로 래퍼가 `tmux new-session -c`로 정한다.

- [ ] **Step 6: sudoers 작성**

`ec2/claude-rc.sudoers`:

```
# Lets the ubuntu user (the ops session, and SSM scripts running as ubuntu)
# start and stop Remote Control session units, and nothing else.
# Installed to /etc/sudoers.d/claude-rc with mode 0440.
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl start claude-rc@*, /usr/bin/systemctl stop claude-rc@*
```

- [ ] **Step 7: 설치 스크립트 교체**

`ec2/install.sh` 전체를 아래로 교체한다:

```bash
#!/bin/bash
# Installs the Remote Control units, wrapper, sudoers and idle watcher, and
# retires the v4 Telegram channel bot. Idempotent: safe to re-run.
# Run as root on the instance after copying ec2/* to /tmp.
set -eu

install -d -o ubuntu -g ubuntu -m 0755 /home/ubuntu/bin /home/ubuntu/worktrees
install -o ubuntu -g ubuntu -m 0755 /tmp/claude-rc-wrap.sh /home/ubuntu/bin/claude-rc-wrap.sh
install -o root -g root -m 0644 /tmp/claude-rc@.service /etc/systemd/system/claude-rc@.service

# A syntax error in sudoers locks everyone out of sudo. Validate first.
visudo -cf /tmp/claude-rc.sudoers
install -o root -g root -m 0440 /tmp/claude-rc.sudoers /etc/sudoers.d/claude-rc

install -o ubuntu -g ubuntu -m 0755 /tmp/idle-watch.sh /home/ubuntu/idle-watch.sh

# Retire the v4 Telegram channel bot. Its unit must not race the new one.
if [ -f /etc/systemd/system/claude-telegram.service ]; then
  systemctl disable --now claude-telegram || true
  rm -f /etc/systemd/system/claude-telegram.service
fi
rm -f /home/ubuntu/claude-supervise.sh /home/ubuntu/start-claude-telegram.sh

systemctl daemon-reload
# Enable only. The first start is interactive (workspace trust + "Enable
# Remote Control?"), see docs/SETUP.md; after that `systemctl start` works.
systemctl enable claude-rc@ops

# Register the cron entry idempotently: drop any previous line, then add ours.
# The `|| true` matters: with no existing crontab, grep receives zero lines and
# exits 1, and `set -e` would kill the subshell before the echo runs, piping an
# empty crontab in and wiping whatever was there.
CRON_LINE='*/5 * * * * /home/ubuntu/idle-watch.sh >/dev/null 2>&1'
{ sudo -u ubuntu crontab -l 2>/dev/null | grep -v 'idle-watch.sh' || true
  echo "$CRON_LINE"
} | sudo -u ubuntu crontab -

echo "installed"
```

- [ ] **Step 8: 구 파일 삭제 및 문법 검사**

```bash
git rm -q ec2/claude-supervise.sh ec2/claude-telegram.service ec2/start-claude-telegram.sh
bash -n ec2/install.sh && bash -n ec2/claude-rc-wrap.sh && bash tests/test_claude_rc_wrap.sh
```
Expected: 오류 없음, `all passed`

- [ ] **Step 9: 커밋**

```bash
git add ec2/claude-rc@.service ec2/claude-rc-wrap.sh ec2/claude-rc.sudoers ec2/install.sh tests/test_claude_rc_wrap.sh
git commit -m "[feat] Remote Control 세션용 systemd 템플릿 유닛과 tmux 래퍼 추가"
```

---

### Task 2: idle-watch.sh 재작성 (활동 신호 기반)

**Files:**
- Modify: `ec2/idle-watch.sh` (전체 교체)
- Create: `tests/test_idle_watch.sh`

**Interfaces:**
- Consumes: 상태 파일 `/home/ubuntu/.claude-idle-state` (Task 1 래퍼가 기동 시 삭제), `/home/ubuntu/.config/gamer4/secrets.env`의 `TELEGRAM_TOKEN`·`TELEGRAM_CHAT_ID` (Task 6 템플릿)
- Produces: 환경변수 오버라이드 계약 — `STATE` `PROJECTS_DIR` `JOBS_DIR` `SECRETS_FILE` `IDLE_MINUTES` `BG_STALE_SECONDS` `DRY_RUN`. 표준출력 한 줄: `idle=<분>/<IDLE_MINUTES> cpu=<초> last=<epoch>`, 정지 시 추가로 `stop`. 상태 파일 형식 `<cpu초> <last_active_epoch>`.

**판정 규칙 (스펙 §3-4):** 활동 신호 = ① `PROJECTS_DIR/*/*.jsonl` 최신 mtime ② `JOBS_DIR/*/state.json`이 비종료 상태이고 `BG_STALE_SECONDS` 안에 갱신됨 ③ `claude` 프로세스 누적 CPU가 지난 샘플과 다름. `last_active = max(이전 last_active, ①, ②·③이면 now)`. `now - last_active >= IDLE_MINUTES*60`이면 알림 후 정지. 상태 파일이 없으면(부팅 직후) `last_active = now`로 시작해 유예를 준다.

- [ ] **Step 1: 셸 테스트 작성 (RED)**

`tests/test_idle_watch.sh`:

```bash
#!/bin/bash
# Exercises ec2/idle-watch.sh against a fake ~/.claude tree. No AWS, no cron,
# no tmux. `ps` is shadowed so the CPU signal is controllable.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=$HERE/../ec2/idle-watch.sh
FAILS=0

setup() {
  TMP=$(mktemp -d)
  mkdir -p "$TMP/projects/-home-ubuntu-gamer4info" "$TMP/jobs" "$TMP/bin"
  # Fake ps: one claude process whose cumulative CPU is $FAKE_CPU seconds.
  printf '#!/bin/sh\necho "${FAKE_CPU:-0} claude"\n' > "$TMP/bin/ps"
  chmod +x "$TMP/bin/ps"
}

run() {   # usage: run [IDLE_MINUTES]
  PATH="$TMP/bin:$PATH" STATE="$TMP/state" PROJECTS_DIR="$TMP/projects" \
  JOBS_DIR="$TMP/jobs" SECRETS_FILE="$TMP/no-secrets" DRY_RUN=1 \
  IDLE_MINUTES="${1:-60}" bash "$SCRIPT"
}

ago() { echo $(( $(date +%s) - $1 )); }

touch_ago() {   # usage: touch_ago <file> <seconds>
  python3 -c 'import os,sys,time
t = time.time() - int(sys.argv[2]); os.utime(sys.argv[1], (t, t))' "$1" "$2"
}

assert_eq() {
  if [ "$1" = "$2" ]; then echo "ok   $3"
  else echo "FAIL $3"; echo "     expected: $2"; echo "     got:      $1"; FAILS=$((FAILS + 1)); fi
}
stopped() { case "$1" in *stop*) echo yes ;; *) echo no ;; esac; }

# 1. First run after boot: no state file, no signals -> grace period, no stop.
setup
OUT=$(run)
assert_eq "$(stopped "$OUT")" no "first run never stops"
assert_eq "$(cut -d' ' -f1 "$TMP/state")" 0 "state records the cpu sample"

# 2. Idle for two hours with no signals -> stop.
setup
echo "0 $(ago 7200)" > "$TMP/state"
OUT=$(run)
assert_eq "$(stopped "$OUT")" yes "two idle hours stop the instance"

# 3. A transcript written 10 minutes ago resets the clock.
setup
echo "0 $(ago 7200)" > "$TMP/state"
touch "$TMP/projects/-home-ubuntu-gamer4info/abc.jsonl"
touch_ago "$TMP/projects/-home-ubuntu-gamer4info/abc.jsonl" 600
OUT=$(run)
assert_eq "$(stopped "$OUT")" no "recent transcript keeps the instance up"
LAST=$(cut -d' ' -f2 "$TMP/state")
[ "$LAST" -ge "$(ago 660)" ] && [ "$LAST" -le "$(ago 540)" ] \
  && echo "ok   last_active follows the transcript mtime" \
  || { echo "FAIL last_active should be ~600s ago, got $LAST"; FAILS=$((FAILS + 1)); }

# 4. A transcript older than the window does not help.
setup
echo "0 $(ago 7200)" > "$TMP/state"
touch "$TMP/projects/-home-ubuntu-gamer4info/old.jsonl"
touch_ago "$TMP/projects/-home-ubuntu-gamer4info/old.jsonl" 7000
OUT=$(run)
assert_eq "$(stopped "$OUT")" yes "old transcript does not count"

# 5. A running background job that was touched recently keeps it up.
setup
echo "0 $(ago 7200)" > "$TMP/state"
mkdir -p "$TMP/jobs/j1"; echo '{"state": "running"}' > "$TMP/jobs/j1/state.json"
OUT=$(run)
assert_eq "$(stopped "$OUT")" no "active background job keeps the instance up"

# 6. A blocked job is terminal: it waits on a human and must not pin the box.
setup
echo "0 $(ago 7200)" > "$TMP/state"
mkdir -p "$TMP/jobs/j2"; echo '{"state": "blocked"}' > "$TMP/jobs/j2/state.json"
OUT=$(run)
assert_eq "$(stopped "$OUT")" yes "blocked job does not count"

# 7. A stale running job (not touched within BG_STALE_SECONDS) does not count.
setup
echo "0 $(ago 7200)" > "$TMP/state"
mkdir -p "$TMP/jobs/j3"; echo '{"state": "running"}' > "$TMP/jobs/j3/state.json"
touch_ago "$TMP/jobs/j3/state.json" 3600
OUT=$(run)
assert_eq "$(stopped "$OUT")" yes "stale job does not count"

# 8. CPU moved since the last sample -> activity.
setup
echo "100 $(ago 7200)" > "$TMP/state"
OUT=$(FAKE_CPU=130 run)
assert_eq "$(stopped "$OUT")" no "cpu change keeps the instance up"
assert_eq "$(cut -d' ' -f1 "$TMP/state")" 130 "new cpu sample is stored"

# 9. Same CPU as last sample -> no activity.
setup
echo "100 $(ago 7200)" > "$TMP/state"
OUT=$(FAKE_CPU=100 run)
assert_eq "$(stopped "$OUT")" yes "flat cpu is idle"

# 10. IDLE_MINUTES is honoured.
setup
echo "0 $(ago 5400)" > "$TMP/state"
OUT=$(run 120)
assert_eq "$(stopped "$OUT")" no "90 idle minutes are under a 120 minute limit"

# 11. Status line is printed every run.
setup
echo "0 $(ago 600)" > "$TMP/state"
OUT=$(run)
case "$OUT" in idle=10/60*) echo "ok   status line reports idle minutes" ;;
  *) echo "FAIL status line: $OUT"; FAILS=$((FAILS + 1)) ;; esac

[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }
```

- [ ] **Step 2: 실패 확인**

Run: `bash tests/test_idle_watch.sh`
Expected: 현재 스크립트는 tmux 세션이 없으면 `exit 0`만 하므로 케이스 2·4·6·7·9의 "stop" 단정이 FAIL, 케이스 1의 state 파일 단정이 FAIL

- [ ] **Step 3: idle-watch.sh 교체**

`ec2/idle-watch.sh` 전체:

```bash
#!/bin/bash
# Stops this instance once no activity signal has fired for IDLE_MINUTES.
# Runs from the ubuntu crontab every 5 minutes.
#
# Remote Control sessions talk to the phone over the network, so the tmux
# screen can sit still while real work is going on. The screen hash the v4
# watcher used is gone. Activity now means any of:
#   1. a conversation transcript (PROJECTS_DIR/*/*.jsonl) was written
#   2. a `claude --bg` job is non-terminal and was touched recently
#   3. a claude process burned CPU since the previous sample
# State file: "<cpu seconds> <last active epoch>". The wrapper deletes it at
# boot so a fresh boot always gets a full IDLE_MINUTES grace period.
set -u

STATE=${STATE:-/home/ubuntu/.claude-idle-state}
PROJECTS_DIR=${PROJECTS_DIR:-/home/ubuntu/.claude/projects}
JOBS_DIR=${JOBS_DIR:-/home/ubuntu/.claude/jobs}
SECRETS_FILE=${SECRETS_FILE:-/home/ubuntu/.config/gamer4/secrets.env}
IDLE_MINUTES=${IDLE_MINUTES:-60}
# A background job counts as working only if it is both non-terminal and
# recently touched. Without the freshness bound, a session stuck in a
# non-terminal state would pin the instance on forever.
BG_STALE_SECONDS=${BG_STALE_SECONDS:-1800}
REGION=${REGION:-ap-northeast-2}
DRY_RUN=${DRY_RUN:-0}

NOW=$(date +%s)

# Cumulative CPU seconds of every claude process owned by this user.
# `cputimes` is procps (Linux); anywhere else this prints 0 and the other
# two signals carry the decision.
claude_cpu() {
  ps -u "$(id -un)" -o cputimes= -o comm= 2>/dev/null \
    | awk '$2 == "claude" { s += $1 } END { print s + 0 }'
}

# Prints the epoch of the newest transcript, or NOW when a fresh non-terminal
# background job exists. Prints 0 when neither signal is present.
file_signal() {
  python3 - "$PROJECTS_DIR" "$JOBS_DIR" "$BG_STALE_SECONDS" <<'PY'
import glob, json, os, sys, time

projects, jobs, stale = sys.argv[1], sys.argv[2], int(sys.argv[3])
now = time.time()
last = 0

for path in glob.glob(os.path.join(projects, '*', '*.jsonl')):
    try:
        last = max(last, os.path.getmtime(path))
    except OSError:
        pass

# States that mean the job will not do further work on its own. "blocked"
# belongs here: it waits on a human, so it must not hold the instance up.
TERMINAL = {'done', 'failed', 'blocked', 'idle', 'cancelled', 'stopped',
            'completed', 'error'}
if os.path.isdir(jobs):
    for entry in os.scandir(jobs):
        if not entry.is_dir():
            continue
        try:
            state = json.load(open(os.path.join(entry.path, 'state.json'))).get('state')
        except Exception:
            continue
        if state in TERMINAL:
            continue
        touched = max(
            (os.path.getmtime(os.path.join(entry.path, f))
             for f in ('state.json', 'timeline.jsonl')
             if os.path.exists(os.path.join(entry.path, f))),
            default=0,
        )
        if now - touched < stale:
            last = now
            break

print(int(last))
PY
}

CPU=$(claude_cpu)
PREV_CPU=''
PREV_ACTIVE=''
[ -f "$STATE" ] && read -r PREV_CPU PREV_ACTIVE < "$STATE"
: "${PREV_CPU:=$CPU}" "${PREV_ACTIVE:=$NOW}"

LAST=$(file_signal)
[ "$CPU" -ne "$PREV_CPU" ] && LAST=$NOW
[ "$LAST" -lt "$PREV_ACTIVE" ] && LAST=$PREV_ACTIVE
echo "$CPU $LAST" > "$STATE"

IDLE=$(( (NOW - LAST) / 60 ))
echo "idle=${IDLE}/${IDLE_MINUTES} cpu=${CPU} last=${LAST}"
[ "$IDLE" -lt "$IDLE_MINUTES" ] && exit 0

if [ "$DRY_RUN" = 1 ]; then
  echo "stop"
  exit 0
fi

logger -t idle-watch "idle ${IDLE} min; stopping instance"

# Notify through the boot-manager bot token kept in secrets.env, so the
# watcher needs no credentials of its own.
if [ -f "$SECRETS_FILE" ]; then
  TELEGRAM_TOKEN=''; TELEGRAM_CHAT_ID=''
  . "$SECRETS_FILE"
  if [ -n "$TELEGRAM_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
      -d chat_id="$TELEGRAM_CHAT_ID" \
      -d text="💤 ${IDLE}분 유휴 — EC2를 자동 종료합니다. /start 로 다시 켜세요." >/dev/null
  fi
fi

T=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
IID=$(curl -s -H "X-aws-ec2-metadata-token: $T" http://169.254.169.254/latest/meta-data/instance-id)
aws ec2 stop-instances --region "$REGION" --instance-ids "$IID"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `bash tests/test_idle_watch.sh && bash tests/test_claude_rc_wrap.sh`
Expected: 두 파일 모두 `all passed`

- [ ] **Step 5: 커밋**

```bash
git add ec2/idle-watch.sh tests/test_idle_watch.sh
git commit -m "[feat] 유휴 감시를 화면 해시 대신 트랜스크립트·bg 작업·CPU 신호로 판정"
```

---

### Task 3: Lambda v5 — 인자 라우팅, 이름 검증, `/new` `/kill`

**Files:**
- Modify: `src/lambda_function.py`
- Modify: `tests/test_lambda_function.py`

**Interfaces:**
- Consumes: Task 1의 유닛 이름 `claude-rc@<name>`, 경로 규약 `/home/ubuntu/gamer4info`, `/home/ubuntu/worktrees/<name>`
- Produces (Task 4·5가 사용):
  - `parse_command(text) -> (str, str)` — `'/new@bot feat-x'` → `('/new', 'feat-x')`
  - `validate_name(name) -> str | None` — 오류 메시지 또는 `None`
  - `workdir(name) -> str`
  - `as_ubuntu(body) -> str` — POSIX sh 본문을 `sudo -u ubuntu -H sh` heredoc으로 감싼 SSM 스크립트
  - `new_session_script(name) -> str`, `kill_session_script(name) -> str`
  - 핸들러 시그니처 `cmd_x(chat_id, arg)` — `HANDLERS` 딕셔너리의 모든 값이 두 인자를 받는다
  - 상수 `OPS = 'ops'`, `UNIT_PREFIX = 'claude-rc@'`, `REPO`, `WORKTREES`, `SESSION_PREFIX = 'gamer4-'`

- [ ] **Step 1: 테스트 추가 (RED)**

`tests/test_lambda_function.py`에서 `TestHandlerRouting` 클래스 **앞에** 아래 클래스들을 추가하고, `TestHandlerRouting`은 아래와 같이 **교체**한다.

```python
class TestParseCommand(unittest.TestCase):
    def test_splits_command_and_argument(self):
        self.assertEqual(lf.parse_command('/new feat-x'), ('/new', 'feat-x'))

    def test_strips_bot_suffix_from_command_only(self):
        self.assertEqual(lf.parse_command('/new@pocket_bot feat-x'), ('/new', 'feat-x'))

    def test_no_argument_gives_empty_string(self):
        self.assertEqual(lf.parse_command('/status'), ('/status', ''))

    def test_collapses_surrounding_whitespace(self):
        self.assertEqual(lf.parse_command('  /kill   t1  '), ('/kill', 't1'))


class TestValidateName(unittest.TestCase):
    def test_accepts_lowercase_digits_and_hyphen(self):
        self.assertIsNone(lf.validate_name('feat-x2'))

    def test_rejects_empty(self):
        self.assertIsNotNone(lf.validate_name(''))

    def test_rejects_uppercase(self):
        self.assertIsNotNone(lf.validate_name('Feat'))

    def test_rejects_shell_metacharacters(self):
        for bad in ('a;b', 'a b', 'a/b', '$(x)', '../x', 'a`b'):
            self.assertIsNotNone(lf.validate_name(bad), bad)

    def test_rejects_over_24_chars(self):
        self.assertIsNone(lf.validate_name('a' * 24))
        self.assertIsNotNone(lf.validate_name('a' * 25))

    def test_reserves_ops(self):
        self.assertIn('ops', lf.validate_name('ops'))


class TestSessionScripts(unittest.TestCase):
    def test_workdir_ops_is_main_checkout(self):
        self.assertEqual(lf.workdir('ops'), '/home/ubuntu/gamer4info')

    def test_workdir_other_is_worktree(self):
        self.assertEqual(lf.workdir('t1'), '/home/ubuntu/worktrees/t1')

    def test_as_ubuntu_wraps_in_heredoc(self):
        out = lf.as_ubuntu('echo hi')
        self.assertTrue(out.startswith("sudo -u ubuntu -H sh <<'EOF'\n"))
        self.assertIn('\necho hi\n', out)
        self.assertTrue(out.rstrip('\n').endswith('EOF'))

    def test_new_creates_worktree_copies_env_and_starts_unit(self):
        s = lf.new_session_script('t1')
        self.assertIn('sudo -u ubuntu', s)
        self.assertIn('git -C /home/ubuntu/gamer4info worktree add /home/ubuntu/worktrees/t1 -b t1 origin/main', s)
        self.assertIn('cp /home/ubuntu/gamer4info/.env /home/ubuntu/worktrees/t1/.env', s)
        self.assertIn('sudo systemctl start claude-rc@t1', s)
        self.assertTrue(s.rstrip('\n').endswith('EOF'))

    def test_new_reuses_existing_branch(self):
        s = lf.new_session_script('t1')
        self.assertIn('show-ref --verify --quiet refs/heads/t1', s)
        self.assertIn('worktree add /home/ubuntu/worktrees/t1 t1\n', s)

    def test_new_uses_no_bash_only_syntax(self):
        s = lf.new_session_script('t1')
        self.assertNotIn('[[', s)
        self.assertNotIn('<(', s)

    def test_kill_stops_unit_and_keeps_worktree(self):
        s = lf.kill_session_script('t1')
        self.assertIn('sudo systemctl stop claude-rc@t1', s)
        self.assertNotIn('worktree remove', s)
        self.assertIn('|| true', s.splitlines()[-2])


class TestHandlerRouting(unittest.TestCase):
    """The handler must never raise: a crash means Telegram shows nothing."""

    def setUp(self):
        self.sent = []
        self.ran = []
        self._tg = lf.tg_send
        self._ssm = lf.ssm_run
        self._ready = lf.ssm_ready
        self._describe = lf.describe
        self._id = lf.INSTANCE_ID
        lf.tg_send = lambda chat_id, text: self.sent.append((chat_id, text))
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'active'
        lf.ssm_ready = lambda: True
        lf.describe = lambda: ('running', None)
        lf.INSTANCE_ID = 'i-test'
        os.environ['ALLOWED_CHAT_ID'] = '111'

    def tearDown(self):
        lf.tg_send = self._tg
        lf.ssm_run = self._ssm
        lf.ssm_ready = self._ready
        lf.describe = self._describe
        lf.INSTANCE_ID = self._id

    @staticmethod
    def _event(text, chat_id=111):
        import json as _j
        return {'body': _j.dumps({'message': {'chat': {'id': chat_id}, 'text': text}})}

    def test_rejects_other_chat_ids_without_replying(self):
        res = lf.lambda_handler(self._event('/status', chat_id=999), None)
        self.assertEqual(res['statusCode'], 200)
        self.assertEqual(self.sent, [])

    def test_unknown_command_gets_help(self):
        lf.lambda_handler(self._event('/nope'), None)
        self.assertIn('/start', self.sent[0][1])
        self.assertIn('/new', self.sent[0][1])

    def test_plain_text_is_ignored(self):
        lf.lambda_handler(self._event('안녕'), None)
        self.assertEqual(self.sent, [])

    def test_command_failure_is_reported_not_raised(self):
        def boom(chat_id, arg):
            raise RuntimeError('describe failed')

        lf.HANDLERS['/status'] = boom
        try:
            res = lf.lambda_handler(self._event('/status'), None)
        finally:
            lf.HANDLERS['/status'] = lf.cmd_status
        self.assertEqual(res['statusCode'], 200)
        self.assertTrue(self.sent, 'failure must be reported to Telegram')
        self.assertIn('describe failed', self.sent[0][1])

    def test_reports_even_when_body_is_malformed(self):
        res = lf.lambda_handler({'body': 'not json'}, None)
        self.assertEqual(res['statusCode'], 200)

    def test_new_with_bad_name_replies_without_ssm(self):
        lf.lambda_handler(self._event('/new Bad;Name'), None)
        self.assertEqual(self.ran, [])
        self.assertTrue(self.sent)

    def test_new_without_name_replies_usage(self):
        lf.lambda_handler(self._event('/new'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('/new', self.sent[0][1])

    def test_new_runs_script_and_names_session(self):
        lf.lambda_handler(self._event('/new t1'), None)
        self.assertEqual(len(self.ran), 1)
        self.assertIn('claude-rc@t1', self.ran[0])
        self.assertIn('gamer4-t1', self.sent[0][1])

    def test_new_when_instance_stopped_does_not_run_ssm(self):
        lf.describe = lambda: ('stopped', None)
        lf.lambda_handler(self._event('/new t1'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('/start', self.sent[0][1])

    def test_kill_ops_is_refused(self):
        lf.lambda_handler(self._event('/kill ops'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('ops', self.sent[0][1])

    def test_kill_runs_stop_script(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'inactive'
        lf.lambda_handler(self._event('/kill t1'), None)
        self.assertIn('systemctl stop claude-rc@t1', self.ran[0])
        self.assertIn('gamer4-t1', self.sent[0][1])
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: `AttributeError: module 'lambda_function' has no attribute 'parse_command'` 류의 ERROR 다수

- [ ] **Step 3: Lambda 구현**

`src/lambda_function.py`를 아래와 같이 고친다.

(a) 모듈 docstring과 상수 블록을 교체:

```python
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
```

(b) `format_error` 뒤, `build_status` 앞에 순수 헬퍼를 추가:

```python
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
```

(c) 명령 핸들러를 두 인자 시그니처로 바꾸고 `/new` `/kill`을 추가. `cmd_start`·`cmd_stop`·`cmd_status`·`cmd_view`의 정의를 `def cmd_x(chat_id, arg):`로 바꾼다 (본문은 이 태스크에서 그대로 둔다; Task 4가 `/status`·`/view`를 손본다). 그리고 `cmd_view` 뒤에 추가:

```python
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
```

`validate_name('ops')`가 오류를 돌려주므로 `/kill ops`·`/new ops`는 같은 경로로 거부된다.

(d) `HELP`·`HANDLERS`·`lambda_handler` 라우팅을 교체:

```python
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
```

`lambda_handler` 안에서:

```python
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
```

(`/rm`은 HELP에 미리 적지만 핸들러는 Task 5에서 붙인다. 그 전까지 `/rm`은 HELP로 응답한다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: 모든 테스트 `OK` (기존 `TestBuildStatus`의 `claude-telegram.service`·`claude setup-token` 단정은 아직 통과해야 한다 — `build_status`는 Task 4에서 바꾼다)

- [ ] **Step 5: 커밋**

```bash
git add src/lambda_function.py tests/test_lambda_function.py
git commit -m "[feat] 람다 v5 — 인자 라우팅과 /new /kill 세션 유닛 명령 추가"
```

---

### Task 4: Lambda v5 — `/status` 재작성, `/sessions`, `/start` 문구, `/view` 제거

**Files:**
- Modify: `src/lambda_function.py`
- Modify: `tests/test_lambda_function.py`

**Interfaces:**
- Consumes: Task 3의 `as_ubuntu`, `OPS`, `UNIT_PREFIX`, `SESSION_PREFIX`, `PROJECTS`, `CLAUDE_ENV`, `CLAUDE_CREDS`, `WORKTREES`, `REPO`, `require_running`, `detect_auth_error`, `format_uptime`; Task 1의 tmux 세션명 `rc-ops`
- Produces:
  - `status_script() -> str` — 출력 3구간을 `---`로 구분: ① `list-units` 결과 `<unit> <active>` 줄들 ② `rc-ops` 화면 ③ 플래그 줄 (`TOKEN_IN_ENV`, `CREDS_OK`/`CREDS_MISSING`)
  - `parse_units(text) -> list[tuple[str, str]]` — `[('ops', 'active'), ...]`
  - `auth_problem(pane, flags) -> str | None`
  - `build_status(state, uptime, units, auth_error) -> str`
  - `sessions_script() -> str`, `parse_sessions(text) -> list[tuple[str, str, str, int]]`, `format_age(epoch, now_epoch) -> str`, `build_sessions(rows, now_epoch) -> str`

- [ ] **Step 1: 테스트 교체·추가 (RED)**

`TestBuildStatus` 클래스를 아래로 **교체**하고, 그 뒤에 `TestStatusParsing`·`TestSessions`를 추가한다. `TestHandlerRouting`에는 마지막 세 메서드를 추가한다.

```python
class TestBuildStatus(unittest.TestCase):
    def test_all_healthy(self):
        out = lf.build_status('running', '2h 13m', [('ops', 'active')], None)
        self.assertIn('✅ EC2 running (2h 13m)', out)
        self.assertIn('🟢 claude-rc@ops active', out)
        self.assertIn('✅ 인증 OK', out)
        self.assertNotIn('❌', out)

    def test_auth_failure_includes_recovery_steps(self):
        out = lf.build_status('running', '5m', [('ops', 'active')], 'API Error: 401 revoked')
        self.assertIn('❌', out)
        self.assertIn('claude auth login', out)
        self.assertIn('401', out)
        self.assertNotIn('setup-token', out)

    def test_ops_unit_down(self):
        out = lf.build_status('running', '5m', [('ops', 'inactive')], None)
        self.assertIn('❌ claude-rc@ops', out)

    def test_ops_unit_missing_is_reported_down(self):
        out = lf.build_status('running', '5m', [], None)
        self.assertIn('❌ claude-rc@ops', out)

    def test_extra_units_are_listed(self):
        out = lf.build_status('running', '5m', [('ops', 'active'), ('t1', 'activating')], None)
        self.assertIn('claude-rc@t1 activating', out)

    def test_stopped_instance_skips_service_lines(self):
        out = lf.build_status('stopped', None, None, None)
        self.assertIn('EC2 stopped', out)
        self.assertIn('/start', out)
        self.assertNotIn('claude-rc@', out)


class TestStatusParsing(unittest.TestCase):
    def test_parse_units_extracts_name_and_state(self):
        text = 'claude-rc@ops.service active\nclaude-rc@t1.service inactive\n'
        self.assertEqual(lf.parse_units(text), [('ops', 'active'), ('t1', 'inactive')])

    def test_parse_units_ignores_noise(self):
        self.assertEqual(lf.parse_units('\n0 loaded units listed.\n'), [])

    def test_auth_problem_flags_token_left_in_env(self):
        msg = lf.auth_problem('', 'TOKEN_IN_ENV\nCREDS_OK')
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN', msg)

    def test_auth_problem_flags_missing_login(self):
        msg = lf.auth_problem('', 'CREDS_MISSING')
        self.assertIn('claude auth login', msg)

    def test_auth_problem_flags_401_on_screen(self):
        msg = lf.auth_problem('x\nAPI Error: 401 revoked\n', 'CREDS_OK')
        self.assertIn('401', msg)

    def test_auth_problem_none_when_healthy(self):
        self.assertIsNone(lf.auth_problem('Claude Code v2.1.278\n', 'CREDS_OK'))

    def test_status_script_reads_units_pane_and_flags(self):
        s = lf.status_script()
        self.assertIn("list-units 'claude-rc@*'", s)
        self.assertIn('tmux capture-pane -t rc-ops', s)
        self.assertIn('CLAUDE_CODE_OAUTH_TOKEN', s)
        self.assertIn('.credentials.json', s)
        self.assertEqual(s.count('echo ---'), 2)


class TestSessions(unittest.TestCase):
    NOW = 1_800_000_000

    def test_format_age(self):
        self.assertEqual(lf.format_age(self.NOW - 30, self.NOW), '방금')
        self.assertEqual(lf.format_age(self.NOW - 5 * 60, self.NOW), '5분 전')
        self.assertEqual(lf.format_age(self.NOW - 3 * 3600, self.NOW), '3시간 전')
        self.assertEqual(lf.format_age(self.NOW - 2 * 86400, self.NOW), '2일 전')

    def test_parse_sessions(self):
        text = f'ops active main {self.NOW - 60}\nt1 inactive t1 0\n'
        self.assertEqual(
            lf.parse_sessions(text),
            [('ops', 'active', 'main', self.NOW - 60), ('t1', 'inactive', 't1', 0)],
        )

    def test_parse_sessions_skips_malformed_lines(self):
        self.assertEqual(lf.parse_sessions('garbage\n'), [])

    def test_build_sessions_marks_active_and_age(self):
        rows = [('ops', 'active', 'main', self.NOW - 300), ('t1', 'inactive', 't1', 0)]
        out = lf.build_sessions(rows, self.NOW)
        self.assertIn('🟢 gamer4-ops', out)
        self.assertIn('main', out)
        self.assertIn('5분 전', out)
        self.assertIn('⚪ gamer4-t1', out)

    def test_build_sessions_empty(self):
        self.assertIn('없', lf.build_sessions([], self.NOW))

    def test_sessions_script_walks_ops_and_worktrees(self):
        s = lf.sessions_script()
        self.assertIn('sudo -u ubuntu', s)
        self.assertIn('/home/ubuntu/worktrees', s)
        self.assertIn('systemctl is-active', s)
        self.assertIn('rev-parse --abbrev-ref HEAD', s)
        self.assertIn('/home/ubuntu/.claude/projects', s)
        self.assertNotIn('[[', s)
```

`TestHandlerRouting`에 추가:

```python
    def test_view_is_gone(self):
        lf.lambda_handler(self._event('/view'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('/sessions', self.sent[0][1])   # falls through to HELP

    def test_status_reports_units_and_auth(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or (
            'claude-rc@ops.service active\n---\nClaude Code\n---\nCREDS_OK\n'
        )
        lf.describe = lambda: ('running', datetime.now(timezone.utc))
        lf.lambda_handler(self._event('/status'), None)
        self.assertIn('🟢 claude-rc@ops active', self.sent[0][1])
        self.assertIn('✅ 인증 OK', self.sent[0][1])

    def test_sessions_lists_rows(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'ops active main 0\n'
        lf.lambda_handler(self._event('/sessions'), None)
        self.assertIn('gamer4-ops', self.sent[0][1])

    def test_start_mentions_ops_session(self):
        started = []
        lf.describe = lambda: ('stopped', None)
        lf.ec2 = type('E', (), {'start_instances': lambda self, **kw: started.append(kw)})()
        lf.lambda_handler(self._event('/start'), None)
        self.assertTrue(started)
        self.assertIn('gamer4-ops', self.sent[0][1])
```

`test_start_mentions_ops_session`은 `lf.ec2`를 통째로 바꾸므로 `setUp`/`tearDown`에 `self._ec2 = lf.ec2` / `lf.ec2 = self._ec2`를 추가한다.

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: `parse_units`, `auth_problem`, `status_script`, `sessions_script`, `format_age` 부재로 ERROR; `TestBuildStatus`는 시그니처 불일치로 FAIL

- [ ] **Step 3: 구현**

(0) 상수 정리: `SESSION = 'claude'`와 `SERVICE = 'claude-telegram'` 줄과 그 위 주석을 삭제하고, `RECOVERY`를 교체한다:

```python
RECOVERY = (
    '   복구 (SSM 셸, ubuntu 사용자):\n'
    '   1. ~/.claude/.env 에서 CLAUDE_CODE_OAUTH_TOKEN 줄 제거\n'
    '   2. claude auth login   (claude.ai 선택)\n'
    '   3. sudo systemctl restart claude-rc@ops'
)
```

(a) `build_status`를 교체하고 그 앞에 파싱 헬퍼를 추가:

```python
def parse_units(text):
    """'claude-rc@ops.service active' lines -> [('ops', 'active'), ...]."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith(UNIT_PREFIX):
            continue
        name = parts[0][len(UNIT_PREFIX):].removesuffix('.service')
        rows.append((name, parts[1]))
    return rows


def auth_problem(pane, flags):
    """Return a human-readable auth problem, or None when auth looks fine.

    Remote Control only works on the claude.ai login in .credentials.json.
    A leftover CLAUDE_CODE_OAUTH_TOKEN in ~/.claude/.env makes the CLI
    refuse to start the server ("API-key auth takes precedence"), which is
    the most likely misconfiguration after the v4 -> v5 migration.
    """
    if 'TOKEN_IN_ENV' in flags:
        return '~/.claude/.env 에 CLAUDE_CODE_OAUTH_TOKEN 이 남아 있어요 (Remote Control 차단)'
    if 'CREDS_MISSING' in flags:
        return 'claude.ai 로그인이 없어요 — claude auth login 필요'
    return detect_auth_error(pane)


def build_status(state, uptime, units, auth_error):
    if state != 'running':
        return f'⚪ EC2 {state}\n   /start 로 켜세요.'

    lines = [f'✅ EC2 running ({uptime})']
    by_name = dict(units or [])
    ops_state = by_name.pop(OPS, None)
    if ops_state == 'active':
        lines.append(f'🟢 {UNIT_PREFIX}{OPS} active')
    else:
        lines.append(f'❌ {UNIT_PREFIX}{OPS} {ops_state or "없음"} — 정지됨')
    for name, unit_state in by_name.items():
        dot = '🟢' if unit_state == 'active' else '⚪'
        lines.append(f'{dot} {UNIT_PREFIX}{name} {unit_state}')
    if auth_error:
        lines.append(f'❌ 인증 실패\n   "{auth_error}"\n\n{RECOVERY}')
    else:
        lines.append('✅ 인증 OK')
    return '\n'.join(lines)


def format_age(epoch, now):
    secs = max(int(now - epoch), 0)
    if secs < 60:
        return '방금'
    if secs < 3600:
        return f'{secs // 60}분 전'
    if secs < 86400:
        return f'{secs // 3600}시간 전'
    return f'{secs // 86400}일 전'


def parse_sessions(text):
    """'<name> <active> <branch> <epoch>' lines -> tuples. Bad lines are dropped."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4 or not parts[3].isdigit():
            continue
        rows.append((parts[0], parts[1], parts[2], int(parts[3])))
    return rows


def build_sessions(rows, now):
    if not rows:
        return '세션 없음. /new <name> 으로 만드세요.'
    lines = []
    for name, active, branch, mtime in rows:
        dot = '🟢' if active == 'active' else '⚪'
        last = format_age(mtime, now) if mtime else '대화 없음'
        lines.append(f'{dot} {SESSION_PREFIX}{name}  [{branch}]  {last}')
    return '\n'.join(lines)
```

(b) `kill_session_script` 뒤에 스크립트 생성기를 추가:

```python
def status_script():
    """Three '---'-separated blocks: units, ops screen, auth flags."""
    return (
        f"systemctl list-units '{UNIT_PREFIX}*' --all --no-legend --plain "
        "| awk '{print $1, $3}' || true\n"
        'echo ---\n'
        f'sudo -u ubuntu tmux capture-pane -t rc-{OPS} -p -S -200 2>/dev/null || true\n'
        'echo ---\n'
        f'grep -q CLAUDE_CODE_OAUTH_TOKEN {CLAUDE_ENV} 2>/dev/null && echo TOKEN_IN_ENV\n'
        f'test -f {CLAUDE_CREDS} && echo CREDS_OK || echo CREDS_MISSING\n'
    )


def sessions_script():
    """One '<name> <active> <branch> <last transcript epoch>' line per session.

    The transcript directory is the working directory with '/' and '.'
    turned into '-', which is how Claude Code names ~/.claude/projects/*.
    """
    return as_ubuntu(
        f'for name in {OPS} $(ls {WORKTREES} 2>/dev/null); do\n'
        f'  if [ "$name" = {OPS} ]; then dir={REPO}; else dir={WORKTREES}/$name; fi\n'
        f'  active=$(systemctl is-active {UNIT_PREFIX}$name 2>/dev/null || true)\n'
        '  branch=$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")\n'
        "  slug=$(echo \"$dir\" | tr '/.' '--')\n"
        f'  last=$(ls -t {PROJECTS}/$slug/*.jsonl 2>/dev/null | head -1)\n'
        '  if [ -n "$last" ]; then mtime=$(stat -c %Y "$last"); else mtime=0; fi\n'
        '  echo "$name ${active:-unknown} $branch $mtime"\n'
        'done'
    )
```

(c) `cmd_start`의 응답 문구 교체:

```python
    tg_send(
        chat_id,
        f'🔄 EC2를 켰습니다. 1분쯤 뒤 Claude 앱 Code 탭에 {SESSION_PREFIX}{OPS} 세션이 보여요.\n'
        '세션은 systemd가 자동으로 띄웁니다. /status 로 확인.',
    )
```

(d) `cmd_status`를 교체하고 `cmd_view`를 삭제, `cmd_sessions` 추가:

```python
def cmd_status(chat_id, arg):
    state, launch = describe()
    if state != 'running':
        tg_send(chat_id, build_status(state, None, None, None))
        return
    if not ssm_ready():
        tg_send(chat_id, '🔄 부팅 중 — SSM 에이전트 대기 중입니다.')
        return

    out = ssm_run(status_script())
    units_text, _, rest = out.partition('---')
    pane, _, flags = rest.partition('---')
    uptime = format_uptime(launch, datetime.now(timezone.utc))
    tg_send(
        chat_id,
        build_status(state, uptime, parse_units(units_text), auth_problem(pane, flags)),
    )


def cmd_sessions(chat_id, arg):
    if not require_running(chat_id):
        return
    out = ssm_run(sessions_script())
    tg_send(chat_id, build_sessions(parse_sessions(out), time.time()))
```

(e) `HANDLERS`에서 `'/view': cmd_view` 줄을 지우고 `'/sessions': cmd_sessions` 추가. `strip_ansi`는 `/view` 전용이었으나 테스트가 있으므로 남긴다 (화면 스캔의 노이즈 제거에 `auth_problem` 호출 전 `strip_ansi(pane)`을 적용해도 좋다 — 적용한다면 `cmd_status`에서 `auth_problem(strip_ansi(pane), flags)`).

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 5: 커밋**

```bash
git add src/lambda_function.py tests/test_lambda_function.py
git commit -m "[feat] 람다 v5 — /status 유닛·인증 진단, /sessions 목록, /view 제거"
```

---

### Task 5: Lambda v5 — `/rm <name>`

**Files:**
- Modify: `src/lambda_function.py`
- Modify: `tests/test_lambda_function.py`

**Interfaces:**
- Consumes: Task 3의 `as_ubuntu`, `validate_name`, `workdir`, `require_running`, `UNIT_PREFIX`, `REPO`
- Produces: `rm_session_script(name) -> str` — 출력 마지막 줄이 `DIRTY`(미커밋 변경으로 거부) 또는 `REMOVED`

- [ ] **Step 1: 테스트 추가 (RED)**

`TestSessionScripts`에 추가:

```python
    def test_rm_refuses_dirty_worktree_before_removing(self):
        s = lf.rm_session_script('t1')
        self.assertIn('sudo systemctl stop claude-rc@t1', s)
        self.assertIn('git -C /home/ubuntu/worktrees/t1 status --porcelain', s)
        self.assertIn('echo DIRTY', s)
        self.assertIn('git -C /home/ubuntu/gamer4info worktree remove /home/ubuntu/worktrees/t1', s)
        self.assertIn('echo REMOVED', s)
        self.assertLess(s.index('echo DIRTY'), s.index('worktree remove'))
        self.assertNotIn('--force', s)
```

`TestHandlerRouting`에 추가:

```python
    def test_rm_ops_is_refused(self):
        lf.lambda_handler(self._event('/rm ops'), None)
        self.assertEqual(self.ran, [])
        self.assertIn('ops', self.sent[0][1])

    def test_rm_dirty_reports_refusal(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'DIRTY\n'
        lf.lambda_handler(self._event('/rm t1'), None)
        self.assertIn('커밋', self.sent[0][1])

    def test_rm_removed_reports_success(self):
        lf.ssm_run = lambda script, timeout=25: self.ran.append(script) or 'REMOVED\n'
        lf.lambda_handler(self._event('/rm t1'), None)
        self.assertIn('삭제', self.sent[0][1])
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: `rm_session_script` 부재 ERROR, `/rm` 라우팅 테스트 FAIL (HELP가 응답됨)

- [ ] **Step 3: 구현**

`kill_session_script` 뒤:

```python
def rm_session_script(name):
    d = workdir(name)
    return as_ubuntu(
        f'sudo systemctl stop {UNIT_PREFIX}{name}\n'
        f'if [ -d {d} ]; then\n'
        f'  if [ -n "$(git -C {d} status --porcelain)" ]; then echo DIRTY; exit 0; fi\n'
        f'  git -C {REPO} worktree remove {d}\n'
        'fi\n'
        'echo REMOVED'
    )
```

`cmd_kill` 뒤:

```python
def cmd_rm(chat_id, arg):
    err = validate_name(arg)
    if err:
        tg_send(chat_id, f'⚠️ {err}')
        return
    if not require_running(chat_id):
        return
    out = ssm_run(rm_session_script(arg), timeout=20)
    if 'DIRTY' in out:
        tg_send(
            chat_id,
            f'⚠️ {SESSION_PREFIX}{arg} 워크트리에 커밋 안 된 변경이 있어 삭제하지 않았어요.\n'
            '세션은 정지했습니다. 앱에서 커밋·푸시한 뒤 다시 /rm.',
        )
    elif 'REMOVED' in out:
        tg_send(chat_id, f'🗑 {SESSION_PREFIX}{arg} 워크트리 삭제. 브랜치는 남아 있어요.')
    else:
        tg_send(chat_id, f'⚠️ /rm 결과를 알 수 없어요:\n{out.strip()[:800]}')
```

`HANDLERS`에 `'/rm': cmd_rm` 추가.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 5: 커밋**

```bash
git add src/lambda_function.py tests/test_lambda_function.py
git commit -m "[feat] 람다 v5 — /rm 워크트리 삭제 (미커밋 변경 시 거부)"
```

---

### Task 6: 권한·보안 템플릿 (settings, secrets.env, IAM)

**Files:**
- Create: `ec2/claude-settings.json`
- Create: `ec2/secrets.env.example`
- Create: `iam/gamer4-operator-policy.json`

**Interfaces:**
- Consumes: Task 2의 `secrets.env` 키 이름 `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`
- Produces: Task 7 문서가 세 파일의 경로와 적용 명령을 안내한다

- [ ] **Step 1: settings 템플릿 작성**

`ec2/claude-settings.json` (EC2의 `~/.claude/settings.json`으로 복사한다. 프로젝트 `.claude/settings.json`에 넣으면 `defaultMode`가 무시되므로 반드시 user settings):

```json
{
  "permissions": {
    "defaultMode": "default",
    "allow": [
      "Read", "Glob", "Grep",
      "Bash(git status:*)", "Bash(git log:*)", "Bash(git diff:*)", "Bash(git branch:*)",
      "Bash(npx vercel ls:*)", "Bash(npx vercel logs:*)", "Bash(npx vercel metrics:*)", "Bash(npx vercel inspect:*)",
      "Bash(sam logs:*)",
      "Bash(aws logs:*)", "Bash(aws cloudformation describe-stacks:*)", "Bash(aws cloudformation describe-stack-events:*)",
      "Bash(curl -s:*)",
      "Bash(yarn lint:*)", "Bash(yarn verify:*)", "Bash(yarn test:*)"
    ],
    "ask": [
      "Bash(sam deploy:*)",
      "Bash(yarn deploy:lambda:*)",
      "Bash(npx vercel promote:*)", "Bash(npx vercel redeploy:*)", "Bash(npx vercel env:*)",
      "Bash(npx vercel firewall:*)", "Bash(npx vercel api:*)",
      "Bash(gh pr merge:*)",
      "Bash(python3:*sb_sql.py*)",
      "Bash(aws lambda update-function-code:*)", "Bash(aws lambda update-function-configuration:*)",
      "Bash(aws ec2 stop-instances:*)"
    ],
    "deny": [
      "Bash(aws iam:*)",
      "Bash(aws ec2 terminate-instances:*)",
      "Read(./.env)", "Read(./**/.env)",
      "Read(~/.config/gamer4/secrets.env)", "Read(~/.claude/.credentials.json)"
    ]
  },
  "remoteControlAtStartup": false,
  "dialogExpiry": 0
}
```

- [ ] **Step 2: secrets.env 템플릿 작성**

`ec2/secrets.env.example`:

```
# Copy to /home/ubuntu/.config/gamer4/secrets.env and chmod 600.
# Loaded by claude-rc@.service (EnvironmentFile) and sourced by idle-watch.sh.
# systemd syntax: KEY=value, one per line, comments only on their own line.
# Never put CLAUDE_CODE_OAUTH_TOKEN here: it blocks Remote Control.

# Boot-manager bot token and your chat id. idle-watch.sh uses them for the
# "shutting down" notice. Same values as the Lambda's TELEGRAM_TOKEN /
# ALLOWED_CHAT_ID.
TELEGRAM_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=000000000

# Tool logins for the session. Alternatives: `gh auth login`, `npx vercel login`.
GH_TOKEN=github_pat_xxx
VERCEL_TOKEN=xxx
SUPABASE_ACCESS_TOKEN=sbp_xxx
```

- [ ] **Step 3: IAM 정책 템플릿 작성**

`iam/gamer4-operator-policy.json` (인스턴스 역할 `ec2-ssm-role`의 인라인 정책. 기존 `ec2-self-stop-policy.json`은 그대로 두고 별도로 추가한다):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudFormationRead",
      "Effect": "Allow",
      "Action": [
        "cloudformation:Describe*",
        "cloudformation:List*",
        "cloudformation:GetTemplate",
        "cloudformation:GetTemplateSummary"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudFormationDeploySyncStack",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateChangeSet",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:DeleteChangeSet"
      ],
      "Resource": "arn:aws:cloudformation:ap-northeast-2:AWS_ACCOUNT_ID:stack/gamer4-sync/*"
    },
    {
      "Sid": "LambdaSyncFunctions",
      "Effect": "Allow",
      "Action": [
        "lambda:Get*",
        "lambda:List*",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration"
      ],
      "Resource": "arn:aws:lambda:ap-northeast-2:AWS_ACCOUNT_ID:function:gamer4-sync-*"
    },
    {
      "Sid": "LogsReadOnly",
      "Effect": "Allow",
      "Action": ["logs:Describe*", "logs:Filter*", "logs:Get*"],
      "Resource": "*"
    },
    {
      "Sid": "SamArtifactBucket",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::aws-sam-cli-managed-default-*",
        "arn:aws:s3:::aws-sam-cli-managed-default-*/*"
      ]
    }
  ]
}
```

`iam:PassRole`은 의도적으로 뺀다. 스택의 IAM 역할을 바꾸는 배포는 EC2에서 실패하며, 그런 변경은 맥에서 한다 (문서에 명시).

- [ ] **Step 4: 검증**

```bash
python3 -m json.tool ec2/claude-settings.json >/dev/null && echo settings-ok
python3 -m json.tool iam/gamer4-operator-policy.json >/dev/null && echo iam-ok
grep -c 'CLAUDE_CODE_OAUTH_TOKEN=' ec2/secrets.env.example || true   # 0 이어야 함 (주석 언급만)
```
Expected: `settings-ok`, `iam-ok`, `0`

- [ ] **Step 5: 커밋**

```bash
git add ec2/claude-settings.json ec2/secrets.env.example iam/gamer4-operator-policy.json
git commit -m "[feat] Remote Control 권한 게이트 settings, secrets.env, 인스턴스 역할 정책 템플릿 추가"
```

---

### Task 7: 문서 — README, OPERATIONS, SETUP 체크리스트, EC2 CLAUDE.md 패치

**Files:**
- Create: `docs/SETUP.md`
- Modify: `README.md` (전체 교체)
- Modify: `docs/OPERATIONS.md` (일상 운영·인증·서비스 관리·유휴·배포·알려진 제약 절 교체; 사고 기록은 유지)
- Modify: `ec2-claude-md-patch.md` (전체 교체)

**Interfaces:**
- Consumes: Task 1~6의 파일 이름·명령·유닛 이름. 스펙 §3-4(세션 종료 의미), §3-6(사용자 1회 작업), §3-7(용량)
- Produces: 없음

- [ ] **Step 1: `docs/SETUP.md` 작성**

```markdown
# 최초 설정 (1회) — Remote Control 모드

EC2에 SSM 세션 셸로 들어가 `sudo -u ubuntu -i`로 수행한다. 브라우저 인증이 필요한
단계(1·2·3)는 봇이 대신할 수 없다. 순서대로 한다.

## 0. 용량 확인

```bash
df -h / ; free -m ; curl -s http://169.254.169.254/latest/meta-data/instance-type
```

24GB EBS면 워크트리(세션)는 `ops` 외 2개까지다. 세션 3개 이상을 쓰려면 t3.large +
EBS 40GB로 늘린다. 세션당 메모리는 유휴 121MB, 포화 518MB(맥 실측).

## 1. 인증 — claude.ai 로그인으로 교체

`claude setup-token`/`CLAUDE_CODE_OAUTH_TOKEN`으로는 Remote Control 세션을 만들 수
없다. `.env`에 토큰이 남아 있으면 "API-key auth takes precedence"로 거부된다.

```bash
grep -v CLAUDE_CODE_OAUTH_TOKEN ~/.claude/.env > /tmp/e && mv /tmp/e ~/.claude/.env
env | grep -E 'ANTHROPIC_(API_KEY|AUTH_TOKEN|BASE_URL)|DISABLE_TELEMETRY|DO_NOT_TRACK|DISABLE_GROWTHBOOK|CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'
#  ↑ 하나라도 잡히면 제거 (Remote Control 자격 조건). ~/.claude/settings.json 의 env 블록도 확인
claude auth login      # claude.ai 선택
claude doctor          # "Remote Control" 항목 확인. 키 입력으로 종료
```

## 2. 도구 로그인

```bash
gh auth login                                   # 또는 secrets.env 의 GH_TOKEN
npx vercel login
cd ~/gamer4info && npx vercel link --scope byseops-projects   # 또는 VERCEL_TOKEN
```

## 3. secrets.env

```bash
umask 077; mkdir -p ~/.config/gamer4
cp /tmp/secrets.env.example ~/.config/gamer4/secrets.env   # 리포 ec2/secrets.env.example
vi ~/.config/gamer4/secrets.env     # TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SUPABASE_ACCESS_TOKEN(맥 키체인 값) 등
chmod 600 ~/.config/gamer4/secrets.env
```

## 4. 리포 비밀 파일

`~/gamer4info/.env`(REVALIDATE_SECRET 포함)와 `~/gamer4info/infra/samconfig.toml`을
붙여넣는다. 둘 다 git에 없다.

## 5. Claude Code 설정

```bash
cp /tmp/claude-settings.json ~/.claude/settings.json     # 리포 ec2/claude-settings.json
cat /tmp/ec2-claude-md-patch.md >> ~/.claude/CLAUDE.md   # 리포 ec2-claude-md-patch.md
```

user settings에 넣어야 `defaultMode`가 먹는다. 프로젝트 `.claude/settings.json`의
`auto`/`bypassPermissions`는 무시된다.

## 6. 인스턴스 역할 IAM 정책 (맥에서)

`iam/gamer4-operator-policy.json`의 `AWS_ACCOUNT_ID`를 채운 뒤:

```bash
aws iam put-role-policy --role-name ec2-ssm-role \
  --policy-name gamer4-operator --policy-document file://iam/gamer4-operator-policy.json
```

AWS 액세스 키는 EC2에 복사하지 않는다. `iam:PassRole`이 없으므로 스택의 IAM 역할을
바꾸는 `sam deploy`는 EC2에서 실패한다 — 그런 변경은 맥에서 한다.

보안 그룹에서 SSH 22 인바운드를 제거한다 (SSM만 사용).

## 7. EC2 파일 설치 (root)

리포 `ec2/*`를 `/tmp`로 복사한 뒤:

```bash
sudo bash /tmp/install.sh
```

`claude-rc@ops`를 enable만 하고 start하지 않는다. 첫 기동은 다음 단계에서 대화형으로 한다.

## 8. Remote Control 1회 수락 (대화형, ubuntu)

```bash
cd ~/gamer4info
claude                      # 워크스페이스 신뢰 수락 → /exit
claude remote-control --name gamer4-ops --spawn same-dir --capacity 2 --permission-mode default --no-chrome
#   "Enable Remote Control? (y/n)" → y
#   QR을 폰 Claude 앱으로 스캔 → 대화 확인 → Ctrl+C
```

폰 앱 `/config`에서 **Push when Claude decides**, **Push when actions required**를 켠다.

## 9. 상시화

```bash
sudo systemctl enable --now claude-rc@ops
systemctl status claude-rc@ops
```

텔레그램에서 `/status` → `🟢 claude-rc@ops active`, `✅ 인증 OK`.

## 10. 스모크 테스트

| 확인 | 기대 |
|---|---|
| `/stop` → `/start` | 90초 내 앱에 `gamer4-ops` 온라인 |
| `/new t1` → `/sessions` | `gamer4-t1` 🟢, 앱에서 대화 가능 |
| `/kill t1` | 앱에서 오프라인, `/sessions`에 ⚪ |
| `/rm t1` | 워크트리 삭제 (변경 있으면 거부) |
| 폰에서 "sam deploy 해줘" | 폰에 승인 프롬프트, 거부하면 실행 안 됨. **"항상 허용" 누르지 않는다** |
| `IDLE_MINUTES=5 ~/idle-watch.sh` 를 6분 간격 2회 | 두 번째에 텔레그램 알림 후 정지 |
```

- [ ] **Step 2: `README.md` 교체**

```markdown
# pocket-claude

Run Claude Code Remote Control sessions on an EC2 box, talk to them from the
Claude mobile app, and use a tiny Telegram bot as the power switch.

## Why

The instance is off most of the time. Something outside it has to turn it
on, and that something is a Lambda behind API Gateway. Everything else —
the conversation, permission prompts, diffs — goes through Anthropic's
Remote Control channel to the phone app, so the bot never relays chat.

## Architecture

```
[Telegram boot bot]  /start /stop /status /sessions /new <name> /kill <name> /rm <name>
   └─ API GW ─▶ Lambda ec2-boot-manager (v5)
                 ├─ ec2:StartInstances / StopInstances
                 └─ ssm:SendCommand   (systemctl start/stop claude-rc@*, read-only queries)

[EC2 (ubuntu)]
   systemd claude-rc@.service (template)
     ├─ claude-rc@ops   ~/gamer4info          enabled, starts at boot
     └─ claude-rc@<x>   ~/worktrees/<x>       created by /new, not enabled
   each unit: claude-rc-wrap.sh ─▶ tmux rc-<name> ─▶ claude remote-control --name gamer4-<name>
   cron (5 min): idle-watch.sh ─▶ Telegram notice, then stop after 60 idle minutes

[Claude mobile app] ── Anthropic ── EC2 claude process   (no inbound ports)
```

The Lambda never launches Claude. It flips systemd units; systemd owns the
process lifetime. An earlier version launched the session remotely over
SSM and every failure mode traced back to that decision (post-mortem in
`docs/OPERATIONS.md`).

## Commands

| Command | What it does |
|---|---|
| `/start` | Start the instance. `gamer4-ops` appears in the app about a minute later |
| `/stop` | Stop the instance. Every session goes offline |
| `/status` | Instance state, `claude-rc@*` units, auth health |
| `/sessions` | One line per session: active, branch, last conversation |
| `/new <name>` | `git worktree add ~/worktrees/<name>` + `systemctl start claude-rc@<name>` |
| `/kill <name>` | Stop the unit. Worktree and branch stay |
| `/rm <name>` | Stop, then remove the worktree. Refused if it has uncommitted changes |

`<name>` is `[a-z0-9-]{1,24}`; `ops` is reserved. Nothing blocks for long:
API Gateway allows 30 s and `/new` only waits for `systemctl start`.

## What "ending a session" means

There is no end. A Remote Control session is online while its process
lives and offline a few seconds after it dies. `/kill` stops one unit;
`/stop` or the idle watcher stops the box and takes every session with it.
On the next boot only `ops` comes back. If the box restarts within about
four hours the server session resumes; later than that it is a new
session, and continuity lives in the repo's docs (OPERATIONS), not in the
chat. Commit, push and update OPERATIONS before closing a session.

## Idle detection

The screen hash of v4 is gone: a Remote Control session can be busy while
the terminal never changes. Activity is any of a written transcript under
`~/.claude/projects`, a fresh non-terminal `claude --bg` job, or CPU burned
by a `claude` process since the last sample. Sixty minutes without any of
them stops the instance after a Telegram notice.

## Permissions

`ec2/claude-settings.json` is the user-level `~/.claude/settings.json`:
`default` mode, read-only tools allowed, production operations in `ask`
(they prompt on the phone in every mode, including `auto`), `aws iam` and
`.env` reads denied. Never press "always allow" on a production prompt: it
writes an `allow` rule and the gate is gone.

## Setup

`docs/SETUP.md` (Korean) is the one-time checklist: claude.ai login on the
instance, tool logins, `secrets.env`, settings, the instance-role policy in
`iam/`, `ec2/install.sh`, and the interactive first start.

Lambda: copy `.env.example` to `.env`, set the same values on the
`ec2-boot-manager` function, point the bot webhook at API Gateway. The
execution role needs `ec2:Start/Stop/DescribeInstances`, `ssm:SendCommand`,
`ssm:GetCommandInvocation`, `ssm:DescribeInstanceInformation`.

## Layout

```
src/lambda_function.py        Lambda handler (v5)
tests/                        unittest for the Lambda helpers, bash tests for the EC2 scripts
ec2/claude-rc@.service        systemd template unit
ec2/claude-rc-wrap.sh         tmux wrapper systemd tracks
ec2/claude-rc.sudoers         ubuntu may start/stop claude-rc@* only
ec2/idle-watch.sh             idle watcher
ec2/install.sh                installer (idempotent)
ec2/claude-settings.json      ~/.claude/settings.json template
ec2/secrets.env.example       ~/.config/gamer4/secrets.env template
ec2-claude-md-patch.md        rules to append to the instance's ~/.claude/CLAUDE.md
iam/                          instance-role policy templates
docs/SETUP.md                 one-time setup checklist
docs/OPERATIONS.md            runbook: diagnosis, deployment, incident record
docs/superpowers/             design and implementation-plan docs
```

## Tests

```bash
python3 -m unittest discover -s tests
bash tests/test_idle_watch.sh
bash tests/test_claude_rc_wrap.sh
```

## License

MIT
```

- [ ] **Step 3: `docs/OPERATIONS.md` 갱신**

머리말의 `Lambda: ec2-boot-manager (v4)` → `(v5)`. 설계 문서 링크 줄에 Remote Control 설계 문서를 추가한다:

```markdown
v5(Remote Control 모드)의 설계는 [Remote Control 설계 문서](superpowers/specs/2026-09-22-remote-control-mode-design.md),
구현 절차는 [v5 구현 계획](superpowers/plans/2026-09-22-remote-control-mode.md)을 참조한다.
```

"## 일상 운영" 절 전체를 교체:

```markdown
## 일상 운영

대화는 **Claude 모바일 앱 Code 탭**에서 한다. 텔레그램 부트매니저 봇은 전원과 세션
유닛만 다룬다.

| 명령 | 하는 일 | 응답 |
|---|---|---|
| `/start` | 인스턴스 시작. 부팅되면 systemd가 `claude-rc@ops`를 띄운다 | 1초 미만 |
| `/stop` | 인스턴스 정지. 모든 세션 오프라인 | 1초 미만 |
| `/status` | 인스턴스 상태 + `claude-rc@*` 유닛 + 인증 상태 | 2~3초 |
| `/sessions` | 세션별 active 여부·브랜치·마지막 대화 시각 | 2~3초 |
| `/new <name>` | `~/worktrees/<name>` 워크트리 생성(`origin/main`에서 `<name>` 브랜치) + `.env` 복사 + 유닛 시작 | ~5초 |
| `/kill <name>` | 유닛 정지. 워크트리·브랜치 유지. `ops`는 거부 | 2~3초 |
| `/rm <name>` | 정지 후 `git worktree remove`. **미커밋 변경이 있으면 거부** | 2~3초 |

`<name>`은 `[a-z0-9-]{1,24}`, `ops`는 예약. SSM 스크립트는 `sudo -u ubuntu`로 돌고
sudoers는 `systemctl start|stop claude-rc@*`만 허용한다.

### 세션 종료의 의미

Remote Control 세션에 "종료"는 없다. 프로세스가 살아 있으면 온라인, 죽으면 몇 초 뒤
앱에서 오프라인이다.

- `/kill <name>`: 유닛 정지. **닫기 전에 커밋·푸시하고 OPERATIONS를 갱신한다** (EC2 CLAUDE.md 규칙).
- `/stop` 또는 유휴 정지: 전 세션 오프라인. 다음 부팅엔 `ops`만 자동 기동. 약 4시간
  이내 재기동이면 서버 세션이 복귀하고, 그 뒤엔 새 세션이다. 연속성은 리포 문서로만
  담보한다.
- 폰 프롬프트에서 **"항상 허용"을 누르지 않는다.** settings의 `allow`에 영구 기록되어
  승인 게이트가 사라진다. `~/.claude/settings.json`의 `permissions.allow`를 주기적으로
  감사한다.

인스턴스는 60분 유휴 시 스스로 정지하며 정지 직전 텔레그램으로 알린다.
```

"## 인증 문제 진단" 절의 도입부와 1단계·2단계를 교체하고, 함정 A·B와 "토큰 재발급"은 아래처럼 다듬는다:

```markdown
## 인증 문제 진단

v5는 **claude.ai OAuth(`claude auth login` → `~/.claude/.credentials.json`)만** 쓴다.
`claude setup-token`이 발급하는 `CLAUDE_CODE_OAUTH_TOKEN`은 모델 요청 전용이라
Remote Control 세션을 만들 수 없고, `~/.claude/.env`에 남아 있으면 "API-key auth takes
precedence"로 서버 기동 자체가 거부된다. `/status`가 이 두 경우를 구분해 보여준다.

| `/status` 표시 | 원인 | 조치 |
|---|---|---|
| `.env 에 CLAUDE_CODE_OAUTH_TOKEN 이 남아 있어요` | v4 토큰 잔존 | `.env`에서 줄 제거 후 `sudo systemctl restart claude-rc@ops` |
| `claude.ai 로그인이 없어요` | `.credentials.json` 없음 | `claude auth login` (대화형, SSM 셸) |
| `401 ...` 화면 스캔 | 리프레시 토큰 만료 | `claude auth login` 후 재시작 |

### 1단계 — 로그인 상태

```bash
sudo -u ubuntu bash -c 'export HOME=/home/ubuntu PATH=/home/ubuntu/.local/bin:$PATH; claude -p "Reply with exactly: PONG"'
grep -c CLAUDE_CODE_OAUTH_TOKEN /home/ubuntu/.claude/.env || true   # 0 이어야 한다
```

`PONG`이 나오고 grep이 0이면 인증은 정상이다. 2단계로 간다.

### 2단계 — 서버 세션은 살아 있는가

```bash
systemctl status claude-rc@ops
sudo -u ubuntu tmux capture-pane -t rc-ops -p | tail -20
journalctl -u claude-rc@ops -n 30
```

화면에 `Enable Remote Control?`이 떠 있으면 1회 수락이 안 된 것이다 — `docs/SETUP.md` 8단계.
```

함정 A 절의 첫 문단 앞에 한 줄 추가: `v5에서는 .env에 토큰이 없으므로 이 가드는 발동하지 않는다. 토큰을 다시 넣으면 그때 살아난다. 코드는 claude-rc-wrap.sh에 남겨 두었다.` 함정 A·B·토큰 재발급 절의 `claude-telegram` → `claude-rc@ops`, `claude-supervise.sh` → `claude-rc-wrap.sh`, `claude-supervise` 로그 태그 → `claude-rc-wrap`으로 바꾼다. "토큰 재발급" 절 제목을 "재로그인"으로 바꾸고 본문을 `claude auth login` → `sudo systemctl restart claude-rc@ops`로 교체한다.

"## 서비스 관리" 절을 교체:

```markdown
## 서비스 관리

```bash
systemctl list-units 'claude-rc@*' --all
systemctl restart claude-rc@ops
journalctl -u claude-rc@ops -n 50
sudo -u ubuntu tmux ls                          # rc-<name> 세션들
grep claude-rc-wrap /var/log/syslog | tail      # 가드 동작 기록
grep idle-watch /var/log/syslog | tail          # 유휴 감시 기록
```

`claude-rc@.service`는 템플릿이다. `claude-rc@ops`만 enable되어 부팅 시 뜨고, 나머지는
`/new`가 start만 한다. 각 인스턴스는 `claude-rc-wrap.sh <name>`이 tmux 세션
`rc-<name>`을 만들고 블록한다. 세션이 죽으면 래퍼가 1로 종료하고 `Restart=on-failure`가
10초 뒤 되살린다. `ExecStop`은 tmux 세션을 죽인다.

`ExecStart`에서 `tmux new-session -d`를 직접 부르면 안 된다. tmux 서버가 스스로
데몬화해 systemd가 PID를 놓치고 재시작 루프가 돈다. 래퍼는 이걸 피하려고 있다.

ubuntu 사용자(ops 세션 포함)는 sudoers(`/etc/sudoers.d/claude-rc`)로
`systemctl start|stop claude-rc@*`만 실행할 수 있다.
```

"## 유휴 자동 종료" 절을 교체:

```markdown
## 유휴 자동 종료

5분마다 cron이 `idle-watch.sh`를 돌린다. Remote Control 세션은 폰에서 대화 중이어도
터미널 화면이 안 바뀌므로 v4의 화면 해시는 버렸다. 활동 신호는 셋이다.

1. `~/.claude/projects/*/*.jsonl` 대화 트랜스크립트의 최신 mtime
2. `~/.claude/jobs/*/state.json`이 비종료 상태이고 30분 이내 갱신됨 (`blocked`는 종료 상태로 본다)
3. `claude` 프로세스 누적 CPU가 직전 샘플과 다름

`last_active = max(직전 값, ①, ②·③이면 지금)`이고 `IDLE_MINUTES`(60) 이상 조용하면
텔레그램 알림 후 `aws ec2 stop-instances`. 알림은 `secrets.env`의 `TELEGRAM_TOKEN`·
`TELEGRAM_CHAT_ID`(부트매니저 봇)로 보낸다.

```bash
IDLE_MINUTES=5 /home/ubuntu/idle-watch.sh      # 검증용 단축
DRY_RUN=1 /home/ubuntu/idle-watch.sh           # 판정만 출력, 정지 안 함
```

매 실행 `idle=<분>/<한계> cpu=<초> last=<epoch>` 한 줄을 출력한다. 상태 파일
`/home/ubuntu/.claude-idle-state`는 `<cpu초> <last_active>` 한 줄이고 부팅 시 래퍼가
지운다 (없으면 그 시점부터 유예).

로컬 테스트: `bash tests/test_idle_watch.sh` (가짜 `~/.claude` 트리, AWS 불필요).
```

"### EC2 스크립트" 절의 첫 문단을 `ec2/` 아래 파일(`claude-rc@.service`, `claude-rc-wrap.sh`, `claude-rc.sudoers`, `idle-watch.sh`, `install.sh`)을 `/tmp`로 보내고 `sudo bash /tmp/install.sh`로 바꾼다. "### Lambda" 절의 테스트 명령 뒤에 셸 테스트 두 줄을 추가한다. "### 롤백" 절에 한 줄 추가: `v4로 되돌리려면 backup/lambda_function.v4.py(이 커밋 이전의 src/lambda_function.py)를 같은 방법으로 올린다. EC2는 claude-telegram.service를 다시 설치해야 한다.` — 그리고 실제로 `git show b8ff012:src/lambda_function.py > backup/lambda_function.v4.py`로 v4 스냅샷을 `backup/`에 넣는다 (`b8ff012`는 v4 마지막 main 커밋. v4에는 하드코딩 ID가 없으므로 커밋 가능).

"## 알려진 제약" 절을 교체:

```markdown
## 알려진 제약

- **대화형 1회 작업이 있다** — `claude auth login`, 워크스페이스 신뢰, "Enable Remote
  Control?" 수락은 SSM 셸에서 사람이 해야 한다. 봇이 대신 못 한다
- **`claude doctor`는 TTY 없이 실행하면 블록된다** — SSM에서 호출하지 않는다.
  `/status`는 `.env` 토큰 잔존·`.credentials.json` 존재·화면 401 스캔으로 대신한다
- **세션 수 = 디스크·메모리** — 24GB EBS면 워크트리 2개까지. `--capacity 2`
- **`/new`는 `yarn install`을 하지 않는다** — 세션에 들어가 필요할 때 시킨다
- **sudoers 와일드카드** — `claude-rc@*`는 공백까지 매칭하므로 `systemctl start claude-rc@x --foo`도
  통과한다. start/stop 외 동작은 못 하므로 허용한다
- **로컬에 session-manager-plugin이 없으면** `aws ssm start-session`을 못 쓴다.
  모든 인스턴스 작업을 `aws ssm send-command`로 해야 한다
- **`ssm_run`의 25초 상한** — SSM이 느려지면 `/status`가 30초 한계에 근접할 수 있다.
  Duration이 20,000ms를 넘기 시작하면 상한을 낮추고 부분 결과를 반환하도록 바꾼다
```

- [ ] **Step 4: `ec2-claude-md-patch.md` 교체**

```markdown

## Remote Control 운영 규칙 (EC2)

이 세션은 EC2 위 `claude remote-control` 서버 세션이며 사용자는 Claude 모바일 앱에서
대화한다. 트랜스크립트는 Anthropic 서버에 저장되므로 **`.env`·`secrets.env`·
`.credentials.json`의 값을 출력하지 않는다.**

### 세션을 닫기 전에

`/kill`·`/stop`·유휴 정지 어느 경우든 대화 맥락은 이 세션과 함께 사라질 수 있다.
작업을 끝내거나 사용자가 "그만"이라고 하면 **반드시**:

1. 커밋·푸시 (미커밋 변경이 있으면 `/rm`이 거부된다)
2. `docs/OPERATIONS.md` 백로그·상태 갱신 후 커밋·푸시

### 승인 게이트

`~/.claude/settings.json`의 `permissions.ask`에 있는 명령(sam deploy, vercel promote/
redeploy/env/firewall/api, gh pr merge, sb_sql.py, aws lambda update-*)은 폰으로 승인
프롬프트가 간다. 사용자가 거부하면 실행하지 않고 대안을 제시한다. 사용자에게 "항상
허용"을 권하지 않는다.

### 시스템 명령은 부트매니저 봇이 담당

EC2 on/off, 세션 유닛 생성·정지(`/new` `/kill` `/rm`), 인증 갱신은 텔레그램 부트매니저
봇의 영역이다. 요청받으면 그쪽으로 안내한다. 인스턴스를 끄는 것만은 이 세션에서도
가능하다: `aws ec2 stop-instances --instance-ids $(curl -s http://169.254.169.254/latest/meta-data/instance-id)` (ask 규칙에 걸려 승인이 필요하다).

### 자원

세션은 `ops` 외 최대 2개(디스크 24GB). 워크트리에서 `yarn install`은 필요할 때만.
```

- [ ] **Step 5: 검증**

```bash
python3 -m unittest discover -s tests 2>&1 | tail -2
bash tests/test_idle_watch.sh | tail -1
bash tests/test_claude_rc_wrap.sh | tail -1
grep -rn 'claude-telegram\|start-claude-telegram\|claude-supervise' README.md docs/SETUP.md src iam ec2-claude-md-patch.md ec2/claude-rc@.service ec2/claude-rc-wrap.sh ec2/idle-watch.sh || echo "no stale references"
grep -n 'claude-telegram\|claude-supervise' docs/OPERATIONS.md
```
Expected: `OK`, `all passed` ×2, `no stale references`. (`ec2/install.sh`는 구 유닛을 폐기하는 로직이라 이름이 남는 게 맞다.) OPERATIONS의 남은 언급은 사고 기록·롤백 절(역사)과 "v4 토큰 잔존" 설명뿐이어야 한다.

- [ ] **Step 6: 커밋**

```bash
git add README.md docs/SETUP.md docs/OPERATIONS.md ec2-claude-md-patch.md backup/lambda_function.v4.py
git commit -m "[docs] Remote Control 모드 설정 체크리스트와 운영 런북 갱신"
```

---

## 범위 밖 (이 계획에서 하지 않음)

- `sb_sql.py`를 gamer4info `scripts/ops/`로 옮기고 `SUPABASE_ACCESS_TOKEN`·`CERT_NONE` 제거 — gamer4info 저장소 작업.
- 실제 EC2 배포·스모크 테스트(스펙 §5 EC2 항목) — 사용자 1회 작업(SETUP.md)이 선행돼야 하므로 계획 완료 후 별도 세션.
- Lambda 배포(`aws lambda update-function-code`) — 코드 머지 후 사용자 승인 하에 수행.
- 텔레그램 채널 플러그인 폴백 유닛 — 스펙 §4에서 설계 유지 안 함.
