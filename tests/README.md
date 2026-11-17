# Tests

The server and the page tested together, the way they run on the Pi:
- **Data:** a made-up household writes its log files as the loggers do.
- **Server:** housemon-lite reads them.
- **Browser:** Chromium opens the page and checks what it shows against the
  model behind the made-up data.

Everything runs in containers, on a fake clock in spring 2027.

```sh
tests/run.sh          # needs Docker with Compose; about 5 minutes
```

The same runs on GitHub for every pull request and every push to `main`
(`.github/workflows/tests.yml`). Results and the containers' logs end up in
`tests/run/results/` (on GitHub: the run's `test-results` artifact).

## The made-up household

`fake/world.py`: everything is derived from time alone, so every run sees the
same data and the tests know what the page must show.

- **What's in it:**
  - a base load of computers, a fridge, cooking and lights in the evening
  - an electric heater and an airco used on cold mornings
  - two solar inverters, and gas for hot water
- **The numbers are invented.** The season is spring, around the switch to
  summer time on 2027-03-28 (a 23-hour day).
- **It includes the hard cases:**
  - the S0 logger starts mid-morning on its first day (an incomplete day)
  - the P1 meter comes two days later (days estimated from S0, one partial P1
    day)
  - the S0 node restarts after 7 minutes without power
  - S0 counters wrap at 65536
  - every 37th P1 telegram is damaged

`fake/feed.py` writes the log files in the Pi's layouts as the fake clock
runs: S0 every 3 s, P1 every second, the airco's compact extract every
minute. Older P1 history (more than 40 hours before the start) has one
telegram per 10 s, which keeps the start-up quick.

## The phases

| Phase | Fake time (local) | What is checked |
|---|---|---|
| 1 | 2027-03-29 23:40 | all pages load without JavaScript errors; `config.json` has the local settings; "Last days" (values and "incomplete" marks), the meter readings, P1 today, the S0 pulses today and the graph data equal the model exactly; tiles and tables agree; damaged telegrams are counted; the net-metering band; the heating card |
| 2 | just after midnight | the day rollover: the day that just ended is in "Last days" with the model's values; today starts from zero |
| 3 | 02:30, after a restart of the server | "Last days" is back within 60 s, and no completed day was computed again (the day cache's files are untouched) |

## The fake clock

- **Clock:** the server and the feeder run under `libfaketime`, which reads
  its offset from `run/clock/faketime`. `fake/clock.py` sets it and makes it
  jump between the phases.
- **File times:** Python's `os.stat` does not go through `libfaketime`, so
  `fake/site/sitecustomize.py` shifts the file times Python sees. Each file
  keeps the time it was written at, even after a jump. That matters: the day
  cache judges files by their times.
- **Browser:** the browser gets the same time through Playwright's clock.

## Files

| File | |
|---|---|
| `run.sh` | runs everything: images, clock, the three phases |
| `compose.yaml` | the containers: `feeder`, `server`, `browser` |
| `server.Dockerfile` | Debian Trixie with Python and `libfaketime`, as on the Pi |
| `browser.Dockerfile` | Playwright's image with pytest |
| `local.json` | local settings for the test (made-up net-metering values) |
| `fake/world.py`, `fake/feed.py` | the made-up household and its log files |
| `fake/clock.py`, `fake/site/sitecustomize.py` | the fake clock |
| `browser/` | the tests per phase, `conftest.py` with what they share |
