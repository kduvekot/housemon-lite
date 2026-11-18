"""
A made-up household for testing housemon-lite.

Everything is derived from time alone, so every run sees the same data and the
tests can work out what the page must show. The shape follows a real house (a
base load of computers, a fridge, cooking and lights in the evening, an
electric heater and an airco used for heating on cold mornings, two solar
inverters, gas for hot water), but every number is invented, and it is set in
spring: around the switch to summer time on 2027-03-28 (a 23-hour day).

Built in on purpose, to test the hard cases:
- the S0 logger starts mid-morning on its first day (an incomplete day);
- the P1 meter is connected two days later (days without P1, one partial);
- the S0 node loses power for 7 minutes once (its counters restart at 0);
- S0 counters wrap at 65536 (the house does that about every two days);
- some P1 telegrams are damaged (wrong CRC).

Times are UTC unless a name says local; local means Europe/Amsterdam.
"""

import math
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
LOCAL = ZoneInfo("Europe/Amsterdam")


def utc(*a):
    return datetime(*a, tzinfo=UTC)


# --- the timeline -------------------------------------------------------------------

S0_START = utc(2027, 3, 22, 8, 12, 0)          # 09:12 local: the first day is incomplete
P1_START = utc(2027, 3, 24, 12, 40, 0)         # 13:40 local: P1 connected
NODE_OFF = (utc(2027, 3, 26, 15, 5, 0), utc(2027, 3, 26, 15, 12, 0))   # node without power
AIRCO_START = utc(2027, 3, 25, 0, 0, 0)
CLOCK_START = utc(2027, 3, 29, 21, 40, 0)      # 23:40 local: the fake clock starts here (tests/run.sh)
MODEL_END = CLOCK_START + timedelta(days=2)    # the model is defined up to here

STEP = 10               # seconds: power is constant within a step
S0_EVERY = 3            # seconds between S0 packets
WH_PER_PULSE = 0.5
NODE = 9
# S0 channels of the pulse node, as in web/config.json
HOUSE, SOLAR2, SOLAR1, GROUP6, GROUP9, GROUP10, GROUP12 = 2, 3, 4, 5, 6, 7, 8
HEATER = GROUP10

METER_ID = "4530303030303030303030303030303030"   # "E000…": made up
GAS_ID = "4730303030303030303030303030303030"
START_REGISTERS = {"1.8.1": 4321123.0, "1.8.2": 3876456.0, "2.8.1": 1234000.0, "2.8.2": 2345678.0}  # Wh
START_GAS = 1234.567        # m3


def noise(*key):
    """A fixed pseudo-random number in [0, 1) for a key."""
    return random.Random("|".join(map(str, key))).random()


def local(t):
    return t.astimezone(LOCAL)


def hours_local(t):
    lt = local(t)
    return lt.hour + lt.minute / 60 + lt.second / 3600


# --- weather: a cool spring with a mild spell --------------------------------------

def day_temp(d):
    """Mean outdoor temperature (°C) of a local date."""
    return 4.0 + 7.0 * noise("temp", d)


def cloudiness(d):
    return noise("cloud", d)            # 0 clear .. 1 overcast


# --- power per channel (W), constant within a STEP ---------------------------------

def sun_times_utc(d):
    """Sunrise and sunset (UTC hours) for the east of the Netherlands, late March."""
    doy = d.timetuple().tm_yday
    return 5.45 - (doy - 80) * 0.030, 17.95 + (doy - 80) * 0.027


def solar_w(t, peak, key):
    d = local(t).date()
    rise, sets = sun_times_utc(d)
    h = t.hour + t.minute / 60 + t.second / 3600
    if not rise < h < sets:
        return 0.0
    shape = math.sin(math.pi * (h - rise) / (sets - rise)) ** 1.6
    k = int((t - utc(2027, 1, 1)).total_seconds() // 600)          # clouds change every 10 minutes
    passing = 1.0 - 0.75 * cloudiness(d) * noise("pass", key, k)
    return min(3300.0, peak * shape * passing)


def power(t):
    """{channel: W} for the step that holds t, plus 'airco' (inside the house, not metered apart)."""
    t = t.replace(microsecond=0) - timedelta(seconds=t.second % STEP)
    d = local(t).date()
    h = hours_local(t)
    cold = day_temp(d) < 7.0
    minute = int((t - utc(2027, 1, 1)).total_seconds() // 60)
    # group 6: the fridge cycles (18 min on per 45), plus the kettle at 07:02
    fridge = 92.0 if minute % 45 < 18 else 3.0
    kettle = 2050.0 if 7.0 + 2 / 60 <= h < 7.0 + 5 / 60 else 0.0
    g6 = fridge + kettle
    # group 9: kitchen and lights; cooking on most evenings
    lights = 140.0 if 18.5 <= h < 23.0 else (25.0 if 7.0 <= h < 8.0 else 8.0)
    cooking = 1900.0 if 17.75 <= h < 18.4 and noise("cook", d) > 0.2 else 0.0
    g9 = lights + cooking
    # group 10: the electric heater on cold mornings (its group also has ~80 W of other things)
    g10 = 80.0 + (1950.0 if cold and 6.5 <= h < 7.5 else 0.0)
    # group 12: small things
    g12 = 45.0 + 15.0 * noise("g12", minute // 15)
    # the airco heats on cold mornings and lightly in the evening
    airco = 0.0
    if t >= AIRCO_START:
        if cold and 6.0 <= h < 9.5:
            airco = 650.0 + 250.0 * noise("airco", minute // 10)
        elif 18.0 <= h < 22.0 and day_temp(d) < 9.0:
            airco = 280.0
    base = 255.0 + 50.0 * noise("base", minute // 7)          # computers and the rest, day and night
    house = base + g6 + g9 + g10 + g12 + airco
    return {1: 0.0, HOUSE: house, SOLAR2: solar_w(t, 2750.0, "z2"), SOLAR1: solar_w(t, 2900.0, "z1"),
            GROUP6: g6, GROUP9: g9, GROUP10: g10, GROUP12: g12, "airco": airco}


def gas_m3_per_h(t):
    d = local(t).date()
    h = hours_local(t)
    rate = 0.0
    if 7.1 <= h < 7.35:
        rate += 1.3                     # showers
    if 21.0 <= h < 21.2:
        rate += 0.6
    if 17.75 <= h < 18.4 and noise("cook", d) > 0.2:
        rate += 0.25                    # gas hob
    if day_temp(d) < 5.5 and (6.0 <= h < 9.0 or 17.0 <= h < 22.0):
        rate += 0.35                    # some heating on the coldest days
    return rate


# --- energy, integrated once ----------------------------------------------------------

class Energy:
    """Cumulative Wh per channel (and airco, gas, P1 registers) from S0_START, per STEP."""

    def __init__(self, start=S0_START, end=MODEL_END):
        self.start = start
        n = int((end - start).total_seconds() // STEP) + 1
        keys = [1, HOUSE, SOLAR2, SOLAR1, GROUP6, GROUP9, GROUP10, GROUP12, "airco"]
        self.keys = keys
        self.watts = []                 # per step: dict of W
        self.cum = {k: [0.0] * (n + 1) for k in keys}
        self.gas = [0.0] * (n + 1)      # m3
        self.reg = {r: [0.0] * (n + 1) for r in START_REGISTERS}   # Wh since start
        for i in range(n):
            t = start + timedelta(seconds=i * STEP)
            w = power(t)
            self.watts.append(w)
            for k in keys:
                self.cum[k][i + 1] = self.cum[k][i] + w[k] * STEP / 3600
            self.gas[i + 1] = self.gas[i] + gas_m3_per_h(t) * STEP / 3600
            net = w[HOUSE] - w[SOLAR1] - w[SOLAR2]
            low = low_tariff(t)
            for r in START_REGISTERS:
                self.reg[r][i + 1] = self.reg[r][i]
            if net > 0:
                self.reg["1.8.1" if low else "1.8.2"][i + 1] += net * STEP / 3600
            else:
                self.reg["2.8.1" if low else "2.8.2"][i + 1] += -net * STEP / 3600

    def _at(self, series, t):
        x = (t - self.start).total_seconds() / STEP
        i = max(0, min(len(series) - 2, int(x)))
        return series[i] + (series[i + 1] - series[i]) * (x - i)

    def wh(self, key, t):
        return self._at(self.cum[key], t)

    def watts_at(self, t):
        i = int((t - self.start).total_seconds() // STEP)
        return self.watts[max(0, min(len(self.watts) - 1, i))]

    def registers_kwh(self, t):
        """The P1 meter's registers at t (kWh, 3 decimals, rounded down like a meter)."""
        return {r: math.floor(START_REGISTERS[r] + self._at(self.reg[r], t)) / 1000 for r in START_REGISTERS}

    def gas_m3(self, t):
        return math.floor((START_GAS + self._at(self.gas, t)) * 1000) / 1000


def low_tariff(t):
    lt = local(t)
    return lt.weekday() >= 5 or lt.hour >= 23 or lt.hour < 7


# --- S0: RF12 packets of the pulse node -------------------------------------------------

def s0_times(start, end):
    """Packet times in [start, end): every S0_EVERY s from S0_START, none while the node is off."""
    first = max(start, S0_START)
    k = math.ceil((first - S0_START).total_seconds() / S0_EVERY - 1e-9)
    while True:
        t = S0_START + timedelta(seconds=k * S0_EVERY, milliseconds=137 + (k * 211) % 400)
        if t >= end:
            return
        if t >= first and not NODE_OFF[0] <= t < NODE_OFF[1]:
            yield t
        k += 1


def node_on_since(t):
    return NODE_OFF[1] if t >= NODE_OFF[1] else S0_START


def s0_counts(energy, t):
    """The node's 8 counters (pulses since it last started, wrapped at 65536)."""
    since = node_on_since(t)
    return [int((energy.wh(ch, t) - energy.wh(ch, since)) / WH_PER_PULSE) % 65536 for ch in range(1, 9)]


def s0_interval(watts):
    """The node's 'time between the last two pulses' field (ms; above 60000: 60000 + seconds)."""
    if watts <= 0:
        return 0
    ms = WH_PER_PULSE * 3.6e6 / watts
    return int(ms) if ms <= 60000 else min(65535, 60000 + int(ms / 1000))


def s0_line(energy, t):
    counts = s0_counts(energy, t)
    w = energy.watts_at(t)
    data = []
    for ch in range(1, 9):
        iv = s0_interval(w[ch]) if counts[ch - 1] else 0
        data += [counts[ch - 1] & 255, counts[ch - 1] >> 8, iv & 255, iv >> 8]
    stamp = t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"
    return f"L {stamp} /dev/jeelink OK {NODE} " + " ".join(map(str, data)) + "\n"


# --- P1: DSMR 5 telegrams, one per second -------------------------------------------------

def _crc_table():
    table = []
    for b in range(256):
        crc = b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        table.append(crc)
    return table


CRC_TABLE = _crc_table()


def crc16(data):
    """CRC16/ARC (as DSMR uses), table-driven for speed."""
    crc = 0
    for b in data:
        crc = (crc >> 8) ^ CRC_TABLE[(crc ^ b) & 0xFF]
    return crc


def dsmr_time(t):
    lt = local(t)
    return lt.strftime("%y%m%d%H%M%S") + ("S" if lt.dst() else "W")


def damaged(t):
    """Every 37th second has a damaged telegram (wrong CRC), as a real line sometimes has."""
    return int(t.timestamp()) % 37 == 0


DENSE_FROM = CLOCK_START - timedelta(hours=40)   # before this, one telegram per 10 s (keeps the history small)


def p1_times(start, end):
    """Telegram times in [start, end): every second (every 10 s before DENSE_FROM), from P1_START."""
    t = max(start, P1_START).replace(microsecond=0) + timedelta(milliseconds=480)
    while t < max(start, P1_START):
        t += timedelta(seconds=1)
    while t < end:
        if t >= DENSE_FROM or int(t.timestamp()) % 10 == 0:
            yield t
        t += timedelta(seconds=1)


def p1_telegram(energy, t):
    reg = energy.registers_kwh(t)
    w = energy.watts_at(t)
    net = w[HOUSE] - w[SOLAR1] - w[SOLAR2]
    gas_t = t.replace(second=0, microsecond=0) - timedelta(minutes=t.minute % 5)
    lines = [
        "/KFM5KAIFA-METER", "",
        "1-3:0.2.8(50)",
        f"0-0:1.0.0({dsmr_time(t)})",
        f"0-0:96.1.1({METER_ID})",
        f"1-0:1.8.1({reg['1.8.1']:010.3f}*kWh)",
        f"1-0:1.8.2({reg['1.8.2']:010.3f}*kWh)",
        f"1-0:2.8.1({reg['2.8.1']:010.3f}*kWh)",
        f"1-0:2.8.2({reg['2.8.2']:010.3f}*kWh)",
        f"0-0:96.14.0({1 if low_tariff(t) else 2:04d})",
        f"1-0:1.7.0({max(net, 0) / 1000:06.3f}*kW)",
        f"1-0:2.7.0({max(-net, 0) / 1000:06.3f}*kW)",
        "0-0:96.7.21(00003)", "0-0:96.7.9(00001)",
        "1-0:99.97.0(0)(0-0:96.7.19)",
        "1-0:32.32.0(00000)", "1-0:32.36.0(00000)", "0-0:96.13.0()",
        f"1-0:32.7.0({229.0 + 3 * noise('volt', int(t.timestamp()) // 10):05.1f}*V)",
        f"1-0:31.7.0({int(abs(net) / 230):03d}*A)",
        f"1-0:21.7.0({max(net, 0) / 1000:06.3f}*kW)",
        f"1-0:22.7.0({max(-net, 0) / 1000:06.3f}*kW)",
        "0-1:24.1.0(003)",
        f"0-1:96.1.0({GAS_ID})",
        f"0-1:24.2.1({dsmr_time(gas_t)})({energy.gas_m3(gas_t):09.3f}*m3)",
    ]
    body = "\r\n".join(lines) + "\r\n!"
    crc = crc16(body.encode())
    if damaged(t):
        crc ^= 0x0101
    return (body + f"{crc:04X}\r\n").encode()


# --- airco: lg-airco-compact lines ------------------------------------------------------

def airco_state(energy, t):
    w = energy.watts_at(t)["airco"]
    if w <= 0:
        return ["POWER_OFF", "HEAT", "LOW", 20.5, 20]
    return ["POWER_ON", "HEAT", "HIGH" if w > 600 else "MID", 19.5 if w > 600 else 20.5, 21]


def airco_lines(energy, start, end):
    """Compact lines in [start, end): a state line on change and every 15 minutes,
    and LG's total for the local day every 5 minutes (at about :x0:30 and :x5:30)."""
    out = []
    t = max(start, AIRCO_START).replace(second=30, microsecond=0)
    if t < max(start, AIRCO_START):
        t += timedelta(minutes=1)
    last = None
    while t < end:
        st = airco_state(energy, t)
        if st != last or t.minute % 15 == 0:
            out.append((t, "state", st))
            last = st
        if t.minute % 5 == 0:
            d = local(t).date()
            midnight = datetime(d.year, d.month, d.day, tzinfo=LOCAL).astimezone(UTC)
            wh = int(energy.wh("airco", t) - energy.wh("airco", max(midnight, AIRCO_START)))
            out.append((t, "energy", [d.strftime("%Y%m%d"), wh]))
        t += timedelta(minutes=1)
    return [f"{t:%Y-%m-%dT%H:%M:%S}Z,{kind}," + ",".join(map(str, f)) + "\n" for t, kind, f in out]


# --- what housemon-lite must show for a completed local day ------------------------------

def day_bounds(d):
    nxt = d + timedelta(days=1)
    return (datetime(d.year, d.month, d.day, tzinfo=LOCAL).astimezone(UTC),
            datetime(nxt.year, nxt.month, nxt.day, tzinfo=LOCAL).astimezone(UTC))


def expected_day(energy, d):
    """The figures of a local day as the page shows them in "Last days".

    S0: the pulses counted from packet to packet, starting from the last packet
    before midnight (if there is one in the 5 minutes before), so a pulse counts
    on the day of the packet that reports it and none is lost between two days;
    after the node restarts its counters count from 0.
    P1: last valid telegram of the day minus the first one.
    """
    start, end = day_bounds(d)
    pulses = {ch: 0 for ch in range(1, 9)}
    before = list(s0_times(start - timedelta(minutes=5), start))
    prev = (before[-1], s0_counts(energy, before[-1])) if before else None
    first = last = None
    for t in s0_times(start, end):
        c = s0_counts(energy, t)
        if prev is not None:
            restarted = node_on_since(t) != node_on_since(prev[0])
            for ch in range(1, 9):
                old, new = prev[1][ch - 1], c[ch - 1]
                pulses[ch] += new if restarted else (new - old) % 65536
        prev = (t, c)
        first = first or t
        last = t
    p1 = [t for t in p1_times(start, end) if not damaged(t)]
    out = {
        "date": d.isoformat(),
        "house_kwh": pulses[HOUSE] * WH_PER_PULSE / 1000,
        "solar_kwh": (pulses[SOLAR1] + pulses[SOLAR2]) * WH_PER_PULSE / 1000,
        "pulses": pulses,
        "s0_partial": first is None or (first - start).total_seconds() > 600 or (end - last).total_seconds() > 600,
        "p1_partial": not p1 or p1[0] - start >= timedelta(hours=1) or end - p1[-1] > timedelta(hours=1),
    }
    if p1:
        # as the page works it out: floating point, (T1 + T2 at the end) - (T1 + T2 at the start)
        a, b = energy.registers_kwh(p1[0]), energy.registers_kwh(p1[-1])
        out["grid_in_kwh"] = (b["1.8.1"] + b["1.8.2"]) - (a["1.8.1"] + a["1.8.2"])
        out["grid_out_kwh"] = (b["2.8.1"] + b["2.8.2"]) - (a["2.8.1"] + a["2.8.2"])
        ga = energy.gas_m3(p1[0].replace(second=0, microsecond=0) - timedelta(minutes=p1[0].minute % 5))
        gb = energy.gas_m3(p1[-1].replace(second=0, microsecond=0) - timedelta(minutes=p1[-1].minute % 5))
        out["gas_m3"] = gb - ga
    return out
