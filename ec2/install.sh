#!/bin/bash
# Installs the supervisor and systemd unit. Idempotent: safe to re-run.
set -eu

install -o ubuntu -g ubuntu -m 0755 /tmp/claude-supervise.sh /home/ubuntu/claude-supervise.sh
install -o root -g root -m 0644 /tmp/claude-telegram.service /etc/systemd/system/claude-telegram.service

systemctl daemon-reload
systemctl enable claude-telegram

install -o ubuntu -g ubuntu -m 0755 /tmp/idle-watch.sh /home/ubuntu/idle-watch.sh

# Register the cron entry idempotently: drop any previous line, then add ours.
# The `|| true` matters: with no existing crontab, grep receives zero lines and
# exits 1, and `set -e` would kill the subshell before the echo runs, piping an
# empty crontab in and wiping whatever was there.
CRON_LINE='*/5 * * * * /home/ubuntu/idle-watch.sh >/dev/null 2>&1'
{ sudo -u ubuntu crontab -l 2>/dev/null | grep -v 'idle-watch.sh' || true
  echo "$CRON_LINE"
} | sudo -u ubuntu crontab -

echo "installed"
