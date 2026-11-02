# housemon-lite

A lite version of the housemon: a small, modern live view of the energy data
that [jeenode-listener](https://github.com/kduvekot/jeenode-listener) (S0 pulse
meters over RF12) and [p1-logger](https://github.com/kduvekot/p1-logger)
(P1 smart-meter telegrams) collect. Made to run on a Raspberry Pi 1.

Current readings (W) updated live, today so far (kWh, from midnight Dutch
time) and the last 7 days; a value lights up briefly when it changes, as in
the original HouseMon. No graphs or admin yet.

## How it works

```
loggers ──> raw log files ──> housemon_lite.py ──SSE──> browser (web/app.js)
            (unchanged)        reads new bytes            decodes, checks,
                               once a second               displays
```

- **The Pi does almost nothing.** `housemon_lite.py` (Python standard library
  only) checks once a second whether the log files have grown, keeps the
  newest S0 line per RF12 node and the newest complete P1 telegram, and pushes
  them unchanged over Server-Sent Events (`/events`). `/api/latest` returns the
  same as JSON.
- **Per day** (`daystats.py`, local days, `--tz`, default Europe/Amsterdam):
  for P1 only the meter reading at midnight and before the next midnight are
  needed (the first or last few KB of one hourly file); S0 pulses are added up
  line by line, across node restarts and counter wraps. Today is counted as
  the lines come in; a completed day is computed once and kept in memory.
  Days the logging did not cover completely are marked.
- **The browser does the work**: decoding the S0 packets, parsing the P1
  telegram including its CRC check (damaged telegrams are skipped), and the
  display. Plain JavaScript and CSS, no framework, no build step.

## S0 decoding

Same packet layout as HouseMon's `pulseNode` driver: after the header byte,
8 channels of two little-endian 16-bit values, a pulse counter and the time
between the last two pulses (milliseconds; above 60000, `t - 60000` seconds).

- energy: counter x `pulseWh` (0.5 Wh per pulse, 2000 pulses/kWh); the
  counter starts at 0 when the node powers up and wraps at 65536;
- power: `pulseWh x 3,600,000 / t` W. HouseMon used `18,000,000 / t`, ten
  times too high for 0.5 Wh pulses (checked against the P1 meter and the
  inverters' own counters);
- when no new pulse has arrived for longer than `t`, the real power must be
  lower, so the page uses the time since the last change instead.

Channel names, which channels count as house or solar, the pulse value and
the RF12 node id are in `web/config.json`.

## Install

```sh
sudo install -d /opt/housemon-lite/web
sudo install -m 0644 housemon_lite.py daystats.py /opt/housemon-lite/
sudo install -m 0644 web/* /opt/housemon-lite/web/
sudo install -m 0644 housemon-lite.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now housemon-lite.service
```

Then open `http://<pi>:3333/`. The service runs as a temporary user that can
only read files (`DynamicUser`, `CAP_DAC_READ_SEARCH`), so the loggers'
private directories stay as they are.

Options: `housemon_lite.py --help` (port, bind address, log directories,
time zone of the day boundary).

## Files

| File | |
|---|---|
| `housemon_lite.py` | the server |
| `daystats.py` | per-day figures (midnight readings, S0 pulse totals) |
| `web/index.html`, `web/style.css` | the page (light and dark theme) |
| `web/app.js` | decoding and display |
| `web/config.json` | channel names and settings |
| `housemon-lite.service` | systemd unit |
