# CLAUDE.md — housemon-lite

A lite, modern replacement for the live view of the original HouseMon
(JeeLabs, 2013, port 3333): current S0 and P1 readings, updated live, with a
brief highlight on change. See README.md for the design.

## Working agreements (from the owner)

- Everything for this project stays in this folder; test scripts and test
  data live outside the repo (in a separate work folder), unless the repo
  needs them.
- The repo is public: no secrets, hostnames, addresses, serial numbers or
  other home-specific details in it.
- Ask before installing anything and before any `sudo` on the bench Pi;
  ask before starting or changing services on the Pis that run the loggers.
- Commits as `Claude <noreply@anthropic.com>`; stage files by name, never
  `git add -A`; work on a branch and open a PR, the owner merges.
- Answer in English unless the owner writes in Dutch.

## Design rules

- Target: Raspberry Pi 1 (ARMv6, single core, ~430 MB RAM). The server stays
  minimal: Python standard library only, no parsing or heavy work on the Pi.
- The browser does the work: decoding, checks, display. Plain JavaScript and
  CSS, no framework, no build step, no external resources (CDNs, fonts).
- Raw data is the source of truth: the loggers' files are only read, never
  changed; housemon-lite keeps no data of its own.
- Code the owner can read: small files, plain names, comments for the why.

## Facts this builds on (checked 2026-11-02)

- S0 layout as HouseMon's local `pulseNode` driver; 0.5 Wh per pulse
  (validated against P1 and the inverters' counters); HouseMon's power
  formula `18e6/t` was 10x too high, the correct one is `pulseWh*3.6e6/t`.
- P1: DSMR 5.0, one telegram per second, 115200 8N1; CRC16/ARC over "/"
  through "!"; gas (M-Bus) value every 5 minutes.
- Log layouts: jeenode-listener `YYYY/YYYYMMDD.txt` (UTC), p1-logger
  `YYYY/YYYYMMDD/HH.txt` (UTC); both directories are mode 700.
