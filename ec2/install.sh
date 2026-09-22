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
