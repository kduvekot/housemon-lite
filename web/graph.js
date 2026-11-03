// housemon-lite graph: house, solar and grid in one chart, per-minute averages
// from /api/series. Drawn as plain SVG; refreshed once a minute.
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

// graph.html?kind=gas: the gas flow instead of house / solar / grid
const GAS = new URLSearchParams(location.search).get("kind") === "gas";
const UNIT = GAS ? "m³/h" : "W";
const fmtValue = (v) => (GAS ? v.toFixed(v < 1 ? 2 : 1) : String(Math.round(v)));

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
const value = (k, i) => (k === "grid" ? gridValue(i) : k === "gas" ? data.gasRate[i] : data[k][i]);

// Gas: the meter gives a reading every 5 minutes; spread each interval's use
// over its minutes as a flow in m³/h (m³ in the interval / its length in hours).
function gasRates(d) {
  const n = d.house.length, t0 = Date.parse(d.start), rate = new Array(n).fill(null);
  const pts = d.gas || [];
  for (let p = 1; p < pts.length; p++) {
    const [ta, ma] = pts[p - 1], [tb, mb] = pts[p];
    if (tb - ta > 30 * MINUTE) continue;           // a gap in the data: leave it empty
    const r = (mb - ma) / ((tb - ta) / 3.6e6);
    for (let i = Math.max(0, Math.ceil((ta - t0) / MINUTE)); i < Math.min(n, Math.ceil((tb - t0) / MINUTE)); i++) rate[i] = r;
  }
  return rate;
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
  let lo = 0, hi = GAS ? 0.5 : 100;
  for (const k of keys) for (let i = 0; i < n; i++) {
    const v = value(k, i);
    if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  }
  const stepW = niceStep(hi - lo);
  lo = Math.floor(lo / stepW) * stepW;
  hi = Math.ceil(hi / stepW) * stepW;
  const x = (i) => 4 + (i / Math.max(1, n - 1)) * (width - 4 - RIGHT);
  const y = (v) => TOP + ((hi - v) / (hi - lo)) * (H - TOP - BOTTOM);
  geo = { view, width, H, n, t0, x, y, keys };

  for (const [el, w] of [[chart, width], [yaxis, AXIS_W]]) {
    el.replaceChildren();
    el.setAttribute("width", w);
    el.setAttribute("height", H);
    el.setAttribute("viewBox", `0 0 ${w} ${H}`);
  }

  // W axis (fixed, left) and its grid lines (in the chart)
  for (let v = lo; v <= hi + 1e-9; v += stepW) {
    svgEl("text", { x: AXIS_W - 6, y: y(v) + 4, "text-anchor": "end", class: "axis" }, yaxis).textContent = `${fmtValue(v)} ${UNIT}`;
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
  for (const k of keys) {
    if (k !== "grid") { stroke(pathData(k, () => true), COLORS[k]); continue; }
    for (const [clip, color] of [["above", "--in"], ["below", "--out"]]) {
      stroke(pathData(k, (i) => !estimated(i)), color, "", clip);
      stroke(pathData(k, (i) => estimated(i)), color, "4 3", clip);
    }
  }

  // crosshair with the values of one minute, on tap or hover
  geo.cross = svgEl("line", { y1: TOP, y2: H - BOTTOM, stroke: css("--muted"), visibility: "hidden" }, chart);
  geo.tip = svgEl("text", { class: "tip", y: TOP + 28, visibility: "hidden" }, chart);
  chart.onpointermove = showValues;
  chart.onpointerdown = showValues;

  $("#legend").innerHTML = keys.map((k) => k === "grid"
    ? `<span><i style="background:${css("--in")}"></i>grid in</span><span><i style="background:${css("--out")}"></i>grid out</span><span>- - - estimated from S0</span>`
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
  const { x, n, t0, keys, cross, tip, view } = geo;
  const r = $("#chart").getBoundingClientRect();
  const px = ev.clientX - r.left;                      // position in the whole (wide) chart
  const i = Math.round(((px - 4) / (geo.width - 4 - RIGHT)) * (n - 1));
  if (i < 0 || i >= n) return;
  cross.setAttribute("x1", x(i));
  cross.setAttribute("x2", x(i));
  cross.setAttribute("visibility", "visible");
  tip.textContent = `${fmtTime(t0 + i * MINUTE)} · ` + keys.map((k) => {
    const v = value(k, i);
    return `${NAMES[k]} ${v == null ? "–" : (k === "grid" && estimated(i) ? "≈" : "") + fmtValue(v) + " " + UNIT}`;
  }).join(" · ");
  // keep the text inside the visible part of the chart
  const left = $("#scroll").scrollLeft;
  tip.setAttribute("x", Math.min(Math.max(x(i) + 8, left + 4), left + view - 330));
  tip.setAttribute("visibility", "visible");
}

// kWh / average / maximum per line over the part that is on screen
function updateStats() {
  const { n, width, view, t0, keys } = geo;
  const left = $("#scroll").scrollLeft;
  const toIndex = (px) => Math.min(n - 1, Math.max(0, Math.round(((px - 4) / (width - 4 - RIGHT)) * (n - 1))));
  const a = toIndex(left), b = toIndex(left + view);
  const span = `${fmtTime(t0 + a * MINUTE)}–${fmtTime(t0 + b * MINUTE)}`;
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
    if (GAS) fresh.gasRate = gasRates(fresh);
    const empty = GAS ? !fresh.gasRate.some((v) => v != null)
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
      ? `Gas flow in m³/h from the meter's readings (one every 5 minutes), updated every minute (last: ${fmtTime(Date.now())}). ` +
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
  $("#series").hidden = true;          // one line only
  document.title = "housemon-lite · gas";
}
load();
setInterval(load, 60 * 1000);
