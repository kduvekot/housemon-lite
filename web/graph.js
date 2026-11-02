// housemon-lite graph: house, solar and grid in one chart, per-minute averages
// from /api/series. Drawn as plain SVG; refreshed once a minute.
"use strict";

const $ = (sel) => document.querySelector(sel);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const params = new URLSearchParams(location.search);
const state = { show: { house: true, solar: true, grid: true }, hours: Number(params.get("hours")) || 36 };
let data = null;

const NAMES = { house: "House", solar: "Solar", grid: "Grid" };
const COLORS = { house: "--house", solar: "--solar" };
const W = 1000, H = 380, L = 54, R = 10, TOP = 12, B = 30;   // chart size and margins

const svgEl = (tag, attrs, parent) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.append(e);
  return e;
};
const fmtTime = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
// a round step for the W axis: 1, 2, 2.5 or 5 times a power of ten, at most 5 lines
const niceStep = (span) => {
  const p = 10 ** Math.floor(Math.log10(Math.max(span, 1) / 4));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => span / s <= 5);
};

// Grid value per minute: P1 when there is one, else the S0 estimate.
const gridValue = (i) => data.grid[i] ?? data.gridEstimate[i];
const value = (k, i) => (k === "grid" ? gridValue(i) : data[k][i]);
const estimated = (i) => data.grid[i] == null && data.gridEstimate[i] != null;

function draw() {
  const svg = $("#chart");
  svg.replaceChildren();
  const n = data.house.length;
  const t0 = Date.parse(data.start), step = data.step * 1000;
  const i0 = Math.max(0, n - state.hours * 60);
  const keys = ["house", "solar", "grid"].filter((k) => state.show[k]);

  // value range, rounded to whole steps; 0 is always in view
  let lo = 0, hi = 100;
  for (const k of keys) for (let i = i0; i < n; i++) {
    const v = value(k, i);
    if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  }
  const stepW = niceStep(hi - lo);
  lo = Math.floor(lo / stepW) * stepW;
  hi = Math.ceil(hi / stepW) * stepW;
  const x = (i) => L + ((i - i0) / Math.max(1, n - 1 - i0)) * (W - L - R);
  const y = (v) => TOP + ((hi - v) / (hi - lo)) * (H - TOP - B);

  for (let v = lo; v <= hi + 1e-9; v += stepW) {
    svgEl("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: v === 0 ? "zero" : "gridline" }, svg);
    svgEl("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", class: "axis" }, svg).textContent = `${Math.round(v)} W`;
  }

  // time axis: labels every 1 or 6 hours; midnight (local) as a dashed line with the date
  const every = state.hours <= 6 ? 1 : 6;
  for (let i = i0; i < n; i++) {
    const t = new Date(t0 + i * step);
    if (t.getMinutes() !== 0) continue;
    if (t.getHours() === 0) {
      svgEl("line", { x1: x(i), x2: x(i), y1: TOP, y2: H - B, class: "midnight" }, svg);
      svgEl("text", { x: x(i) + 4, y: TOP + 12, class: "axis" }, svg).textContent =
        t.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
    }
    if (t.getHours() % every === 0)
      svgEl("text", { x: x(i), y: H - 10, "text-anchor": "middle", class: "axis" }, svg).textContent = fmtTime(t);
  }

  // one path per run of known values; grid: red when taken, green when
  // delivered, dashed where estimated from S0
  const line = (k, keep, color, dash) => {
    let d = "", pen = false;
    for (let i = i0; i < n; i++) {
      const v = value(k, i);
      if (v == null || !keep(i, v)) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    }
    if (d) svgEl("path", { d, fill: "none", stroke: css(color), "stroke-width": 1.6, "stroke-dasharray": dash || "" }, svg);
  };
  for (const k of keys) {
    if (k !== "grid") { line(k, () => true, COLORS[k]); continue; }
    line(k, (i, v) => v >= 0 && !estimated(i), "--in");
    line(k, (i, v) => v <= 0 && !estimated(i), "--out");
    line(k, (i, v) => v >= 0 && estimated(i), "--in", "4 3");
    line(k, (i, v) => v <= 0 && estimated(i), "--out", "4 3");
  }

  // crosshair with the values of one minute, on tap or hover
  const cross = svgEl("line", { y1: TOP, y2: H - B, stroke: css("--muted"), visibility: "hidden" }, svg);
  const tip = svgEl("text", { class: "tip", y: TOP + 28, visibility: "hidden" }, svg);
  const show = (ev) => {
    const r = svg.getBoundingClientRect();
    const i = Math.round(i0 + (((ev.clientX - r.left) / r.width) * W - L) / (W - L - R) * (n - 1 - i0));
    if (i < i0 || i >= n) return;
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    cross.setAttribute("visibility", "visible");
    const parts = keys.map((k) => {
      const v = value(k, i);
      return `${NAMES[k]} ${v == null ? "–" : (k === "grid" && estimated(i) ? "≈" : "") + Math.round(v) + " W"}`;
    });
    tip.textContent = `${fmtTime(t0 + i * step)} · ${parts.join(" · ")}`;
    tip.setAttribute("x", Math.min(Math.max(x(i) + 8, L), W - 330));
    tip.setAttribute("visibility", "visible");
  };
  svg.onpointermove = show;
  svg.onpointerdown = show;

  // legend and kWh / average / maximum per line over the chosen period
  $("#legend").innerHTML = keys.map((k) => k === "grid"
    ? `<span><i style="background:${css("--in")}"></i>grid in</span><span><i style="background:${css("--out")}"></i>grid out</span><span>- - - estimated from S0</span>`
    : `<span><i style="background:${css(COLORS[k])}"></i>${NAMES[k]}</span>`).join("");
  $("#stats").innerHTML = keys.map((k) => {
    const v = [];
    for (let i = i0; i < n; i++) if (value(k, i) != null) v.push(value(k, i));
    if (!v.length) return "";
    const sum = v.reduce((a, b) => a + b, 0);
    return `<div class="stat"><small>${NAMES[k]} · ${state.hours} h</small><br><b>${(sum / 60 / 1000).toFixed(1)} kWh</b>` +
           `<br><small>avg ${Math.round(sum / v.length)} W · max ${Math.round(Math.max(...v))} W</small></div>`;
  }).join("");
  for (const b of document.querySelectorAll("#series button")) b.classList.toggle("on", state.show[b.dataset.s]);
  for (const b of document.querySelectorAll("#range button")) b.classList.toggle("on", Number(b.dataset.h) === state.hours);
}

async function load() {
  try {
    data = await (await fetch(`api/series?hours=36`, { cache: "no-store" })).json();
    $("#note").textContent = `Per-minute averages, updated every minute (last: ${fmtTime(Date.now())}). ` +
      "Grid above 0 = taken from the grid, below 0 = delivered; dashed = estimated from S0 where there is no P1 data.";
    draw();
  } catch (e) {
    $("#note").textContent = "Could not load the data; trying again in a minute.";
  }
}

$("#series").onclick = (e) => {
  const k = e.target.dataset.s;
  if (!k) return;
  state.show[k] = !state.show[k];
  if (!Object.values(state.show).some(Boolean)) state.show[k] = true;   // keep at least one line
  if (data) draw();
};
$("#range").onclick = (e) => {
  if (!e.target.dataset.h) return;
  state.hours = Number(e.target.dataset.h);
  if (data) draw();
};
matchMedia("(prefers-color-scheme: dark)").onchange = () => data && draw();
load();
setInterval(load, 60 * 1000);
