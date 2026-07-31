#!/bin/bash
# Stops this instance after IDLE_LIMIT consecutive unchanged screen samples.
# Runs from the ubuntu crontab every 5 minutes, so 12 samples = 60 minutes.
set -u

SESSION=claude
STATE=/home/ubuntu/.claude-idle-state
IDLE_LIMIT=${IDLE_LIMIT:-12}
REGION=ap-northeast-2
CHANNEL_DIR=/home/ubuntu/.claude/channels/telegram
JOBS_DIR=/home/ubuntu/.claude/jobs
# A background job counts as working only if it is both non-terminal and
# recently touched. Without the freshness bound, a session stuck in a
# non-terminal state would pin the instance on forever.
BG_STALE_SECONDS=${BG_STALE_SECONDS:-1800}

tmux has-session -t "$SESSION" 2>/dev/null || exit 0

# The main pane goes static while `claude --bg` agents work, so screen hashing
# alone would shut the box down mid-task and lose that work.
bg_busy() {
  [ -d "$JOBS_DIR" ] || return 1
  python3 - "$JOBS_DIR" "$BG_STALE_SECONDS" <<'PY'
import json, os, sys, time

jobs_dir, stale = sys.argv[1], int(sys.argv[2])
# States that mean the job will not do further work on its own. "blocked"
# belongs here: it waits on a human, so it must not hold the instance up.
TERMINAL = {'done', 'failed', 'blocked', 'idle', 'cancelled', 'stopped',
            'completed', 'error'}
now = time.time()

for entry in os.scandir(jobs_dir):
    if not entry.is_dir():
        continue
    state_path = os.path.join(entry.path, 'state.json')
    try:
        state = json.load(open(state_path)).get('state')
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
        print(f'{entry.name} state={state}')
        sys.exit(0)          # busy
sys.exit(1)                  # nothing running
PY
}

if bg_busy; then
  # Reset rather than skip: the idle window should start over once the
  # background work finishes, not resume from a stale count.
  echo "pending 0" > "$STATE"
  logger -t idle-watch "background job active; idle counter reset"
  exit 0
fi

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
if [ -f "$CHANNEL_DIR/.env" ]; then
  TOK=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$CHANNEL_DIR/.env" | cut -d= -f2- | tr -d "\"' \r\n")
  CHAT=$(python3 -c "import json;print(json.load(open('$CHANNEL_DIR/access.json'))['allowFrom'][0])" 2>/dev/null || echo '')
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
