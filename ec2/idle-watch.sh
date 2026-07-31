#!/bin/bash
# Stops this instance after IDLE_LIMIT consecutive unchanged screen samples.
# Runs from the ubuntu crontab every 5 minutes, so 12 samples = 60 minutes.
set -u

SESSION=claude
STATE=/home/ubuntu/.claude-idle-state
IDLE_LIMIT=${IDLE_LIMIT:-12}
REGION=ap-northeast-2
CHANNEL_DIR=/home/ubuntu/.claude/channels/telegram

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
