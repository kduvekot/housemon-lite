// housemon-lite graph: house, solar and grid in one chart, per-minute averages
// from /api/series; or (graph.html?kind=gas) gas used per hour / 5 minutes as
// bars. Drawn as plain SVG; refreshed once a minute.
//
// Zoom = a wider chart: at zoom 4 the chart is 4x as wide as the card, so a
// quarter of the 36 hours fits on the screen. The strip below the chart shows
// all 36 h with a window for what is on screen; dragging it moves the chart,
// so a drag in the chart itself stays free for reading values. The W axis is
// a separate SVG on the left, so it stays in place. The figures below the
// chart cover what is on screen.
"use strict";

const $ = (sel) => document.querySelector(sel);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const state = { show: { house: true, solar: true, grid: true }, zoom: 1 };
let data = null;

// graph.html?kind=gas: gas used (m³) per hour or per 5 minutes, as bars,
// instead of house / solar / grid power
const GAS = new URLSearchParams(location.search).get("kind") === "gas";
const UNIT = GAS ? "m³" : "W";
const fmtValue = (v) => (GAS ? v.toFixed(v < 0.1 ? 3 : 2) : String(Math.round(v)));

const NAMES = { house: "House", solar: "Solar", grid: "Grid", gas: "Gas" };
const COLORS = { house: "--house", solar: "--solar", gas: "--gas" };
const ZOOMS = [1, 2, 3, 6, 12, 24];          // 36 h, 18 h, 12 h, 6 h, 3 h, 1.5 h on screen
const AXIS_W = 58, TOP = 12, BOTTOM = 30, RIGHT = 10;   // px
const MINUTE = 60 * 1000;

const svgEl = (tag, attrs, parent) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.append(e);
  return e;
};
const fmtTime = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
// a round step for the W axis: 1, 2, 2.5 or 5 times a power of ten, at most 5 lines
const niceStep = (span) => {
  const p = 10 ** Math.floor(Math.log10(Math.max(span, 1e-3) / 4));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => span / s <= 5);
};

// Grid value per minute: P1 when there is one, else the S0 estimate.
const gridValue = (i) => data.grid[i] ?? data.gridEstimate[i];
const value = (k, i) => (k === "grid" ? gridValue(i) : data[k][i]);

// Gas: the meter gives a reading every 5 minutes. Each pair of readings is a
// period with the m³ used in it; gaps of more than 30 min stay empty.
function gasPeriods(d) {
  const out = [], pts = d.gas || [];
  for (let p = 1; p < pts.length; p++) {
    const [ta, ma] = pts[p - 1], [tb, mb] = pts[p];
    if (tb - ta <= 30 * MINUTE) out.push({ ta, tb, m3: mb - ma });
  }
  return out;
}

// Bars: per 5 minutes, the meter's own rhythm (per hour was too coarse).
const barMinutes = () => 5;

// Use per bar of `size` minutes from the chart start: { i0, i1, m3 } with i0
// and i1 in minutes; m3 is null where no period covers the bar. A period
// that spans two bars is shared in proportion to time.
function gasBars(size) {
  const n = data.house.length, t0 = Date.parse(data.start), bars = [];
  for (let i = 0; i < n; i += size) bars.push({ i0: i, i1: Math.min(n - 1, i + size), m3: null });
  for (const { ta, tb, m3 } of data.gasPeriods) {
    const a = (ta - t0) / MINUTE, b = (tb - t0) / MINUTE;
    for (let k = Math.max(0, Math.floor(a / size)); k < bars.length && k * size < b; k++) {
      const overlap = Math.min(b, (k + 1) * size) - Math.max(a, k * size);
      if (overlap > 0) bars[k].m3 = (bars[k].m3 ?? 0) + (m3 * overlap) / (b - a);
    }
  }
  return bars;
}
const estimated = (i) => data.grid[i] == null && data.gridEstimate[i] != null;

// Sizes and scales of the current drawing, shared by drawing, scrolling and the crosshair.
let geo = null;

function draw() {
  const scroll = $("#scroll"), chart = $("#chart"), yaxis = $("#yaxis");
  // visible width in px: the chart area minus the W axis (not the scrolling
  // box itself, which can measure too small before the page is laid out)
  const view = Math.max(120, $(".chart-wrap").clientWidth - AXIS_W);
  const width = Math.round(view * state.zoom);            // whole chart in px
  const H = Math.round(Math.min(380, Math.max(220, view * 0.45)));
  const n = data.house.length, t0 = Date.parse(data.start);
  const keys = GAS ? ["gas"] : ["house", "solar", "grid"].filter((k) => state.show[k]);

  // value range over all 36 h (so it does not jump while scrolling); 0 always in view
  const bars = GAS ? gasBars(barMinutes()) : null;
  let lo = 0, hi = GAS ? 0.01 : 100;
  if (GAS) for (const b of bars) hi = Math.max(hi, b.m3 ?? 0);
  else for (const k of keys) for (let i = 0; i < n; i++) {
    const v = value(k, i);
    if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  }
  const stepW = niceStep(hi - lo);
  lo = Math.floor(lo / stepW) * stepW;
  hi = Math.ceil(hi / stepW) * stepW;
  const x = (i) => 4 + (i / Math.max(1, n - 1)) * (width - 4 - RIGHT);
  const y = (v) => TOP + ((hi - v) / (hi - lo)) * (H - TOP - BOTTOM);
  geo = { view, width, H, n, t0, x, y, keys, bars };

  for (const [el, w] of [[chart, width], [yaxis, AXIS_W]]) {
    el.replaceChildren();
    el.setAttribute("width", w);
    el.setAttribute("height", H);
    el.setAttribute("viewBox", `0 0 ${w} ${H}`);
  }

  // value axis (fixed, left) and its grid lines (in the chart); as many
  // decimals as the step needs (0.05 -> 2), so the labels fit the narrow axis
  let digits = Math.max(0, -Math.floor(Math.log10(stepW) + 1e-9));
  while (digits < 4 && Math.abs(stepW * 10 ** digits - Math.round(stepW * 10 ** digits)) > 1e-6) digits++;   // 0.025 -> 3
  for (let v = lo; v <= hi + 1e-9; v += stepW) {
    svgEl("text", { x: AXIS_W - 6, y: y(v) + 4, "text-anchor": "end", class: "axis" }, yaxis).textContent = `${v.toFixed(digits)} ${UNIT}`;
    svgEl("line", { x1: 0, x2: width, y1: y(v), y2: y(v), class: v === 0 ? "zero" : "gridline" }, chart);
  }

  // time labels: the smallest step that keeps them at least ~70 px apart;
  // midnight (local) as a dashed line with the date
  const pxPerMin = (width - 4 - RIGHT) / Math.max(1, n - 1);
  const every = [15, 30, 60, 120, 180, 360, 720].find((m) => m * pxPerMin >= 70) ?? 720;
  for (let i = 0; i < n; i++) {
    const t = new Date(t0 + i * MINUTE);
    const minOfDay = t.getHours() * 60 + t.getMinutes();
    if (minOfDay === 0) {
      svgEl("line", { x1: x(i), x2: x(i), y1: TOP, y2: H - BOTTOM, class: "midnight" }, chart);
      svgEl("text", { x: x(i) + 4, y: TOP + 12, class: "axis" }, chart).textContent =
        t.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
    }
    if (minOfDay % every === 0)
      svgEl("text", { x: x(i), y: H - 10, "text-anchor": "middle", class: "axis" }, chart).textContent = fmtTime(t);
  }

  // A line only breaks where data is missing. `keep` picks the minutes of one
  // style (estimated or not); where the style changes, the next part starts
  // at the last point of the previous one, so the parts connect.
  const pathData = (k, keep) => {
    let d = "", pen = false;
    for (let i = 0; i < n; i++) {
      const v = value(k, i);
      if (v == null) { pen = false; continue; }
      if (!keep(i)) {
        if (pen) d += `L${x(i).toFixed(1)},${y(v).toFixed(1)}`;   // finish into the next part
        pen = false;
        continue;
      }
      if (!pen && i > 0 && value(k, i - 1) != null) {               // start from the previous point
        d += `M${x(i - 1).toFixed(1)},${y(value(k, i - 1)).toFixed(1)}`;
        pen = true;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    }
    return d;
  };
  const stroke = (d, color, dash, clip) => {
    if (d) svgEl("path", { d, fill: "none", stroke: css(color), "stroke-width": 1.6,
                           "stroke-dasharray": dash || "", "clip-path": clip ? `url(#${clip})` : "" }, chart);
  };

  // Grid is one line, red above 0 (taken) and green below (delivered): drawn
  // twice, each copy cut off at the zero line. Dashed where estimated from S0.
  const defs = svgEl("defs", {}, chart);
  svgEl("rect", { x: 0, y: 0, width, height: y(0) }, svgEl("clipPath", { id: "above" }, defs));
  svgEl("rect", { x: 0, y: y(0), width, height: H - y(0) }, svgEl("clipPath", { id: "below" }, defs));
  // gas: one bar per hour or per 5 minutes, with a small gap between bars
  if (GAS) for (const b of bars) {
    if (b.m3 == null) continue;
    const x0 = x(b.i0), w = Math.max(1, x(b.i1) - x0 - (x(b.i1) - x0 > 4 ? 1 : 0));
    svgEl("rect", { x: x0, y: y(b.m3), width: w, height: Math.max(0, y(0) - y(b.m3)), fill: css("--gas") }, chart);
  }
  for (const k of keys) {
    if (k === "gas") continue;
    if (k !== "grid") { stroke(pathData(k, () => true), COLORS[k]); continue; }
    for (const [clip, color] of [["above", "--in"], ["below", "--out"]]) {
      stroke(pathData(k, (i) => !estimated(i)), color, "", clip);
      stroke(pathData(k, (i) => estimated(i)), color, "4 3", clip);
    }
  }

  // a vertical line at the minute being read; its values go in the line above the chart
  geo.cross = svgEl("line", { y1: TOP, y2: H - BOTTOM, stroke: css("--muted"), visibility: "hidden" }, chart);
  chart.onpointermove = showValues;
  chart.onpointerdown = showValues;

  $("#legend").innerHTML = keys.map((k) => k === "grid"
    ? `<span><i style="background:${css("--in")}"></i>grid in</span><span><i style="background:${css("--out")}"></i>grid out</span><span>- - - estimated from S0</span>`
    : k === "gas"
    ? `<span><i style="background:${css(COLORS[k])}"></i>Gas used, m³ per ${barMinutes() === 60 ? "hour" : "5 minutes"}</span>`
    : `<span><i style="background:${css(COLORS[k])}"></i>${NAMES[k]}</span>`).join("");
  for (const b of document.querySelectorAll("#series button")) b.classList.toggle("on", state.show[b.dataset.s]);
  $("#zoom-label").textContent = `${36 / state.zoom} h`;
  drawNav();
  updateStats();
}

// The strip below the chart: all 36 h in miniature, with a window for the
// part on screen. Dragging or tapping it moves the chart.
function drawNav() {
  const nav = $("#nav");
  const w = nav.clientWidth, h = 44, { n, keys } = geo;
  nav.replaceChildren();
  nav.setAttribute("viewBox", `0 0 ${w} ${h}`);
  if (GAS) {                                      // gas: the 36 hours as small hourly bars
    const hourly = gasBars(60), top = Math.max(0.01, ...hourly.map((b) => b.m3 ?? 0));
    for (const b of hourly) if (b.m3) {
      const bx = (b.i0 / Math.max(1, n - 1)) * w, bw = ((b.i1 - b.i0) / Math.max(1, n - 1)) * w;
      svgEl("rect", { x: bx, y: h - 3 - (b.m3 / top) * (h - 6), width: Math.max(1, bw - 1),
                      height: (b.m3 / top) * (h - 6), fill: css("--gas"), opacity: 0.7 }, nav);
    }
    geo.window = svgEl("rect", { class: "window", y: 1, height: h - 2, rx: 3 }, nav);
    geo.navWidth = w;
    placeWindow();
    return;
  }
  let lo = 0, hi = 1;
  for (const k of keys) for (let i = 0; i < n; i += 5) {
    const v = value(k, i);
    if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  }
  const nx = (i) => (i / Math.max(1, n - 1)) * w, ny = (v) => 3 + ((hi - v) / (hi - lo)) * (h - 6);
  for (const k of keys) {                         // every 5th minute is detailed enough here
    let d = "", pen = false;
    for (let i = 0; i < n; i += 5) {
      const v = value(k, i);
      if (v == null) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${nx(i).toFixed(1)},${ny(v).toFixed(1)}`;
      pen = true;
    }
    if (d) svgEl("path", { d, fill: "none", stroke: css(k === "grid" ? "--in" : COLORS[k]), "stroke-width": 1, opacity: 0.7 }, nav);
  }
  geo.window = svgEl("rect", { class: "window", y: 1, height: h - 2, rx: 3 }, nav);
  geo.navWidth = w;
  placeWindow();
}

function placeWindow() {
  const left = $("#scroll").scrollLeft;
  geo.window.setAttribute("x", (left / geo.width) * geo.navWidth);
  geo.window.setAttribute("width", Math.max(6, (geo.view / geo.width) * geo.navWidth));
}

// Move the chart so the window is centred on strip position `px`.
function scrollToNav(px) {
  const scroll = $("#scroll");
  scroll.scrollLeft = (px / geo.navWidth) * geo.width - geo.view / 2;
  placeWindow();
  updateStats();
}

function showValues(ev) {
  const { x, n, t0, keys, cross } = geo;
  const r = $("#chart").getBoundingClientRect();
  const px = ev.clientX - r.left;                      // position in the whole (wide) chart
  const i = Math.round(((px - 4) / (geo.width - 4 - RIGHT)) * (n - 1));
  if (i < 0 || i >= n) return;
  cross.setAttribute("x1", x(i));
  cross.setAttribute("x2", x(i));
  cross.setAttribute("visibility", "visible");
  const readout = $("#readout");
  readout.classList.add("on");
  if (GAS) {                                       // the bar under the finger: its period and m³
    const b = geo.bars.find((bar) => i >= bar.i0 && i < bar.i1) ?? geo.bars[geo.bars.length - 1];
    readout.textContent = `${fmtTime(t0 + b.i0 * MINUTE)}–${fmtTime(t0 + b.i1 * MINUTE)} · Gas ` +
      (b.m3 == null ? "no data" : `${fmtValue(b.m3)} m³`);
    return;
  }
  readout.textContent = `${fmtTime(t0 + i * MINUTE)} · ` + keys.map((k) => {
    const v = value(k, i);
    return `${NAMES[k]} ${v == null ? "–" : (k === "grid" && estimated(i) ? "≈" : "") + fmtValue(v) + " " + UNIT}`;
  }).join(" · ");
  readout.classList.add("on");
}

// kWh / average / maximum per line over the part that is on screen
function updateStats() {
  const { n, width, view, t0, keys } = geo;
  const left = $("#scroll").scrollLeft;
  const toIndex = (px) => Math.min(n - 1, Math.max(0, Math.round(((px - 4) / (width - 4 - RIGHT)) * (n - 1))));
  const a = toIndex(left), b = toIndex(left + view);
  const span = `${fmtTime(t0 + a * MINUTE)}–${fmtTime(t0 + b * MINUTE)}`;
  if (GAS) {                                       // gas: total of the bars on screen, and the largest
    const shown = geo.bars.filter((bar) => bar.m3 != null && (bar.i0 + bar.i1) / 2 >= a && (bar.i0 + bar.i1) / 2 <= b);
    const total = shown.reduce((s, bar) => s + bar.m3, 0);
    const most = shown.reduce((m, bar) => Math.max(m, bar.m3), 0);
    const per = barMinutes() === 60 ? "hour" : "5 minutes";
    $("#stats").innerHTML = shown.length ? `<div class="stat"><small>Gas · ${span}</small><br><b>${total.toFixed(3)} m³</b>` +
      `<br><small>most in ${per === "hour" ? "one hour" : "5 minutes"}: ${fmtValue(most)} m³</small></div>` : "";
    return;
  }
  $("#stats").innerHTML = keys.map((k) => {
    const v = [];
    for (let i = a; i <= b; i++) if (value(k, i) != null) v.push(value(k, i));
    if (!v.length) return "";
    const sum = v.reduce((p, q) => p + q, 0);     // per-minute values: sum / 60 = amount
    const amount = GAS ? `${(sum / 60).toFixed(3)} m³` : `${(sum / 60 / 1000).toFixed(2)} kWh`;
    return `<div class="stat"><small>${NAMES[k]} · ${span}</small><br><b>${amount}</b>` +
           `<br><small>avg ${fmtValue(sum / v.length)} ${UNIT} · max ${fmtValue(Math.max(...v))} ${UNIT}</small></div>`;
  }).join("");
}

// Zoom around the middle of what is on screen (or jump to the end).
function setZoom(zoom, atEnd) {
  const scroll = $("#scroll");
  const center = geo ? (scroll.scrollLeft + geo.view / 2) / geo.width : 1;
  state.zoom = zoom;
  draw();
  scroll.scrollLeft = atEnd ? geo.width : center * geo.width - geo.view / 2;
  placeWindow();
  updateStats();
}

async function load() {
  try {
    const fresh = await (await fetch("api/series?hours=36", { cache: "no-store" })).json();
    if (GAS) fresh.gasPeriods = gasPeriods(fresh);
    const empty = GAS ? !fresh.gasPeriods.length
                      : !fresh.house.some((v) => v != null) && !fresh.grid.some((v) => v != null);
    if (empty) {
      $("#note").textContent = "The Pi is starting up and loading its figures; the graph follows within a few minutes.";
      return;
    }
    // follow the newest data if the right edge was on screen, else keep the place
    const scroll = $("#scroll");
    const atEnd = !geo || scroll.scrollLeft + geo.view >= geo.width - 2;
    const left = scroll.scrollLeft;
    data = fresh;
    draw();
    scroll.scrollLeft = atEnd ? geo.width : left;
    placeWindow();
    updateStats();
    $("#note").textContent = GAS
      ? `Gas used in m³ per 5 minutes, from the meter's readings (one every 5 minutes). ` +
        `Updated every minute (last: ${fmtTime(Date.now())}). ` +
        "Zoom with − and +; drag the strip below the chart to move; drag in the chart to read values. " +
        "The figures below cover what is on screen."
      : `Per-minute averages, updated every minute (last: ${fmtTime(Date.now())}). ` +
      "Grid above 0 = taken from the grid, below 0 = delivered; dashed = estimated from S0 where there is no P1 data. " +
      "Zoom with − and +; drag the strip below the chart to move; drag in the chart to read values. " +
      "The figures below cover what is on screen.";
  } catch (e) {
    $("#note").textContent = "Could not load the data; trying again in a minute.";
  }
}

$("#series").onclick = (e) => {
  const k = e.target.dataset.s;
  if (!k) return;
  state.show[k] = !state.show[k];
  if (!Object.values(state.show).some(Boolean)) state.show[k] = true;   // keep at least one line
  if (data) setZoom(state.zoom);
};
$("#zoom").onclick = (e) => {
  const z = e.target.dataset.z;
  if (!z || !data) return;
  const at = ZOOMS.indexOf(state.zoom);
  if (z === "in") setZoom(ZOOMS[Math.min(ZOOMS.length - 1, at + 1)]);
  if (z === "out") setZoom(ZOOMS[Math.max(0, at - 1)]);
  if (z === "6h") setZoom(6, true);
  if (z === "all") setZoom(1);
};
let pending = false;
$("#scroll").addEventListener("scroll", () => {          // stats follow the scrolling, once per frame
  if (pending || !geo) return;
  pending = true;
  requestAnimationFrame(() => { pending = false; placeWindow(); updateStats(); });
});
// the strip: drag or tap to move through the 36 hours
let dragging = false;
const navX = (ev) => ev.clientX - $("#nav").getBoundingClientRect().left;
$("#nav").addEventListener("pointerdown", (ev) => {
  if (!geo) return;
  dragging = true;
  $("#nav").setPointerCapture(ev.pointerId);
  scrollToNav(navX(ev));
});
$("#nav").addEventListener("pointermove", (ev) => { if (dragging) scrollToNav(navX(ev)); });
$("#nav").addEventListener("pointerup", () => { dragging = false; });
$("#nav").addEventListener("pointercancel", () => { dragging = false; });
// a trackpad's sideways scroll, or Shift + mouse wheel, over the chart moves it too
$("#scroll").addEventListener("wheel", (ev) => {
  const dx = ev.shiftKey ? ev.deltaY : ev.deltaX;
  if (!dx || Math.abs(ev.deltaX) < Math.abs(ev.deltaY) && !ev.shiftKey) return;
  ev.preventDefault();
  $("#scroll").scrollLeft += dx;
}, { passive: false });
// Redraw when the chart area changes width: a first measurement before the
// page was laid out, turning the phone, the browser bar coming and going.
let lastWidth = 0;
new ResizeObserver(() => {
  const w = $(".chart-wrap").clientWidth;
  if (data && w && Math.abs(w - lastWidth) > 2) { lastWidth = w; setZoom(state.zoom); }
}).observe($(".chart-wrap"));
matchMedia("(prefers-color-scheme: dark)").onchange = () => data && setZoom(state.zoom);
if (GAS) {
  $("#series").style.display = "none";  // one line only (the hidden attribute loses to .seg's CSS)
  document.title = "housemon-lite · gas";
}
load();
setInterval(load, 60 * 1000);
