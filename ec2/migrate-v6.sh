#!/bin/bash
# One-time v5 -> v6 migration: single hard-wired repo -> project-agnostic
# `pocket` layout. Run as root on the box, after ec2/install.sh.
# Idempotent: safe to re-run. Never moves a repository, only links it.
#
# Usage: migrate-v6.sh <name>=<path> [<name>=<path> ...]
#   e.g. migrate-v6.sh myapp=/home/ubuntu/myapp other=/home/ubuntu/workspace/other
#
# See docs/superpowers/specs/2026-09-23-generalize-multi-project-design.md
# §5-1 and §6 for why repos are linked under ~/work instead of moved: trust
# (~/.claude.json), transcripts (~/.claude/projects/<path>) and session
# resume are all keyed on the real path.
set -eu

HOME_DIR=/home/ubuntu
WORK=$HOME_DIR/work
CONFIG_OLD=$HOME_DIR/.config/gamer4
CONFIG_NEW=$HOME_DIR/.config/pocket-claude
POCKET=$HOME_DIR/bin/pocket
NAME_RE='^[a-z0-9][a-z0-9-]{0,23}$'

echo "== 1. ~/work =="
install -d -o ubuntu -g ubuntu -m 0755 "$WORK"
echo "ok: $WORK"

echo "== 2. project links =="
names=()
for pair in "$@"; do
  name=${pair%%=*}
  path=${pair#*=}
  if [ "$name" = "$pair" ] || [ -z "$path" ]; then
    echo "skip: '$pair' is not <name>=<path>" >&2
    continue
  fi
  if ! [[ "$name" =~ $NAME_RE ]]; then
    echo "skip: invalid name '$name' (must match $NAME_RE)" >&2
    continue
  fi
  if [ ! -d "$path" ]; then
    echo "skip: $path does not exist" >&2
    continue
  fi
  link="$WORK/$name"
  if [ -e "$link" ] || [ -L "$link" ]; then
    current=$(readlink -f "$link" 2>/dev/null || true)
    target=$(readlink -f "$path" 2>/dev/null || true)
    if [ "$current" = "$target" ]; then
      echo "ok (already linked): $name -> $path"
    else
      echo "skip: $link already exists (-> ${current:-?}), not overwriting" >&2
      continue
    fi
  else
    ln -s "$path" "$link"
    chown -h ubuntu:ubuntu "$link"
    echo "linked: $name -> $path"
  fi
  names+=("$name")
done

echo "== 3. ~/.config/gamer4 -> ~/.config/pocket-claude =="
if [ -d "$CONFIG_OLD" ]; then
  install -d -o ubuntu -g ubuntu -m 0700 "$CONFIG_NEW"
  for f in secrets.env telegram.env; do
    if [ -f "$CONFIG_OLD/$f" ]; then
      if [ -e "$CONFIG_NEW/$f" ]; then
        echo "skip: $CONFIG_NEW/$f already exists, not overwriting" >&2
      else
        mv "$CONFIG_OLD/$f" "$CONFIG_NEW/$f"
        chown ubuntu:ubuntu "$CONFIG_NEW/$f"
        chmod 600 "$CONFIG_NEW/$f"      # keep the pre-move permissions (0600)
        echo "moved: $f"
      fi
    fi
  done
  rmdir "$CONFIG_OLD" 2>/dev/null && echo "removed empty $CONFIG_OLD" \
    || echo "note: $CONFIG_OLD left in place (not empty)" >&2
else
  echo "none (already migrated, or a fresh install)"
fi

echo "== 4. retire claude-rc@ops =="
if systemctl is-enabled claude-rc@ops >/dev/null 2>&1; then
  systemctl disable --now claude-rc@ops
  echo "disabled: claude-rc@ops"
else
  echo "none (already retired, or never installed)"
fi

echo "== 5. cron idle-watch.sh =="
if sudo -u ubuntu crontab -l >/dev/null 2>&1; then
  sudo -u ubuntu crontab -l 2>/dev/null | grep -v 'idle-watch.sh' | sudo -u ubuntu crontab -
  echo "ok (entry removed if it was there)"
else
  echo "none (no crontab for ubuntu)"
fi

echo "== 6. trust =="
if [ "${#names[@]}" -eq 0 ]; then
  echo "no projects passed on the command line"
else
  echo "run these once per project (interactive, may fail under systemd -- retry from an SSM shell as ubuntu):"
  for name in "${names[@]}"; do
    echo "  pocket trust $name"
  done
fi

echo "== 7. pocket list =="
sudo -u ubuntu -H "$POCKET" list
