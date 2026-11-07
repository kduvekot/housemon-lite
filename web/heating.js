// housemon-lite heating graph: the airco's own temperature reading and its
// target (LG, from the compact extract) on top; below, the airco's power from
// LG's 5-minute energy totals and the electric heater (S0) per minute.
// Plain SVG, refreshed once a minute.
"use strict";

const $ = (sel) => document.querySelector(sel);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const MINUTE = 60 * 1000;
const LEFT = 52, RIGHT = 12, TOP = 10, GAP = 26, BOTTOM = 28;   // px
let hours = 12;
let data = null;      // { t0, t1, temps, targets, aircoW, heater, heaterStart }
let geo = null;

const svgEl = (tag, attrs, parent) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.append(e);
  return e;
};
const fmtTime = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const niceStep = (span) => {
  const p = 10 ** Math.floor(Math.log10(Math.max(span, 1e-3) / 4));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => span / s <= 5);
};

async function load() {
  const [series, airco] = await Promise.all([
    fetch(`api/series?hours=${hours + 1}`).then((r) => r.json()),
    fetch(`api/airco?hours=${hours + 1}`).then((r) => r.json()),
  ]);
  const t1 = Date.now(), t0 = t1 - hours * 60 * MINUTE;
  const temps = [], targets = [], aircoW = [];
  const last = {};                 // LG date -> its previous total (Wh)
  let lastUpdate = null;           // time of LG's previous update, of any day
  for (const [time, kind, ...f] of airco.lines) {
    const t = Date.parse(time);
    if (kind === "state") {
      temps.push([t, f[3]]);
      targets.push([t, f[0] === "POWER_ON" ? f[4] : null]);
    } else if (kind === "energy") {
      // Every LG update (every 5 minutes) adds the energy since its previous
      // update to the day's total. A block runs from the previous update, of
      // any day, to this one: the first block of a new day starts at the old
      // day's last update and counts from 0 (LG counts Dutch days; the block
      // that ends just after midnight belongs to the new day). A new day's 0
      // before its first update is no block; neither is the first total seen
      // of a day whose start we did not see (the graph window starts mid-day).
      const [date, wh] = f;
      const newDay = !(date in last) && Object.keys(last).some((d) => Number(d) < Number(date));
      const from = date in last ? last[date] : newDay ? 0 : null;
      last[date] = wh;
      if (from === null || wh <= from) continue;
      // 4 to 15 minutes: a shorter block (the first after the logger started)
      // covers only part of LG's period; a longer one is a gap
      if (lastUpdate !== null && t - lastUpdate >= 4 * MINUTE && t - lastUpdate <= 15 * MINUTE) {
        aircoW.push([lastUpdate, t, ((wh - from) * 3.6e6) / (t - lastUpdate)]);
      }
      lastUpdate = t;
    }
  }
  data = { t0, t1, temps, targets, aircoW, heater: series.heater || [], heaterStart: Date.parse(series.start) };
  $("#note").textContent = airco.lines.length
    ? "Airco: its own reading and target (LG, every 30 s when they change), power from LG's energy total (every 5 minutes). " +
      "Heater: an estimate from its group's S0 meter, per minute; that meter also counts the other devices on the group " +
      "(about 70–150 W with the heater off). The airco's sensor sits high up in its own warm air: while heating it reads several degrees above the room."
    : "No airco data on this Pi (yet).";
  draw();
}

function draw() {
  if (!data) return;
  const svg = $("#heat-chart");
  const w = svg.clientWidth || 800;
  const h = Math.max(360, Math.min(520, Math.round(w * 0.55)));
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("height", h);
  svg.replaceChildren();
  const plotH = (h - TOP - GAP - BOTTOM) / 2;
  const x = (t) => LEFT + ((t - data.t0) / (data.t1 - data.t0)) * (w - LEFT - RIGHT);
  const panels = [
    { top: TOP, unit: "°C", lines: [["targets", "--muted", true], ["temps", "--airco", false]] },
    { top: TOP + plotH + GAP, unit: "W", lines: [] },
  ];

  // value ranges
  const inRange = (t) => t >= data.t0 - 30 * MINUTE;
  const tv = [...data.temps, ...data.targets].filter(([t, v]) => inRange(t) && v != null).map(([, v]) => v);
  const tMin = tv.length ? Math.floor(Math.min(...tv) - 1) : 15, tMax = tv.length ? Math.ceil(Math.max(...tv) + 1) : 30;
  const heaterPts = data.heater.map((v, i) => [data.heaterStart + i * MINUTE, v]).filter(([t]) => t >= data.t0);
  const pv = [...data.aircoW.filter(([a]) => a >= data.t0).map((s) => s[2]), ...heaterPts.map(([, v]) => v ?? 0)];
  const pMax = Math.max(500, ...pv) * 1.1;
  const scales = [[tMin, tMax], [0, pMax]];

  panels.forEach((p, k) => {
    const [lo, hi] = scales[k];
    p.y = (v) => p.top + plotH - ((v - lo) / (hi - lo)) * plotH;
    const step = niceStep(hi - lo);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      svgEl("line", { x1: LEFT, x2: w - RIGHT, y1: p.y(v), y2: p.y(v), class: "gridline" }, svg);
      const label = svgEl("text", { x: LEFT - 6, y: p.y(v) + 4, "text-anchor": "end", class: "axis" }, svg);
      label.textContent = k === 1 && v >= 1000 ? `${v / 1000} kW` : `${v}${k === 0 ? "°" : ""}`;
    }
  });

  // time axis: a line every 1, 2, 3, 6 or 12 hours, whatever leaves room for
  // the labels; midnight dashed
  const pxHour = (w - LEFT - RIGHT) / hours;
  const every = [1, 2, 3, 6, 12].find((n) => n * pxHour >= 64) ?? 12;
  const first = new Date(data.t0); first.setMinutes(0, 0, 0);
  for (let t = first.getTime() + 60 * MINUTE; t <= data.t1; t += 60 * MINUTE) {
    const d = new Date(t);
    if (d.getHours() % every) continue;
    const midnight = d.getHours() === 0;
    for (const p of panels) svgEl("line", { x1: x(t), x2: x(t), y1: p.top, y2: p.top + plotH, class: midnight ? "midnight" : "gridline" }, svg);
    const label = svgEl("text", { x: x(t), y: h - 8, "text-anchor": "middle", class: "axis" }, svg);
    label.textContent = midnight ? d.toLocaleDateString([], { weekday: "short" }) : fmtTime(t);
  }

  // a step line (value holds until the next point); null = a gap
  const stepPath = (pts, y) => {
    let d = "", prev = null;
    for (const [t, v] of pts) {
      if (v == null) { prev = null; continue; }
      const px = Math.max(LEFT, x(t));
      d += prev === null ? `M${px},${y(v)}` : `H${px}V${y(v)}`;
      prev = v;
    }
    return prev === null ? d : d + `H${x(data.t1)}`;
  };
  const top = panels[0], bottom = panels[1];
  svgEl("path", { d: stepPath(data.targets.filter(([t]) => inRange(t)), top.y), fill: "none", stroke: css("--muted"), "stroke-width": 1.5, "stroke-dasharray": "5 4" }, svg);
  svgEl("path", { d: stepPath(data.temps.filter(([t]) => inRange(t)), top.y), fill: "none", stroke: css("--airco"), "stroke-width": 2 }, svg);

  // heater per minute
  let d = "", pen = false;
  for (const [t, v] of heaterPts) {
    if (v == null) { pen = false; continue; }
    d += `${pen ? "L" : "M"}${x(t)},${bottom.y(v)}`;
    pen = true;
  }
  svgEl("path", { d, fill: "none", stroke: css("--heater"), "stroke-width": 1.5 }, svg);
  // airco power: one flat step per LG update
  d = "";
  for (const [a, b, v] of data.aircoW) {
    if (b < data.t0) continue;
    d += `M${Math.max(LEFT, x(a))},${bottom.y(v)}H${x(b)}`;
  }
  svgEl("path", { d, fill: "none", stroke: css("--airco"), "stroke-width": 2.5 }, svg);

  geo = { x, w, h, panels, plotH };
  const cross = svgEl("line", { y1: TOP, y2: h - BOTTOM, class: "zero", visibility: "hidden" }, svg);
  geo.cross = cross;
  $("#legend").innerHTML =
    `<span><i style="background:${css("--airco")}"></i>airco: own reading, power (LG)</span>` +
    `<span><i style="background:${css("--muted")}"></i>airco target</span>` +
    `<span><i style="background:${css("--heater")}"></i>heater group (S0, estimate)</span>`;
}

// value of a step series at time t
const at = (pts, t) => { let v = null; for (const [pt, pv] of pts) { if (pt > t) break; v = pv; } return v; };

function readout(ev) {
  if (!geo || !data) return;
  const box = $("#heat-chart").getBoundingClientRect();
  const px = ((ev.clientX - box.left) / box.width) * geo.w;
  const t = data.t0 + ((px - LEFT) / (geo.w - LEFT - RIGHT)) * (data.t1 - data.t0);
  if (t < data.t0 || t > data.t1) return;
  geo.cross.setAttribute("x1", px); geo.cross.setAttribute("x2", px); geo.cross.setAttribute("visibility", "visible");
  const temp = at(data.temps, t), target = at(data.targets, t);
  const step = data.aircoW.find(([a, b]) => a <= t && t < b);
  const hv = data.heater[Math.floor((t - data.heaterStart) / MINUTE)];
  const parts = [fmtTime(t)];
  if (temp != null) parts.push(`airco reads ${temp} °C`);
  parts.push(target != null ? `target ${target} °C` : "airco off");
  parts.push(step ? `airco ${Math.round(step[2])} W` : "airco –");
  parts.push(hv != null ? `heater ${hv} W` : "heater –");
  const r = $("#readout");
  r.textContent = parts.join(" · ");
  r.classList.add("on");
}

function setRange(h) {
  hours = h;
  for (const b of document.querySelectorAll("#range button")) b.classList.toggle("on", Number(b.dataset.h) === h);
  load();
}

for (const b of document.querySelectorAll("#range button")) b.addEventListener("click", () => setRange(Number(b.dataset.h)));
const chart = $("#heat-chart");
chart.addEventListener("pointerdown", (e) => { chart.setPointerCapture(e.pointerId); readout(e); });
chart.addEventListener("pointermove", (e) => { if (e.buttons || e.pointerType === "mouse") readout(e); });
window.addEventListener("resize", draw);
setRange(12);
setInterval(load, 60 * 1000);
