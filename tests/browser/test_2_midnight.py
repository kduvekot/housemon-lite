"""Phase 2, just after midnight (local) on 2027-03-30: the day rollover."""

from datetime import date

import world
from conftest import api, check_day_row, expected_history, local_today, open_main, table, wait_for


def test_the_server_moved_on_to_the_new_day():
    assert local_today() == date(2027, 3, 30)
    wait_for(lambda: api("/api/latest")["days"]["today"]["date"] == "2027-03-30", timeout=30, what="the new day")
    assert [h["date"] for h in api("/api/latest")["days"]["history"]] == expected_history(local_today())


def test_last_days_include_the_day_that_just_ended(page, energy):
    open_main(page)
    history = api("/api/latest")["days"]["history"]
    assert history[0]["date"] == "2027-03-29"
    got = table(page, "hist")
    classes = page.evaluate("() => [...document.querySelectorAll('#hist tbody tr')].map((tr) => [...tr.cells].map((td) => td.className))")
    assert len(got) == 7
    for row, cls, h in zip(got, classes, history):
        check_day_row(row, cls, h, world.expected_day(energy, date.fromisoformat(h["date"])))


def test_today_starts_from_zero(page):
    open_main(page)
    page.wait_for_function("document.querySelector('#today-date').textContent.includes('Mar 30')")
    house = float(page.text_content("#today-house").replace(",", ""))
    assert house < 0.5, "only a few minutes of the new day"
