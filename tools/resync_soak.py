#!/usr/bin/env python3
"""Soak with two interleaved disturbances on a fixed schedule:

  rotate     every --period s (at 1, 2, 3 ... periods): move the GM to the
             next board via `ptp_prio` (new GM 32, everyone else 128)
  knockout   every --period s, offset by half a period (0.5, 1.5 ...):
             `ptp_clock set ptp-clock 0` on every non-GM board, forcing each
             to step its clock, reset its servo and re-acquire lock

At t=0 the first GM is set up (event "setup"). Events whose time is less
than --tail s before the end are skipped, so each one can be observed.

Holds every console for the whole run; logs each to <out>/bN.log with host
timestamps and records events in <out>/events.txt as
"<epoch> <kind> gm=<n> prev=<n>" (the gm_rotation_analyze.py format).
Never touches DTR/RTS. Run export_time_error.py on <out> afterwards for CSV.

usage: resync_soak.py <out> [--hours 2] [--period 900] [--first-gm 1]
                      [--boards 1,2,...] [--tail 450]
"""
import argparse
import os
import re
import threading
import time

import serial

from boards import select

ANSI = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
GM_PRIO = 32
OTHER_PRIO = 128


class Board:
    def __init__(self, n, port, out):
        self.n = n
        self.ser = serial.Serial(port, 115200, timeout=0.1)
        self.log = open(os.path.join(out, f"b{n}.log"), "a", buffering=1)
        self.lock = threading.Lock()
        threading.Thread(target=self.reader, daemon=True).start()
        # line noise on open can prefix the first command; flush the shell's
        # input line before anything real is sent
        self.ser.write(b"\r\n")
        time.sleep(0.3)

    def reader(self):
        buf = b""
        while True:
            data = self.ser.read(4096)
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = ANSI.sub(b"", raw).decode("utf-8", "replace").rstrip("\r")
                line = line.replace("uart:~$ ", "")
                if line.strip():
                    self.log.write(f"{time.time():.3f} {line}\n")

    def send(self, cmd):
        with self.lock:
            self.ser.write((cmd + "\r\n").encode())
            self.log.write(f"{time.time():.3f} >>> {cmd}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--period", type=float, default=900.0)
    ap.add_argument("--first-gm", type=int, default=1)
    ap.add_argument("--boards", default=None, help="comma list, default all")
    ap.add_argument("--tail", type=float, default=450.0,
                    help="skip events closer than this to the end")
    ap.add_argument("--diag-every", type=float, default=300.0)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    boards = {n: Board(n, p, args.out) for n, p in select(args.boards).items()}
    order = sorted(boards)
    events = open(os.path.join(args.out, "events.txt"), "a", buffering=1)

    def set_gm(gm):
        boards[gm].send(f"ptp_prio {GM_PRIO}")
        time.sleep(0.2)
        for n, b in boards.items():
            if n != gm:
                b.send(f"ptp_prio {OTHER_PRIO}")

    start = time.time()
    end = start + args.hours * 3600
    sched = [(0.0, "setup")]
    k = 1
    while True:
        t_ko = (k - 0.5) * args.period
        t_rot = k * args.period
        if t_ko <= args.hours * 3600 - args.tail:
            sched.append((t_ko, "knockout"))
        if t_rot <= args.hours * 3600 - args.tail:
            sched.append((t_rot, "rotate"))
        if t_ko > args.hours * 3600:
            break
        k += 1
    sched.sort()

    gm = args.first_gm
    next_diag = start + 60
    for t_rel, kind in sched:
        t_ev = start + t_rel
        while time.time() < t_ev:
            if time.time() >= next_diag:
                for b in boards.values():
                    b.send("ptp_diag")
                next_diag += args.diag_every
            time.sleep(0.2)

        prev = gm
        if kind == "rotate":
            gm = order[(order.index(gm) + 1) % len(order)]
        t0 = time.time()
        events.write(f"{t0:.3f} {kind} gm={gm} prev={prev}\n")
        print(f"{time.strftime('%H:%M:%S')} {kind}: GM B{gm}"
              + (f" (was B{prev})" if kind == "rotate" else ""), flush=True)
        if kind in ("setup", "rotate"):
            set_gm(gm)
        else:
            for n, b in boards.items():
                if n != gm:
                    b.send("ptp_clock set ptp-clock 0")

    while time.time() < end:
        time.sleep(1)
    for b in boards.values():
        b.send("ptp_diag")
    time.sleep(3)
    print("resync soak finished", flush=True)


if __name__ == "__main__":
    main()
