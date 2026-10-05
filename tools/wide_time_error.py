#!/usr/bin/env python3
"""Pivot a run's time_error.csv to one row per second, boards as columns.

Writes <run_dir>/time_error_wide.csv:
  t_rel_s, gm, event, B1_delay_ns, B1_offset_ns, B2_delay_ns, B2_offset_ns, ...

- Each sample is placed at its board uptime plus a per-board host offset
  (median of host_epoch - uptime), because host read times are bunched by
  deferred logging while the board's own timestamp is exact to 1 ms; then
  binned to whole seconds of t_rel_s.
- offset = servo offset samples only. Clock-step offsets (~1e14 ns after a
  knockout) are left out; the event column marks knockouts and rotations.
- A board with no sample in a second leaves the cell empty; if it has two,
  the later one is kept (counted in the summary).

usage: wide_time_error.py <run_dir>
"""
import csv
import statistics
import sys


def main():
    run = sys.argv[1]
    rows = list(csv.DictReader(open(f"{run}/time_error.csv")))
    events = list(csv.DictReader(open(f"{run}/events.csv")))
    t0 = float(events[0]["host_epoch"])
    boards = sorted({int(r["board"]) for r in rows})

    # Per-board host-minus-uptime offset. A reboot would change it; treat
    # each uptime run separately by splitting on uptime going backwards.
    seg_off = {}
    for b in boards:
        rb = [r for r in rows if int(r["board"]) == b and r["uptime_s"]]
        seg, prev = 0, -1.0
        diffs = {}
        for r in rb:
            up = float(r["uptime_s"])
            if up < prev:
                seg += 1
            prev = up
            r["_seg"] = seg
            diffs.setdefault(seg, []).append(float(r["host_epoch"]) - up)
        for s, d in diffs.items():
            seg_off[(b, s)] = statistics.median(d)

    grid, dup = {}, 0
    for r in rows:
        if r["kind"] not in ("offset", "delay") or not r["uptime_s"]:
            continue
        b = int(r["board"])
        t = float(r["uptime_s"]) + seg_off[(b, r["_seg"])] - t0
        sec = int(t // 1)
        key = (sec, f"B{b}_{r['kind']}_ns")
        if key in grid:
            dup += 1
        grid[key] = r["value_ns"]

    ev_by_sec = {}
    for e in events:
        sec = int((float(e["host_epoch"]) - t0) // 1)
        ev_by_sec[sec] = e["kind"] + (f" B{e['prev']}->B{e['gm']}" if e["kind"] == "rotate"
                                      else f" (GM B{e['gm']})")
    ev_times = sorted((float(e["host_epoch"]) - t0, e["gm"]) for e in events)

    cols = []
    for b in boards:
        cols += [f"B{b}_delay_ns", f"B{b}_offset_ns"]
    secs = sorted({k[0] for k in grid} | set(ev_by_sec))
    secs = range(max(0, secs[0]), secs[-1] + 1)
    with open(f"{run}/time_error_wide.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_rel_s", "gm", "event"] + cols)
        for s in secs:
            gm = next((g for t, g in reversed(ev_times) if t <= s + 0.999), "")
            w.writerow([s, gm, ev_by_sec.get(s, "")] + [grid.get((s, c), "") for c in cols])

    filled = {c: sum(1 for s in secs if (s, c) in grid) for c in cols}
    print(f"wrote {len(secs)} rows x {len(cols)} board columns to {run}/time_error_wide.csv; "
          f"{dup} same-second duplicates (later kept)")
    for c in cols:
        print(f"  {c:14} {filled[c]:5d} samples ({filled[c] / len(secs):.0%} of seconds)")


if __name__ == "__main__":
    main()
