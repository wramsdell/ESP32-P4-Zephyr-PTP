#!/usr/bin/env python3
"""Export per-board time-error measurements from a soak run to CSV.

Writes <run_dir>/time_error.csv with one row per measurement:
  host_epoch   host time the line was read (s)
  t_rel_s      seconds since the run's first event
  board        board number
  uptime_s     board uptime from the log line's own timestamp
  kind         offset   servo offset sample (clock_adjust_rate)
               step     offset that triggered a clock step
               delay    E2E mean path delay sample
  value_ns     the measurement in ns
  gm           grandmaster in effect at that time (from events.txt)

and <run_dir>/events.csv: host_epoch, t_rel_s, kind, gm, prev.

The offsets are each slave's own measurement against the GM (its servo's
view), not an external reference.

usage: export_time_error.py <run_dir>
"""
import bisect
import csv
import glob
import re
import sys

UPTIME = re.compile(r"\[(\d+):(\d+):(\d+)\.(\d+),(\d+)\]")
OFFSET = re.compile(r"clock_adjust_rate: Offset (-?\d+)ns")
STEP = re.compile(r"exceeds step threshold .*? offset=(-?\d+)ns")
DELAY = re.compile(r"ptp_clock_delay: Delay (-?\d+)ns")


def main():
    run = sys.argv[1]
    events = []
    for line in open(f"{run}/events.txt"):
        t, kind, gm, prev = line.split()
        events.append((float(t), kind, gm.split("=")[1], prev.split("=")[1]))
    events.sort()
    t_start = events[0][0]
    ev_times = [e[0] for e in events]

    with open(f"{run}/events.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["host_epoch", "t_rel_s", "kind", "gm", "prev"])
        for t, kind, gm, prev in events:
            w.writerow([f"{t:.3f}", f"{t - t_start:.3f}", kind, gm, prev])

    rows = 0
    with open(f"{run}/time_error.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["host_epoch", "t_rel_s", "board", "uptime_s", "kind", "value_ns", "gm"])
        for path in sorted(glob.glob(f"{run}/b*.log")):
            board = int(re.search(r"b(\d+)\.log$", path)[1])
            for line in open(path, errors="replace"):
                try:
                    t = float(line.split(" ", 1)[0])
                except ValueError:
                    continue
                for kind, pat in (("offset", OFFSET), ("step", STEP), ("delay", DELAY)):
                    m = pat.search(line)
                    if m:
                        break
                else:
                    continue
                u = UPTIME.search(line)
                up = (int(u[1]) * 3600 + int(u[2]) * 60 + int(u[3]) + int(u[4]) / 1000
                      if u else "")
                i = bisect.bisect_right(ev_times, t) - 1
                gm = events[i][2] if i >= 0 else ""
                w.writerow([f"{t:.3f}", f"{t - t_start:.3f}", board,
                            f"{up:.3f}" if up != "" else "", kind, m[1], gm])
                rows += 1
    print(f"wrote {rows} rows to {run}/time_error.csv and {len(events)} events to "
          f"{run}/events.csv")


if __name__ == "__main__":
    main()
