"""Phase 1, the evening of 2027-03-29 (local): the page against the model."""

import re
from datetime import datetime, timedelta

import pytest
import world
from conftest import (BASE, PAGES, api, check_day_row, expected_history, fake_now, fmt, kwh_text, local_today,
                      open_main, rows, table, wait_for)

pytestmark = pytest.mark.usefixtures("server_ready")


def test_clock_is_in_spring():
    assert local_today().isoformat() == "2027-03-29"


def test_config_has_the_local_settings():
    cfg = api("/config.json")
    assert cfg["settlement"]["balance"] == -1234.5
    assert cfg["s0"]["node"] == world.NODE, "web/config.json is still there"


@pytest.mark.parametrize("path", PAGES)
def test_page_loads_without_errors(page, path):
    page.goto(BASE + path)
    page.wait_for_timeout(3000)         # the page fixture fails the test on any JavaScript error


def test_last_days_match_the_model(page, energy):
    open_main(page)
    history = api("/api/latest")["days"]["history"]
    assert [h["date"] for h in history] == expected_history(local_today())
    got = table(page, "hist")
    classes = page.evaluate("() => [...document.querySelectorAll('#hist tbody tr')].map((tr) => [...tr.cells].map((td) => td.className))")
    assert len(got) == 7
    for row, cls, h in zip(got, classes, history):
        check_day_row(row, cls, h, world.expected_day(energy, datetime.fromisoformat(h["date"]).date()))


def meter_time(page):
    """The time of the telegram shown: the meter clock (local) as UTC, plus its 480 ms."""
    text = rows(page, "p1info")["Meter clock"]
    local = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=world.LOCAL)
    return local.astimezone(world.UTC) + timedelta(milliseconds=480)


def test_meter_readings_match_the_model(page, energy):
    open_main(page)
    t = meter_time(page)
    shown = rows(page, "p1totals")
    if meter_time(page) != t:           # a new telegram came in between: read again
        t, shown = meter_time(page), rows(page, "p1totals")
    reg = energy.registers_kwh(t)
    assert shown["Taken, tariff 1"] == fmt(reg["1.8.1"], 3)
    assert shown["Taken, tariff 2"] == fmt(reg["1.8.2"], 3)
    assert shown["Delivered, tariff 1"] == fmt(reg["2.8.1"], 3)
    assert shown["Delivered, tariff 2"] == fmt(reg["2.8.2"], 3)


def first_telegram_of_day(day):
    start, end = world.day_bounds(day)
    return next(t for t in world.p1_times(start, end) if not world.damaged(t))


def test_today_from_p1_matches_the_model(page, energy):
    open_main(page)
    page.wait_for_function("document.querySelectorAll('#p1today tbody tr').length > 0")
    t = meter_time(page)
    today = rows(page, "p1today")
    if meter_time(page) != t:
        t, today = meter_time(page), rows(page, "p1today")
    a, b = energy.registers_kwh(first_telegram_of_day(local_today())), energy.registers_kwh(t)
    taken = (b["1.8.1"] + b["1.8.2"]) - (a["1.8.1"] + a["1.8.2"])           # as the page works it out
    delivered = (b["2.8.1"] + b["2.8.2"]) - (a["2.8.1"] + a["2.8.2"])
    assert today["Taken from grid"] == fmt(taken, 3)
    assert today["Delivered to grid"] == fmt(delivered, 3)
    # the "Today" tiles show the same, from P1 (the whole day is covered)
    tiles = page.evaluate("() => ['today-in', 'today-out', 'today-in-note'].map((id) => document.getElementById(id).textContent)")
    assert abs(kwh_text(tiles[0]) - kwh_text(today["Taken from grid"])) < 0.006
    assert abs(kwh_text(tiles[1]) - kwh_text(today["Delivered to grid"])) < 0.006
    assert tiles[2] == "from P1"


def test_s0_today_matches_the_model(energy):
    """The pulses counted today, up to the newest packet, exactly as the model has them.
    Today comes from the hour records, which count from the last packet before midnight
    (the completed days in "Last days" count from the first packet after it)."""
    msg = api("/api/latest")["s0-9"]
    newest = datetime.strptime(msg["time"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=world.UTC)
    start, _ = world.day_bounds(local_today())
    before = list(world.s0_times(start - timedelta(seconds=30), start))[-1]
    expected = {str(ch): 0 for ch in range(1, 9)}
    prev = world.s0_counts(energy, before)
    for t in world.s0_times(start, newest + timedelta(milliseconds=1)):
        c = world.s0_counts(energy, t)
        for ch in range(1, 9):
            expected[str(ch)] += (c[ch - 1] - prev[ch - 1]) % 65536
        prev = c
    assert {k: v for k, v in msg["today"].items()} == expected


def test_s0_table_and_tiles_agree(page):
    open_main(page)
    page.wait_for_function("document.querySelectorAll('#s0 tbody tr').length === 7")
    s0 = {r[0]: r for r in table(page, "s0")}
    assert list(s0) == ["Huis", "Zon 2", "Zon 1", "Groep 6", "Groep 9", "Groep 10", "Groep 12"]
    house_tile = kwh_text(page.text_content("#today-house"))
    solar_tile = kwh_text(page.text_content("#today-solar"))
    assert abs(kwh_text(s0["Huis"][2].replace("kWh", "")) - house_tile) < 0.006
    assert abs(kwh_text(s0["Zon 1"][2].replace("kWh", "")) + kwh_text(s0["Zon 2"][2].replace("kWh", "")) - solar_tile) < 0.011


def test_damaged_telegrams_are_skipped_and_counted(page):
    open_main(page)
    page.wait_for_function("document.querySelector('#p1-age').textContent.includes('damaged telegram')", timeout=60000)


def test_net_metering_band(page):
    open_main(page)
    page.wait_for_function("document.querySelector('#netm-balance').textContent !== '–'")
    t = meter_time(page)
    v = page.evaluate("() => ['netm', 'netm-label', 'netm-balance', 'netm-perday', 'netm-days', 'netm-title'].map((id) => id === 'netm' ? document.getElementById(id).hidden : document.getElementById(id).textContent)")
    if meter_time(page) != t:
        t = meter_time(page)
    shown = rows(page, "p1totals")
    taken = kwh_text(shown["Taken, tariff 1"]) + kwh_text(shown["Taken, tariff 2"])
    delivered = kwh_text(shown["Delivered, tariff 1"]) + kwh_text(shown["Delivered, tariff 2"])
    balance = -1234.5 + (taken - 8000.0) - (delivered - 3500.0)
    left = (datetime(2028, 1, 1, tzinfo=world.LOCAL) - fake_now()).total_seconds() / 86400
    assert v[0] is False, "band shown"
    assert v[5] == "Net metering 2027"
    assert v[1] == ("Surplus" if balance < 0 else "Net taken")
    assert abs(kwh_text(v[2]) - abs(balance)) <= 1.0
    if balance < 0:
        assert abs(kwh_text(v[3]) - (-balance / left)) <= 0.1
    assert abs(kwh_text(v[4]) - left) <= 0.1


def test_heating_card(page, energy):
    open_main(page)
    page.wait_for_function("!document.querySelector('#heat-card').hidden", timeout=30000)
    heat = rows(page, "heat")
    st = world.airco_state(energy, fake_now())
    assert heat["Airco"] == ("off" if st[0] == "POWER_OFF" else "on · heating")
    assert heat["Target"] == f"{fmt(st[4], 1)} °C"


def test_graph_data_match_the_model(energy):
    """House and solar per minute (S0) for the last 2 hours, and the grid per minute (P1) for the
    minutes the server built from the log files at start-up. Live telegrams are filed by the
    moment the server reads them (up to a second later), so a live minute can differ by one."""
    s = api("/api/series?hours=2")
    start = datetime.fromisoformat(s["start"])
    want_house, want_solar, net_sum, net_n = {}, {}, {}, {}
    prev = None
    for t in world.s0_times(start - timedelta(minutes=1), start + timedelta(minutes=len(s["house"]))):
        c = world.s0_counts(energy, t)
        m = int((t - start).total_seconds() // 60)
        if prev is not None and m >= 0:
            want_house[m] = want_house.get(m, 0) + (c[world.HOUSE - 1] - prev[world.HOUSE - 1]) % 65536
            want_solar[m] = want_solar.get(m, 0) + sum((c[ch - 1] - prev[ch - 1]) % 65536 for ch in (world.SOLAR1, world.SOLAR2))
        prev = c
    pat = re.compile(rb"1-0:1\.7\.0\((\d+\.\d+)\*kW\).*?1-0:2\.7\.0\((\d+\.\d+)\*kW\)", re.S)
    for t in world.p1_times(start, start + timedelta(minutes=len(s["grid"]))):
        m = int((t - start).total_seconds() // 60)
        a, b = pat.search(world.p1_telegram(energy, t)).groups()
        net_sum[m] = net_sum.get(m, 0.0) + 1000 * (float(a) - float(b))
        net_n[m] = net_n.get(m, 0) + 1
    from_logs = int((world.CLOCK_START - timedelta(minutes=1) - start).total_seconds() // 60)
    grid_checked = 0
    for m in range(len(s["house"])):
        assert s["house"][m] == round(want_house.get(m, 0) * world.WH_PER_PULSE * 60), f"house, minute {m}"
        assert s["solar"][m] == round(want_solar.get(m, 0) * world.WH_PER_PULSE * 60), f"solar, minute {m}"
        if m < from_logs and net_n.get(m):
            assert s["grid"][m] == round(net_sum[m] / net_n[m]), f"grid, minute {m}"
            grid_checked += 1
    assert len(s["house"]) >= 100 and grid_checked >= 90
