#!/bin/bash
# Runs the tests (see tests/README.md): made-up data, the server and the
# browser tests in containers, on a fake clock in spring 2027.
#   phase 1  23:40 local on 2027-03-29: the page against the model
#   phase 2  just after midnight: the day rollover
#   phase 3  02:30, after a restart of the server: the cache
# Results and the containers' logs end up in tests/run/results/.
set -euo pipefail
cd "$(dirname "$0")"

DC=(docker compose -p housemon-lite-tests -f compose.yaml --profile tests)
clock() { python3 fake/clock.py run/clock "$@"; }
step() {
  echo
  if [ -f run/clock/faketime ]; then echo "=== $* (fake clock $(clock now))"; else echo "=== $*"; fi
}
status=0
phase() { "${DC[@]}" run --rm -T browser pytest -q -p no:cacheprovider -o console_output_style=classic "$@" || status=1; }

"${DC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
rm -rf run && mkdir -p run/clock run/results
start=$(cd fake && python3 -c 'import world; print(world.CLOCK_START.isoformat())')

step "building the images"
"${DC[@]}" build -q

step "writing the history"
clock start "$start" >/dev/null
"${DC[@]}" up -d --wait feeder
"${DC[@]}" up -d server

step "phase 1: the evening of 2027-03-29"
phase browser/test_1_before_midnight.py

clock to 2027-03-29T21:59:40Z >/dev/null
sleep 25
step "phase 2: after midnight"
phase browser/test_2_midnight.py

clock to 2027-03-30T00:30:00Z >/dev/null
sleep 15
step "phase 3: a restart at 02:30"
"${DC[@]}" run --rm -T browser python3 browser/snapshot.py
"${DC[@]}" restart server >/dev/null
date +%s.%N > run/results/restarted
phase browser/test_3_restart.py

"${DC[@]}" logs --no-color server > run/results/server.log 2>&1
"${DC[@]}" logs --no-color feeder > run/results/feeder.log 2>&1
"${DC[@]}" down -v >/dev/null 2>&1
echo
[ "$status" = 0 ] && echo "=== all phases passed" || echo "=== FAILED (see above and tests/run/results/)"
exit "$status"
