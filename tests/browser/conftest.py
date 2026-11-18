"""Shared parts of the browser tests: the server, the fake clock, the model and a page."""

import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fake"))
import clock  # noqa: E402
import world  # noqa: E402

BASE = os.environ.get("BASE", "http://server:3333")
CLOCK = Path(os.environ.get("CLOCK_DIR", "/clock"))
STATE = Path("/state")
RESULTS = Path("/results")
PAGES = ["/", "/graph.html", "/graph.html?kind=gas", "/heating.html"]


def fake_now():
    return clock.now(CLOCK)


def local_today():
    return fake_now().astimezone(world.LOCAL).date()


def api(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def fmt(value, digits=0):
    """As the page's fmt(): en-US, fixed digits, ties away from zero (like toLocaleString)."""
    q = Decimal(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return f"{q:,.{digits}f}"


def day_name(d):
    """As the page's dayName() in en-US: "Sun, Mar 28"."""
    return f"{d:%a}, {d:%b} {d.day}"


def wait_for(check, timeout, every=1.0, what="condition"):
    end = time.monotonic() + timeout
    while True:
        try:
            value = check()
            if value:
                return value
        except Exception:       # the server may still be starting
            if time.monotonic() > end:
                raise
        if time.monotonic() > end:
            raise AssertionError(f"timed out after {timeout} s waiting for {what}")
        time.sleep(every)


def history_dates():
    return [h["date"] for h in api("/api/latest")["days"]["history"]]


def expected_history(today):
    return [(today - timedelta(days=k)).isoformat() for k in range(1, 8)]


@pytest.fixture(scope="session")
def energy():
    return world.Energy()


@pytest.fixture(scope="session", autouse=True)
def server_ready():
    """The server answers and has its 7 completed days for today's date."""
    wait_for(lambda: history_dates() == expected_history(local_today()), timeout=900, every=2,
             what="the server's last 7 days")


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """A phone-sized page in Dutch time, on the fake clock; fails the test on any JavaScript error."""
    ctx = browser.new_context(viewport={"width": 412, "height": 915}, locale="en-US",
                              timezone_id="Europe/Amsterdam")
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.clock.install(time=fake_now())
    yield pg
    ctx.close()
    assert not errors, f"JavaScript errors: {errors}"


def open_main(page):
    page.goto(BASE + "/")
    page.wait_for_function("document.querySelectorAll('#hist tbody tr').length > 0", timeout=30000)
    page.wait_for_function("document.querySelector('#p1-age').textContent.startsWith('updated')", timeout=30000)
    return page


def table(page, table_id):
    """Rows of a table as lists of cell texts."""
    return page.evaluate(f"""() => [...document.querySelectorAll('#{table_id} tbody tr')]
        .map((tr) => [...tr.cells].map((td) => td.textContent))""")


def rows(page, table_id):
    """A two-column table as {label: value}."""
    return {r[0]: r[1] for r in table(page, table_id)}


def kwh_text(text):
    return float(text.replace(",", "").replace("≈", "").strip())


def check_day_row(row, classes, h, exp):
    """One "Last days" row against the model's day and the server's own figures."""
    d = date.fromisoformat(exp["date"])
    got = {int(c): n for c, n in h["s0"]["pulses"]["9"].items()}
    assert got == exp["pulses"], f"S0 pulses of {exp['date']} (server) against the model"
    assert row[0] == day_name(d)
    assert row[1] == fmt(exp["house_kwh"], 2), "house"
    assert row[2] == fmt(exp["solar_kwh"], 2), "solar"
    assert ("partial" in classes[1]) == exp["s0_partial"], "house marked incomplete"
    if "grid_in_kwh" in exp and not exp["p1_partial"]:
        assert row[3] == fmt(exp["grid_in_kwh"], 2), "grid in"
        assert row[4] == fmt(exp["grid_out_kwh"], 2), "grid out"
        assert row[5] == fmt(exp["gas_m3"], 2), "gas"
    else:
        # no complete P1 day: estimated from S0; in − out is exactly house − solar
        g = h["s0"]["grid"]
        p = h["s0"]["pulses"]["9"]
        assert g["in"] - g["out"] == p["2"] - p["3"] - p["4"], "S0 grid estimate"
        assert row[3] == f"≈ {fmt(g['in'] * world.WH_PER_PULSE / 1000, 2)}", "grid in (estimate)"
        assert row[4] == f"≈ {fmt(g['out'] * world.WH_PER_PULSE / 1000, 2)}", "grid out (estimate)"
        assert row[5] == (fmt(exp["gas_m3"], 2) if "gas_m3" in exp else "–"), "gas"
