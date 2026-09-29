#!/usr/bin/env python3
"""Summarize servo offsets from a Zephyr PTP slave console log.

usage: offset_stats.py <log> [skip_seconds]
Uses the "clock_adjust_rate: Offset <n>ns" debug lines; samples before
skip_seconds of uptime are treated as acquisition and ignored.
"""
import re
import statistics
import sys

skip = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
pat = re.compile(r"\[(\d+):(\d+):(\d+)\.(\d+),\d+\].*Offset (-?\d+)ns")
vals = []
for line in open(sys.argv[1], errors="replace"):
    m = pat.search(line)
    if not m:
        continue
    h, mi, s, ms, off = m.groups()
    t = int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000
    if t >= skip:
        vals.append(int(off))

if not vals:
    sys.exit("no samples")
print(f"n={len(vals)}  mean={statistics.mean(vals):+.1f} ns  "
      f"stdev={statistics.pstdev(vals):.1f} ns  "
      f"rms={(sum(v * v for v in vals) / len(vals)) ** 0.5:.1f} ns  "
      f"worst={max(vals, key=abs):+d} ns  "
      f"p99|x|={sorted(abs(v) for v in vals)[int(0.99 * (len(vals) - 1))]} ns")
