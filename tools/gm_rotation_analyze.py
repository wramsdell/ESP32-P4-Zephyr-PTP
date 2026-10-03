#!/usr/bin/env python3
"""Analyze a gm_rotation_soak.py run: per event, how long until every board
is back in sync with the new grandmaster.

For each event (from events.txt):
  gm_up    when the new GM logged its move to TIME TRANSMITTER/GRAND MASTER
  demoted  (rotations) when the previous GM logged its move to TIME RECEIVER
  sync     per slave, the first offset sample that starts --run consecutive
           samples within the threshold, counted only from when the new GM
           was up (and, for the old GM, from its demotion); reported for
           1 us and 300 ns
  all      the latest slave's sync time, i.e. "all boards back in sync"
  peak     largest |offset| a slave showed between the event and its 1 us sync
  steps    clock steps ("Set clock time") during that window

Times are seconds after the event. A slave that never meets the criterion
before the next event is reported as '-'.

usage: gm_rotation_analyze.py <run_dir> [--run 10]
"""
import argparse
import glob
import re
import statistics

OFFSET = re.compile(r"Offset (-?\d+)ns")
STATE = re.compile(r"changed state from (.+?) to (.+)$")


def load(run_dir):
    events = []
    for line in open(f"{run_dir}/events.txt"):
        t, kind, gm, prev = line.split()
        prev = prev.split("=")[1]
        events.append((float(t), kind, int(gm.split("=")[1]),
                       None if prev == "None" else int(prev)))
    boards = {}
    for n in sorted(int(re.search(r"b(\d+)\.log$", f)[1])
                    for f in glob.glob(f"{run_dir}/b*.log")):
        offs, states, steps = [], [], []
        for line in open(f"{run_dir}/b{n}.log", errors="replace"):
            try:
                t = float(line.split(" ", 1)[0])
            except ValueError:
                continue
            m = OFFSET.search(line)
            if m:
                offs.append((t, int(m[1])))
                continue
            m = STATE.search(line)
            if m:
                states.append((t, m[1].strip(), m[2].strip()))
                continue
            if "Set clock time" in line:
                steps.append(t)
        boards[n] = (offs, states, steps)
    return events, boards


def first_state(states, t0, t1, targets):
    for t, _, to in states:
        if t0 <= t < t1 and to in targets:
            return t
    return None


def sync_time(offs, t_from, t_end, thr, run):
    win = [(t, o) for t, o in offs if t_from <= t < t_end]
    for i in range(len(win) - run + 1):
        if all(abs(o) < thr for _, o in win[i:i + run]):
            return win[i][0]
    return None


def fmt(v):
    return "-" if v is None else f"{v:5.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--run", type=int, default=10)
    args = ap.parse_args()

    events, boards = load(args.run_dir)
    summary = {"reset": [], "rotate": []}
    print(f"{'#':>2} {'time':8} {'kind':6} {'GM':>5} {'gm_up':>6} {'demote':>6} "
          f"{'all<1us':>7} {'all<300n':>8}  per-slave <1us / <300ns / peak us / steps")
    for i, (t0, kind, gm, prev) in enumerate(events):
        t_end = events[i + 1][0] if i + 1 < len(events) else float("inf")
        gm_up = first_state(boards[gm][1], t0, t_end,
                            ("TIME TRANSMITTER", "GRAND MASTER"))
        demote = None
        if kind == "rotate" and prev is not None:
            demote = first_state(boards[prev][1], t0, t_end, ("TIME RECEIVER",))
        per, s1, s3 = [], [], []
        for n in sorted(boards):
            if n == gm:
                continue
            offs, _, steps = boards[n]
            start = gm_up if gm_up else t0
            if n == prev and demote:
                start = max(start, demote)
            a = sync_time(offs, start, t_end, 1000, args.run)
            b = sync_time(offs, start, t_end, 300, args.run)
            stop = a if a else t_end
            peak = max((abs(o) for t, o in offs if t0 <= t <= stop), default=0)
            nstep = sum(1 for t in steps if t0 <= t <= stop)
            s1.append(None if a is None else a - t0)
            s3.append(None if b is None else b - t0)
            per.append(f"B{n} {fmt(s1[-1])}/{fmt(s3[-1])}/{peak / 1000:.1f}/{nstep}")
        all1 = None if None in s1 else max(s1)
        all3 = None if None in s3 else max(s3)
        summary[kind].append((all1, all3))
        tm = __import__("time").strftime("%H:%M:%S", __import__("time").localtime(t0))
        print(f"{i:2d} {tm} {kind:6} {prev if prev else '-':>2}->{gm} "
              f"{fmt(None if gm_up is None else gm_up - t0):>6} "
              f"{fmt(None if demote is None else demote - t0):>6} "
              f"{fmt(all1):>7} {fmt(all3):>8}  " + "  ".join(per))

    for kind, rows in summary.items():
        for label, idx in (("<1 us", 0), ("<300 ns", 1)):
            v = [r[idx] for r in rows if r[idx] is not None]
            if v:
                print(f"{kind:6} all boards {label:8}: n={len(v)}/{len(rows)} "
                      f"median {statistics.median(v):.1f} s, min {min(v):.1f}, max {max(v):.1f}")


if __name__ == "__main__":
    main()
