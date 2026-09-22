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
