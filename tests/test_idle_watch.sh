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
  # Fake curl: log all arguments, and print i-fake for IMDS queries.
  printf '#!/bin/sh\necho "$@" >> "%s/curl.log"\ncase "$@" in *169.254.169.254*) echo "i-fake" ;; esac\n' "$TMP" > "$TMP/bin/curl"
  chmod +x "$TMP/bin/curl"
  # Fake aws: log all arguments.
  printf '#!/bin/sh\necho "$@" >> "%s/aws.log"\n' "$TMP" > "$TMP/bin/aws"
  chmod +x "$TMP/bin/aws"
}

run() {   # usage: run [IDLE_MINUTES]
  PATH="$TMP/bin:$PATH" STATE="$TMP/state" PROJECTS_DIR="$TMP/projects" \
  JOBS_DIR="$TMP/jobs" SECRETS_FILE="$TMP/no-telegram-env" DRY_RUN=1 \
  BOOT_EPOCH="${BOOT_EPOCH:-0}" \
  IDLE_MINUTES="${1:-60}" bash "$SCRIPT"
}

run_real() {   # usage: run_real [IDLE_MINUTES]
  PATH="$TMP/bin:$PATH" STATE="$TMP/state" PROJECTS_DIR="$TMP/projects" \
  JOBS_DIR="$TMP/jobs" SECRETS_FILE="$TMP/telegram.env" DRY_RUN=0 \
  BOOT_EPOCH="${BOOT_EPOCH:-0}" \
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

# 12. Real stop path with telegram.env present: notification is sent, instance is stopped.
setup
echo "0 $(ago 7200)" > "$TMP/state"
printf 'TELEGRAM_TOKEN=tok123\nTELEGRAM_CHAT_ID=42\n' > "$TMP/telegram.env"
OUT=$(run_real)
assert_eq "$(stopped "$OUT")" yes "real stop with telegram.env returns stop"
grep -qs "bottok123/sendMessage" "$TMP/curl.log" && echo "ok   telegram notification sent" \
  || { echo "FAIL telegram notification not sent"; FAILS=$((FAILS + 1)); }
grep -qs "chat_id=42" "$TMP/curl.log" && echo "ok   chat id passed to telegram" \
  || { echo "FAIL chat id not in curl log"; FAILS=$((FAILS + 1)); }
grep -qs "ec2 stop-instances" "$TMP/aws.log" && echo "ok   aws stop-instances called" \
  || { echo "FAIL aws.log missing stop-instances"; FAILS=$((FAILS + 1)); }
! grep -qs "tok123" <<< "$OUT" && echo "ok   token not leaked to stdout" \
  || { echo "FAIL token appeared in stdout"; FAILS=$((FAILS + 1)); }

# 13. Real stop path with no telegram.env: instance stops without notification.
setup
echo "0 $(ago 7200)" > "$TMP/state"
OUT=$(run_real)
assert_eq "$(stopped "$OUT")" yes "real stop without telegram.env returns stop"
! grep -qs "sendMessage" "$TMP/curl.log" && echo "ok   no telegram notification without telegram.env" \
  || { echo "FAIL telegram notification sent despite no telegram.env"; FAILS=$((FAILS + 1)); }
grep -qs "ec2 stop-instances" "$TMP/aws.log" && echo "ok   aws still called without telegram.env" \
  || { echo "FAIL aws.log missing despite no telegram.env"; FAILS=$((FAILS + 1)); }

# 14. Real path while active: no stop action taken.
setup
echo "0 $(ago 600)" > "$TMP/state"
OUT=$(run_real)
assert_eq "$(stopped "$OUT")" no "active instance does not stop via real path"
! grep -qs "stop-instances" "$TMP/aws.log" && echo "ok   aws not called when active" \
  || { echo "FAIL aws called when instance was active"; FAILS=$((FAILS + 1)); }
! grep -qs "sendMessage" "$TMP/curl.log" && echo "ok   no notification when active" \
  || { echo "FAIL telegram notification sent when active"; FAILS=$((FAILS + 1)); }

# 15. A state file written before this boot belongs to the previous run of the
# instance: honouring it would stop the box seconds after it came up.
setup
echo "0 $(ago 7200)" > "$TMP/state"
touch_ago "$TMP/state" 7200
OUT=$(BOOT_EPOCH=$(ago 60) run)
assert_eq "$(stopped "$OUT")" no "pre-boot state file is ignored"

# 16. A garbled state file falls back to the defaults, without shell errors.
setup
echo "garbage here" > "$TMP/state"
OUT=$(run 2>&1)
assert_eq "$(stopped "$OUT")" no "garbled state file does not stop the instance"
case "$OUT" in
  *"integer expression"*)
    echo "FAIL garbled state file produced shell errors"; echo "     got: $OUT"
    FAILS=$((FAILS + 1)) ;;
  *) echo "ok   garbled state file produces no shell errors" ;;
esac

[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }
