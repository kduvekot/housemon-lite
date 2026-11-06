// housemon-lite browser side: receives raw S0 lines and P1 telegrams from the
// Pi over Server-Sent Events and does all decoding and display here. The Pi
// also sends per-day figures ("days"): the P1 meter readings at midnight, the
// S0 pulses so far today (with every S0 line) and the last completed days;
// and, if the Pi receives it, the airco's newest state and LG's energy totals.
"use strict";

const $ = (sel) => document.querySelector(sel);
let config = null;
let days = null;          // latest "days" message from the Pi

// --- small display helpers ---------------------------------------------------

// Restart the yellow fade on an element (HouseMon's "changed" highlight).
function flash(el) {
  el.classList.remove("flash");
  void el.offsetWidth;               // force a reflow so the animation restarts
  el.classList.add("flash");
}

// Only touch the DOM (and flash) when the shown text really changes.
function setText(el, text) {
  if (el.textContent !== text) {
    el.textContent = text;
    flash(el);
  }
}

// One table row per key, created on first use; returns its value cells.
const rows = new Map();
function row(table, key, label, cols) {
  let cells = rows.get(key);
  if (!cells) {
    const tr = document.createElement("tr");
    const th = document.createElement("td");
    th.textContent = label;
    tr.append(th);
    cells = cols.map((cls) => {
      const td = document.createElement("td");
      td.className = cls;
      tr.append(td);
      return td;
    });
    $(`#${table} tbody`).append(tr);
    rows.set(key, cells);
  }
  return cells;
}

const fmt = (v, digits = 0) =>
  v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const clock = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const kwh = (pulses) => (pulses * config.s0.pulseWh) / 1000;
// "14:03:12" today, "yesterday 22:10", or "Sat 31 Oct 08:15" for older
function pulseTime(ms, now) {
  const d = new Date(ms), today = new Date(now);
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (day(d) === day(today)) return clock(d);
  if (day(today) - day(d) === 86400000) return `yesterday ${hm}`;
  return `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${hm}`;
}

// --- S0 pulse meters (RF12 node, decoded like HouseMon's pulseNode) -------------

const s0state = {};       // per channel: last counter value and when it changed

function decodeS0(msg) {
  const cfg = config.s0;
  const nums = msg.line.split(" ").slice(1).map(Number);   // drop "OK"
  const header = nums[0];
  const b = nums.slice(1);
  if ((header & 0x1f) !== cfg.node || b.length < 32) return;

  const now = Date.now();
  const packetTime = Date.parse(msg.time);         // when the Pi received this packet
  const wattPerMs = cfg.pulseWh * 3.6e6;   // W x ms for one pulse (0.5 Wh -> 1,800,000)
  const sumNow = { house: 0, solar: 0, heater: 0 };
  const sumToday = { house: 0, solar: 0, heater: 0 };

  for (const [ch, info] of Object.entries(cfg.channels)) {
    const i = 4 * (Number(ch) - 1);
    const count = b[i] | (b[i + 1] << 8);          // pulses since the node started
    let t = b[i + 2] | (b[i + 3] << 8);            // time between the last two pulses
    if (t > 60000) t = (t - 60000) * 1000;         // above 60000 the unit is seconds

    const st = (s0state[ch] ??= { count, changed: null });
    const pulsedNow = st.count !== count;            // the counter went up in this packet
    if (pulsedNow) {
      st.count = count;
      st.changed = now;
    }

    // When this channel last pulsed: from the Pi (it counts every packet, also
    // before this page was opened); else what this page itself saw.
    const lastIso = msg.lastPulse?.[ch];
    const lastPulse = lastIso ? Date.parse(`${lastIso}Z`) : st.changed;

    // Time since the last pulse, in packet time: this packet's time minus the
    // last pulse's, both from the Pi's clock (not this device's), less 1 s for
    // the last pulse being rounded to whole seconds. 0 when it pulsed just now.
    const since = pulsedNow || lastPulse === null ? 0 : Math.max(0, packetTime - lastPulse - 1000);

    // Power from the pulse interval. When no new pulse has come for longer than
    // that interval, the real power must be lower: at most one pulse per `since`.
    let watt = t > 0 ? wattPerMs / t : 0;
    if (since > t) watt = Math.min(watt, wattPerMs / since);
    // At that power the next pulse was due after t. If it has not come after
    // stopFactor x t (and at least 10 s: packets come every few seconds),
    // assume the power stopped (e.g. solar at sunset, a device switched off).
    // An assumption, not a measurement: the next pulse corrects it.
    if (t > 0 && since > Math.max((cfg.stopFactor ?? 3) * t, 10000)) watt = 0;
    if (count === 0) watt = 0;

    // Today: pulses counted by the Pi since midnight (it handles node restarts).
    const today = msg.today && msg.today[ch] !== undefined ? kwh(msg.today[ch]) : null;

    const [pw, td, last] = row("s0", `s0-${ch}`, info.name, ["num", "num", "muted"]);
    setText(pw, `${fmt(watt)} W`);
    setText(td, today === null ? "–" : `${fmt(today, 3)} kWh`);
    last.textContent = lastPulse ? pulseTime(lastPulse, now) : "more than 36 h ago";
    if (info.kind) {
      sumNow[info.kind] += watt;
      if (today !== null) sumToday[info.kind] += today;
    }
  }

  for (const kind of ["house", "solar"]) {
    setText($(`#now-${kind}`), fmt(sumNow[kind]));
    setText($(`#today-${kind}`), fmt(sumToday[kind], 2));
  }
  heaterNow = { watt: sumNow.heater, today: sumToday.heater };
  showHeater();
  // Grid in/out today: from P1 when it covers the whole day, else estimated
  // from S0 (house minus solar per 30 s, worked out by the Pi).
  if (msg.todayGrid && !p1CoversToday()) {
    setText($("#today-in"), `≈ ${fmt(kwh(msg.todayGrid.in), 2)}`);
    setText($("#today-out"), `≈ ${fmt(kwh(msg.todayGrid.out), 2)}`);
    $("#today-in-note").textContent = $("#today-out-note").textContent = "estimated from S0";
  }
  sources.s0.last = Date.parse(msg.time);
}

// --- P1 smart meter (DSMR telegrams) ---------------------------------------------

// CRC16/ARC over everything from "/" up to and including "!" (DSMR 4/5).
function crc16(text) {
  let crc = 0;
  for (let i = 0; i < text.length; i++) {
    crc ^= text.charCodeAt(i);
    for (let k = 0; k < 8; k++) crc = crc & 1 ? (crc >>> 1) ^ 0xa001 : crc >>> 1;
  }
  return crc;
}

// "1-0:1.7.0(00.512*kW)" -> { "1-0:1.7.0": ["00.512*kW"] }
function parseTelegram(body) {
  const fields = {};
  for (const line of body.split(/\r?\n/)) {
    const m = line.match(/^(\d+-\d+:\d+\.\d+\.\d+)((?:\([^)]*\))+)/);
    if (m) fields[m[1]] = [...m[2].matchAll(/\(([^)]*)\)/g)].map((x) => x[1]);
  }
  return fields;
}

const num = (v) => parseFloat(v);                  // "00.512*kW" -> 0.512
const watts = (v) => fmt(num(v[0]) * 1000);
const count = (v) => String(parseInt(v[0], 10));
// DSMR time "261102114311W" -> "2026-11-02 11:43:11" (W = winter, S = summer)
const dsmrTime = (v) => `20${v.slice(0, 2)}-${v.slice(2, 4)}-${v.slice(4, 6)} ${v.slice(6, 8)}:${v.slice(8, 10)}:${v.slice(10, 12)}`;

// What to show where; rows only appear for fields the meter sends.
const P1_ROWS = [
  ["p1now", "1-0:1.7.0", "Power taken from grid", watts, "W"],
  ["p1now", "1-0:2.7.0", "Power delivered to grid", watts, "W"],
  ["p1now", "1-0:21.7.0", "L1 taken", watts, "W"],
  ["p1now", "1-0:41.7.0", "L2 taken", watts, "W"],
  ["p1now", "1-0:61.7.0", "L3 taken", watts, "W"],
  ["p1now", "1-0:22.7.0", "L1 delivered", watts, "W"],
  ["p1now", "1-0:42.7.0", "L2 delivered", watts, "W"],
  ["p1now", "1-0:62.7.0", "L3 delivered", watts, "W"],
  ["p1now", "1-0:32.7.0", "L1 voltage", (v) => fmt(num(v[0]), 1), "V"],
  ["p1now", "1-0:52.7.0", "L2 voltage", (v) => fmt(num(v[0]), 1), "V"],
  ["p1now", "1-0:72.7.0", "L3 voltage", (v) => fmt(num(v[0]), 1), "V"],
  ["p1now", "1-0:31.7.0", "L1 current", (v) => fmt(num(v[0])), "A"],
  ["p1now", "1-0:51.7.0", "L2 current", (v) => fmt(num(v[0])), "A"],
  ["p1now", "1-0:71.7.0", "L3 current", (v) => fmt(num(v[0])), "A"],
  ["p1now", "0-0:96.14.0", "Current tariff", count, ""],
  ["p1totals", "1-0:1.8.1", "Taken, tariff 1", (v) => fmt(num(v[0]), 3), "kWh"],
  ["p1totals", "1-0:1.8.2", "Taken, tariff 2", (v) => fmt(num(v[0]), 3), "kWh"],
  ["p1totals", "1-0:2.8.1", "Delivered, tariff 1", (v) => fmt(num(v[0]), 3), "kWh"],
  ["p1totals", "1-0:2.8.2", "Delivered, tariff 2", (v) => fmt(num(v[0]), 3), "kWh"],
  ["p1totals", "gas", "Gas", (v) => fmt(num(v[1]), 3), "m³"],
  ["p1totals", "gas", "Gas reading time", (v) => dsmrTime(v[0]), ""],
  ["p1info", "0-0:1.0.0", "Meter clock", (v) => dsmrTime(v[0]), ""],
  ["p1info", "0-0:96.7.21", "Power failures", count, ""],
  ["p1info", "0-0:96.7.9", "Long power failures", count, ""],
  ["p1info", "1-0:32.32.0", "L1 voltage sags", count, ""],
  ["p1info", "1-0:32.36.0", "L1 voltage swells", count, ""],
  ["p1info", "1-3:0.2.8", "DSMR version", (v) => (v[0] / 10).toFixed(1), ""],
];

let p1Bad = 0;

// Meter counters as numbers: taken (T1+T2), delivered (T1+T2), gas.
function totals(r) {
  const n = (x) => (Number.isFinite(x) ? x : 0);   // a counter the meter doesn't send counts as 0
  return {
    taken: n(r["1.8.1"]) + n(r["1.8.2"]),
    delivered: n(r["2.8.1"]) + n(r["2.8.2"]),
    gas: Number.isFinite(r.gas) ? r.gas : null,
  };
}

function decodeP1(msg) {
  const tel = msg.telegram;
  const bang = tel.lastIndexOf("!");
  const ok = parseInt(tel.slice(bang + 1, bang + 5), 16) === crc16(tel.slice(0, bang + 1));
  if (!ok) {                                       // never show values from a damaged telegram
    p1Bad += 1;
    return;
  }
  const f = parseTelegram(tel.slice(0, bang));
  const gasKey = Object.keys(f).find((k) => /^0-\d:24\.2\.1$/.test(k));
  if (gasKey) f.gas = f[gasKey];

  for (const [table, key, label, show, unit] of P1_ROWS) {
    if (!f[key]) continue;
    const [value, u] = row(table, `p1-${label}`, label, ["num", "unit"]);
    setText(value, show(f[key]));
    u.textContent = unit;
  }

  if (f["1-0:1.7.0"] && f["1-0:2.7.0"]) {
    const net = (num(f["1-0:1.7.0"][0]) - num(f["1-0:2.7.0"][0])) * 1000;
    setText($("#now-grid"), fmt(net));
    $("#now-grid-note").textContent = net < 0 ? "from P1 · delivering" : "from P1 · taking";
  }

  // Today = meter now minus the meter at midnight (sent by the Pi).
  const first = days?.today?.p1First;
  if (first) {
    const now = totals({
      "1.8.1": num(f["1-0:1.8.1"]?.[0]), "1.8.2": num(f["1-0:1.8.2"]?.[0]),
      "2.8.1": num(f["1-0:2.8.1"]?.[0]), "2.8.2": num(f["1-0:2.8.2"]?.[0]),
      gas: f.gas ? num(f.gas[1]) : undefined,
    });
    const start = totals(first);
    const taken = now.taken - start.taken;
    const delivered = now.delivered - start.delivered;
    const todayRows = [["Taken from grid", fmt(taken, 3), "kWh"], ["Delivered to grid", fmt(delivered, 3), "kWh"],
                       ["Net from grid", fmt(taken - delivered, 3), "kWh"]];
    if (now.gas !== null && start.gas !== null) todayRows.push(["Gas", fmt(now.gas - start.gas, 3), "m³"]);
    for (const [label, text, unit] of todayRows) {
      const [value, u] = row("p1today", `p1today-${label}`, label, ["num", "unit"]);
      setText(value, text);
      u.textContent = unit;
    }
    if (p1CoversToday()) {
      setText($("#today-in"), fmt(taken, 2));
      setText($("#today-out"), fmt(delivered, 2));
      $("#today-in-note").textContent = $("#today-out-note").textContent = "from P1";
    }
    if (now.gas !== null && start.gas !== null) setText($("#today-gas"), fmt(now.gas - start.gas, 2));
  }
  sources.p1.last = msg.seen * 1000;
}

// --- heating: the airco (LG, via the compact extract) and the electric heater (S0) ---

let airco = null;          // latest "airco" message: { state, energy }
let heaterNow = null;      // from the S0 heater channels: { watt, today }

const MODES = { HEAT: "heating", COOL: "cooling", AUTO: "auto", AIR_DRY: "drying", FAN: "fan only" };
const hasHeater = () => Object.values(config.s0.channels).some((c) => c.kind === "heater");

// LG's total for the newest day, and the power from its last two totals (LG
// updates them every 5 minutes; a longer gap gives no power).
function aircoEnergy() {
  const dates = Object.keys(airco?.energy ?? {}).sort();
  if (!dates.length) return null;
  const pts = airco.energy[dates[dates.length - 1]];
  const [t2, wh2] = pts[pts.length - 1];
  let watt = null;
  if (pts.length > 1) {
    const [t1, wh1] = pts[0];
    const hours = (Date.parse(t2) - Date.parse(t1)) / 3.6e6;
    // 4 to 15 minutes: a shorter step (the first after the logger started) covers only part of a period
    if (hours >= 4 / 60 && hours <= 0.25) watt = (wh2 - wh1) / hours;
  }
  return { wh: wh2, watt, time: Date.parse(t2) };
}

function onAirco(msg) {
  airco = msg;
  $("#heat-card").hidden = false;
  const st = msg.state;
  if (st) {
    const on = st.operation === "POWER_ON";
    let [v] = row("heat", "airco-on", "Airco", ["num"]);
    setText(v, on ? `on · ${MODES[st.mode] ?? st.mode.toLowerCase()}` : "off");
    [v] = row("heat", "airco-temp", "Airco's own reading", ["num"]);
    setText(v, `${fmt(st.reading, 1)} °C`);
    [v] = row("heat", "airco-target", "Target", ["num"]);
    setText(v, `${fmt(st.target, 1)} °C`);
    [v] = row("heat", "airco-fan", "Fan", ["num"]);
    setText(v, st.fan.toLowerCase());
    sources.airco.last = Date.parse(st.time);
  }
  const e = aircoEnergy();
  if (e) {
    let [v] = row("heat", "airco-power", "Airco power (LG, last 5 min)", ["num"]);
    setText(v, e.watt === null ? "–" : `${fmt(e.watt)} W`);
    [v] = row("heat", "airco-today", "Airco today (LG)", ["num"]);
    setText(v, `${fmt(e.wh / 1000, 2)} kWh`);
  }
  showHeater();
}

// The heater row only once the card is shown (that is: once airco data came).
function showHeater() {
  if (!airco || !heaterNow || !hasHeater()) return;
  let [v] = row("heat", "heater-now", "Heater group (S0)", ["num"]);
  setText(v, `${fmt(heaterNow.watt)} W`);
  [v] = row("heat", "heater-today", "Heater group today", ["num"]);
  setText(v, `${fmt(heaterNow.today, 2)} kWh`);
}

// --- days: today's note and the last completed days ------------------------------

const p1CoversToday = () => Boolean(days?.today?.p1First) && !days.today.p1Partial;

const dayName = (iso) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });

// kWh of the S0 channels of one kind ("house", "solar") from a day's pulses.
function s0kind(pulses, kind) {
  const node = pulses?.[String(config.s0.node)];
  if (!node) return null;
  let sum = 0;
  for (const [ch, info] of Object.entries(config.s0.channels)) if (info.kind === kind) sum += kwh(node[ch] ?? 0);
  return sum;
}

function onDays(msg) {
  days = msg;
  $("#today-date").textContent = `· ${dayName(msg.today.date)}`;
  const partial = [];
  if (msg.today.p1Partial) partial.push("P1");
  if (msg.today.s0Partial) partial.push("S0");
  $("#today-note").textContent = partial.length
    ? `${partial.join(" and ")} logging started after midnight: today is incomplete.` : "";

  // The history is fixed data: rebuild the table, no flashing.
  const tbody = $("#hist tbody");
  tbody.replaceChildren();
  for (const d of msg.history) {
    const p1 = d.p1.first && d.p1.last ? { a: totals(d.p1.first), b: totals(d.p1.last) } : null;
    // Grid in/out: from P1 when it covers the day; else estimated from S0 (≈).
    const useP1 = p1 && !d.p1.partial;
    const est = (v) => (d.s0.grid ? `≈ ${fmt(kwh(d.s0.grid[v]), 2)}` : "–");
    const cells = [
      [s0kind(d.s0.pulses, "house"), d.s0.partial],
      [s0kind(d.s0.pulses, "solar"), d.s0.partial],
      useP1 ? [fmt(p1.b.taken - p1.a.taken, 2), false] : [est("in"), d.s0.partial],
      useP1 ? [fmt(p1.b.delivered - p1.a.delivered, 2), false] : [est("out"), d.s0.partial],
      [p1 && p1.a.gas !== null && p1.b.gas !== null ? fmt(p1.b.gas - p1.a.gas, 2) : "–", d.p1.partial],
    ];
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = dayName(d.date);
    tr.append(name);
    for (let [value, part] of cells) {
      if (typeof value === "number") value = fmt(value, 2);
      if (value === null) value = "–";
      const td = document.createElement("td");
      td.className = "num" + (part && value !== "–" ? " partial" : "");
      td.textContent = value;
      tr.append(td);
    }
    tbody.append(tr);
  }
  $("#hist-note").textContent = msg.history.length
    ? "kWh per day (gas in m³). ≈ = estimated from S0 (no P1 data for that day). * = incomplete day: logging did not cover the whole day."
    : "No completed days yet.";
}

// --- freshness and connection ------------------------------------------------------

// airco: its state line comes on every change and at least every 15 minutes
const sources = { s0: { last: null }, p1: { last: null }, airco: { last: null, staleAfter: 20 * 60 } };

function updateAges() {
  const now = Date.now();
  for (const [name, src] of Object.entries(sources)) {
    const el = $(`#${name}-age`);
    if (src.last === null) continue;
    const age = Math.max(0, Math.round((now - src.last) / 1000));
    let text = `updated ${clock(new Date(src.last))} · ${age} s ago`;
    if (name === "p1" && p1Bad) text += ` · ${p1Bad} damaged telegram${p1Bad > 1 ? "s" : ""} skipped`;
    el.textContent = text;
    el.classList.toggle("stale", age > (src.staleAfter ?? config.staleAfter));
  }
}

let events = null;

function connect() {
  const status = $("#status");
  events = new EventSource("events");            // reconnects by itself
  // "live" only once data arrives; right after a restart the Pi first loads its figures
  events.onopen = () => { status.textContent = "connected, waiting for data…"; status.className = "status"; };
  const live = () => { if (status.textContent !== "live") { status.textContent = "live"; status.className = "status live"; } };
  for (const kind of ["s0", "p1"]) events.addEventListener(kind, live);
  events.onerror = () => { status.textContent = "reconnecting…"; status.className = "status down"; };
  events.addEventListener("s0", (e) => decodeS0(JSON.parse(e.data)));
  events.addEventListener("p1", (e) => decodeP1(JSON.parse(e.data)));
  events.addEventListener("days", (e) => onDays(JSON.parse(e.data)));
  events.addEventListener("airco", (e) => onAirco(JSON.parse(e.data)));
}

// A phone freezes background tabs and drops their connection; when the page
// is shown again, reconnect at once instead of waiting for the browser's retry.
// The server sends the latest values on every new connection.
function onVisible() {
  if (document.visibilityState === "visible" && events && events.readyState !== EventSource.OPEN) {
    events.close();
    connect();
  }
}

async function start() {
  config = await (await fetch("config.json")).json();
  $("#s0-title").textContent = config.s0.location;
  $("#p1-title").textContent = config.p1.location;
  connect();
  document.addEventListener("visibilitychange", onVisible);
  setInterval(updateAges, 1000);
}

start();
