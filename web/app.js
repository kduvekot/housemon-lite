// housemon-lite browser side: receives raw S0 lines and P1 telegrams from the
// Pi over Server-Sent Events and does all decoding and display here.
"use strict";

const $ = (sel) => document.querySelector(sel);
let config = null;

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

// One table row per key, created on first use; returns its cells.
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

// --- S0 pulse meters (RF12 node, decoded like HouseMon's pulseNode) -------------

// Per channel: last counter value and when we saw it change.
const s0state = {};
const tileValues = { house: null, solar: null };

function decodeS0(msg) {
  const cfg = config.s0;
  const nums = msg.line.split(" ").slice(1).map(Number);   // drop "OK"
  const header = nums[0];
  const b = nums.slice(1);
  if ((header & 0x1f) !== cfg.node || b.length < 32) return;

  const now = Date.now();
  const wattPerMs = cfg.pulseWh * 3.6e6;   // W x ms for one pulse (0.5 Wh -> 1,800,000)
  const sums = { house: 0, solar: 0 };

  for (const [ch, info] of Object.entries(cfg.channels)) {
    const i = 4 * (Number(ch) - 1);
    const count = b[i] | (b[i + 1] << 8);          // pulses since the node started
    let t = b[i + 2] | (b[i + 3] << 8);            // time between the last two pulses
    if (t > 60000) t = (t - 60000) * 1000;         // above 60000 the unit is seconds

    const st = (s0state[ch] ??= { count, changed: null });
    if (st.count !== count) {
      st.count = count;
      st.changed = now;
    }

    // Power from the pulse interval. When no new pulse has come for longer than
    // that interval, the real power must be lower: use the time since then.
    let watt = t > 0 ? wattPerMs / t : 0;
    if (st.changed !== null && now - st.changed > t) watt = Math.min(watt, wattPerMs / (now - st.changed));
    if (count === 0) watt = 0;

    const [pw, cnt, last] = row("s0", `s0-${ch}`, info.name, ["num", "num", "muted"]);
    setText(pw, `${fmt(watt)} W`);
    setText(cnt, `${fmt((count * cfg.pulseWh) / 1000, 3)} kWh`);
    last.textContent = st.changed ? clock(new Date(st.changed)) : "–";
    if (info.kind) sums[info.kind] += watt;
  }

  for (const kind of ["house", "solar"]) {
    tileValues[kind] = sums[kind];
    setText($(`#tile-${kind}`), fmt(sums[kind]));
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
// DSMR time "251202174606W" -> "2025-12-02 17:46:06" (W = winter, S = summer)
const dsmrTime = (v) => `20${v.slice(0, 2)}-${v.slice(2, 4)}-${v.slice(4, 6)} ${v.slice(6, 8)}:${v.slice(8, 10)}:${v.slice(10, 12)}`;

// What to show, in this order; rows only appear for fields the meter sends.
const P1_ROWS = [
  ["1-0:1.7.0", "Power taken from grid", (v) => fmt(num(v[0]) * 1000), "W"],
  ["1-0:2.7.0", "Power delivered to grid", (v) => fmt(num(v[0]) * 1000), "W"],
  ["1-0:21.7.0", "L1 taken", (v) => fmt(num(v[0]) * 1000), "W"],
  ["1-0:41.7.0", "L2 taken", (v) => fmt(num(v[0]) * 1000), "W"],
  ["1-0:61.7.0", "L3 taken", (v) => fmt(num(v[0]) * 1000), "W"],
  ["1-0:22.7.0", "L1 delivered", (v) => fmt(num(v[0]) * 1000), "W"],
  ["1-0:42.7.0", "L2 delivered", (v) => fmt(num(v[0]) * 1000), "W"],
  ["1-0:62.7.0", "L3 delivered", (v) => fmt(num(v[0]) * 1000), "W"],
  ["1-0:32.7.0", "L1 voltage", (v) => fmt(num(v[0]), 1), "V"],
  ["1-0:52.7.0", "L2 voltage", (v) => fmt(num(v[0]), 1), "V"],
  ["1-0:72.7.0", "L3 voltage", (v) => fmt(num(v[0]), 1), "V"],
  ["1-0:31.7.0", "L1 current", (v) => fmt(num(v[0])), "A"],
  ["1-0:51.7.0", "L2 current", (v) => fmt(num(v[0])), "A"],
  ["1-0:71.7.0", "L3 current", (v) => fmt(num(v[0])), "A"],
  ["1-0:1.8.1", "Taken, tariff 1", (v) => fmt(num(v[0]), 3), "kWh"],
  ["1-0:1.8.2", "Taken, tariff 2", (v) => fmt(num(v[0]), 3), "kWh"],
  ["1-0:2.8.1", "Delivered, tariff 1", (v) => fmt(num(v[0]), 3), "kWh"],
  ["1-0:2.8.2", "Delivered, tariff 2", (v) => fmt(num(v[0]), 3), "kWh"],
  ["0-0:96.14.0", "Current tariff", (v) => String(parseInt(v[0], 10)), ""],
  ["gas", "Gas", (v) => fmt(num(v[1]), 3), "m³"],
  ["gas", "Gas reading time", (v) => dsmrTime(v[0]), ""],
  ["0-0:1.0.0", "Meter clock", (v) => dsmrTime(v[0]), ""],
  ["0-0:96.7.21", "Power failures", (v) => String(parseInt(v[0], 10)), ""],
  ["0-0:96.7.9", "Long power failures", (v) => String(parseInt(v[0], 10)), ""],
  ["1-0:32.32.0", "L1 voltage sags", (v) => String(parseInt(v[0], 10)), ""],
  ["1-0:32.36.0", "L1 voltage swells", (v) => String(parseInt(v[0], 10)), ""],
  ["1-3:0.2.8", "DSMR version", (v) => (v[0] / 10).toFixed(1), ""],
];

let p1Bad = 0;

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

  for (const [key, label, show, unit] of P1_ROWS) {
    if (!f[key]) continue;
    const [value, u] = row("p1", `p1-${label}`, label, ["num", "unit"]);
    setText(value, show(f[key]));
    u.textContent = unit;
  }

  if (f["1-0:1.7.0"] && f["1-0:2.7.0"]) {
    const net = (num(f["1-0:1.7.0"][0]) - num(f["1-0:2.7.0"][0])) * 1000;
    setText($("#tile-grid"), fmt(net));
    $("#tile-grid-note").textContent = net < 0 ? "from P1 · delivering" : "from P1 · taking";
  }
  sources.p1.last = msg.seen * 1000;
}

// --- freshness and connection ------------------------------------------------------

const sources = { s0: { last: null }, p1: { last: null } };

function updateAges() {
  const now = Date.now();
  for (const [name, src] of Object.entries(sources)) {
    const el = $(`#${name}-age`);
    if (src.last === null) continue;
    const age = Math.max(0, Math.round((now - src.last) / 1000));
    let text = `updated ${clock(new Date(src.last))} · ${age} s ago`;
    if (name === "p1" && p1Bad) text += ` · ${p1Bad} damaged telegram${p1Bad > 1 ? "s" : ""} skipped`;
    el.textContent = text;
    el.classList.toggle("stale", age > config.staleAfter);
  }
}

let events = null;

function connect() {
  const status = $("#status");
  events = new EventSource("events");            // reconnects by itself
  events.onopen = () => { status.textContent = "live"; status.className = "status live"; };
  events.onerror = () => { status.textContent = "reconnecting…"; status.className = "status down"; };
  events.addEventListener("s0", (e) => decodeS0(JSON.parse(e.data)));
  events.addEventListener("p1", (e) => decodeP1(JSON.parse(e.data)));
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
