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
