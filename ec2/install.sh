#!/bin/bash
# Installs the Remote Control units, wrapper, pocket CLI, sudoers and idle
# watcher, and retires the v4 Telegram channel bot. Idempotent: safe to
# re-run. Run as root on the instance after copying ec2/* to /tmp.
set -eu

install -d -o ubuntu -g ubuntu -m 0755 /home/ubuntu/bin /home/ubuntu/work
install -d -o ubuntu -g ubuntu -m 0700 /home/ubuntu/.config/pocket-claude/projects
install -o ubuntu -g ubuntu -m 0755 /tmp/claude-rc-wrap.sh /home/ubuntu/bin/claude-rc-wrap.sh
install -o ubuntu -g ubuntu -m 0755 /tmp/pocket /home/ubuntu/bin/pocket
install -o root -g root -m 0644 /tmp/claude-rc@.service /etc/systemd/system/claude-rc@.service
install -o root -g root -m 0644 /tmp/claude-rc.slice /etc/systemd/system/claude-rc.slice

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
# Units are per project (claude-rc@<name>) and enabled individually by the
# migration script that creates each project, not here.

# Idle auto-stop was chosen to stay manual: drop any previous cron
# registration instead of installing one. Only rewrite the crontab when one
# already exists; otherwise this is a no-op, not a fresh empty crontab.
if sudo -u ubuntu crontab -l >/dev/null 2>&1; then
  sudo -u ubuntu crontab -l 2>/dev/null | grep -v 'idle-watch.sh' | sudo -u ubuntu crontab -
fi

echo "installed"
