"""Session report written next to the frames at the end of every capture."""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Dict, Optional, Tuple

from .gauges import GAUGES, footage, format_duration, running_time
from .project import Reel
from .session import SessionSettings, SessionStats


def _ranges(numbers) -> str:
    """[3,4,5,9] -> '3-5, 9'"""
    nums = sorted(set(numbers))
    if not nums:
        return "none"
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(out)


def write_report(reel: Reel, stats: SessionStats, settings: SessionSettings,
                 camera: Optional[Dict] = None, when: Optional[datetime] = None) -> Tuple[str, str]:
    """Write ``session_<time>.txt`` and ``.csv`` in the reel folder. Returns both paths."""
    when = when or datetime.now()
    g = GAUGES.get(reel.gauge, GAUGES["super8"])
    base = os.path.join(reel.folder, f"session_{when.strftime('%Y%m%d_%H%M%S')}")
    cam = camera or {}
    rows = [
        ("Reel", f"{reel.id} {reel.metadata.title}"),
        ("Gauge", g.name),
        ("Film", ("negative" if reel.negative else "positive") +
         (f", {reel.metadata.stock}" if reel.metadata.stock else "")),
        ("Started", when.strftime("%Y-%m-%d %H:%M:%S")),
        ("Duration", format_duration(stats.elapsed)),
        ("Triggers", str(stats.triggers)),
        ("Frames saved", str(stats.saved)),
        ("Frame numbers", f"{stats.first_number}-{stats.last_number}" if stats.last_number
         else "none"),
        ("Dropped frames", f"{stats.dropped} ({_ranges(stats.dropped_numbers)})"),
        ("Duplicate frames", f"{stats.duplicates} ({_ranges(stats.duplicate_numbers)})"),
        ("Sensor bounces ignored", str(stats.bounces)),
        ("Write errors", str(stats.write_errors)),
        ("Average capture speed", f"{stats.trigger_rate:.2f} fps"),
        ("Average trigger to frame", f"{stats.mean_latency_ms:.0f} ms"),
        ("Trigger delay", f"{settings.delay * 1000:.0f} ms"),
        ("File format", settings.file_format),
        ("Data written", f"{stats.bytes_written / 1e9:.2f} GB"),
        ("Footage", f"{footage(stats.saved, g):.1f} ft"),
        ("Running time", f"{format_duration(running_time(stats.saved, g.default_fps))} "
                         f"at {g.default_fps:g} fps"),
        ("Camera", str(cam.get("name", ""))),
        ("Camera mode", str(cam.get("mode", ""))),
        ("Camera settings", ", ".join(f"{k}={v:g}" for k, v in
                                      dict(cam.get("values", {})).items()) or "not locked"),
    ]
    verdict = ("OK: every trigger produced exactly one saved frame."
               if stats.dropped == 0 and stats.duplicates == 0 and stats.write_errors == 0
               and stats.saved == stats.triggers
               else "CHECK: some frames are missing or repeated; see the numbers above and "
                    "capture.log.")
    width = max(len(k) for k, _ in rows)
    with open(base + ".txt", "w", encoding="utf-8") as fh:
        fh.write("Cine Capture Pro - capture session report\n")
        fh.write("=" * 60 + "\n")
        for k, v in rows:
            fh.write(f"{k.ljust(width)} : {v}\n")
        fh.write("=" * 60 + "\n" + verdict + "\n")
    with open(base + ".csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "value"])
        w.writerows(rows)
        w.writerow(["dropped_numbers", " ".join(map(str, stats.dropped_numbers))])
        w.writerow(["duplicate_numbers", " ".join(map(str, stats.duplicate_numbers))])
    return base + ".txt", base + ".csv"
