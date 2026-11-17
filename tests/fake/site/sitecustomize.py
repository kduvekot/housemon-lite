"""
Test containers only: make the file times Python sees follow the fake clock.

libfaketime moves the clock but not os.stat(), so files written "now" would
look months older or newer than the clock says. Each real file time is shifted
by the offset that was in force when it was written (CLOCK_HISTORY: lines
"<real epoch> <offset>", see tests/fake/clock.py), so a clock jump does not
move files written before it.
"""

import os
import time

_HISTORY = os.environ.get("CLOCK_HISTORY")

if _HISTORY:
    _cache = {"read": 0.0, "steps": []}
    _real_time = time.time          # the faked clock; only used to know when to re-read

    def _steps():
        if _real_time() - _cache["read"] > 1.0:
            try:
                with open(_HISTORY) as f:
                    _cache["steps"] = sorted(tuple(map(float, line.split())) for line in f if line.strip())
            except OSError:
                pass
            _cache["read"] = _real_time()
        return _cache["steps"]

    def _offset(real):
        off = 0.0
        for since, value in _steps():
            if real >= since:
                off = value
        return off

    def _shift(r):
        off = _offset(r.st_mtime)
        ns = int(off * 1e9)
        fields = tuple(r)[:7] + (int(r.st_atime + off), int(r.st_mtime + off), int(r.st_ctime + off))
        return os.stat_result(fields, {
            "st_atime": r.st_atime + off, "st_mtime": r.st_mtime + off, "st_ctime": r.st_ctime + off,
            "st_atime_ns": r.st_atime_ns + ns, "st_mtime_ns": r.st_mtime_ns + ns, "st_ctime_ns": r.st_ctime_ns + ns,
            "st_blksize": r.st_blksize, "st_blocks": r.st_blocks, "st_rdev": r.st_rdev,
        })

    _stat, _lstat, _fstat = os.stat, os.lstat, os.fstat
    os.stat = lambda *a, **k: _shift(_stat(*a, **k))
    os.lstat = lambda *a, **k: _shift(_lstat(*a, **k))
    os.fstat = lambda *a, **k: _shift(_fstat(*a, **k))
