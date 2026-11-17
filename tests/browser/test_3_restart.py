"""Phase 3, 02:30 (local) on 2027-03-30, after a restart of the server.

Yesterday ended more than 2 hours ago, so its figures are final: a restart must
take every completed day from the cache, and the last days must be back quickly.
"""

import json
import time
from datetime import date

import world
from conftest import RESULTS, STATE, api, check_day_row, expected_history, local_today, open_main, table, wait_for

BACK_WITHIN = 60      # seconds after the restart


def test_last_days_are_back_quickly():
    restarted = float((RESULTS / "restarted").read_text())
    wait_for(lambda: [h["date"] for h in api("/api/latest")["days"]["history"]] == expected_history(local_today()),
             timeout=900, every=1, what="the last days after the restart")
    took = time.time() - restarted
    assert took <= BACK_WITHIN, f"the last days took {took:.0f} s to come back after the restart"


def test_no_completed_day_was_computed_again():
    before = json.loads((RESULTS / "cache-before.json").read_text())
    after = {p.name: p.stat().st_mtime_ns for p in (STATE / "days").glob("*.json")}
    redone = sorted(name for name, mtime in before.items() if after.get(name) != mtime)
    assert not redone, f"computed again after the restart instead of read from the cache: {redone}"


def test_last_days_still_match_the_model(page, energy):
    open_main(page)
    history = api("/api/latest")["days"]["history"]
    got = table(page, "hist")
    classes = page.evaluate("() => [...document.querySelectorAll('#hist tbody tr')].map((tr) => [...tr.cells].map((td) => td.className))")
    assert len(got) == 7
    for row, cls, h in zip(got, classes, history):
        check_day_row(row, cls, h, world.expected_day(energy, date.fromisoformat(h["date"])))
