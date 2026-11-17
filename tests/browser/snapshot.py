"""Records the day cache's file times before the server restarts (phase 3 compares them)."""

import json
from pathlib import Path

times = {p.name: p.stat().st_mtime_ns for p in Path("/state/days").glob("*.json")}
Path("/results/cache-before.json").write_text(json.dumps(times, indent=1))
print(f"{len(times)} cached days recorded")
