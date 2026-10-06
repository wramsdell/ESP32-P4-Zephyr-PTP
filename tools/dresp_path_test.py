#!/usr/bin/env python3
"""Hidden-delay-error test: drop Delay_Resp on some slaves, then change the
path to the GM by moving the GM to the other switch, then restore Delay_Resp.

A slave that gets no Delay_Resp keeps its last path delay, so after a path
change it silently servoes to a wrong time; when responses resume the delay
jumps and the hidden error shows up as an offset step.

Schedule (s): 0 GM=--gm-a; 60 faults on; 120 GM=--gm-b; 240 faults off;
300 faults on; 360 GM=--gm-a; 480 faults off; 540 end.

Holds all consoles (never touching DTR/RTS), logs to <out>/bN.log and
events to <out>/events.txt ("<epoch> <kind> gm=<n> prev=<n>"), then prints
per-phase median delay/offset per board and the offsets after each change.

usage: dresp_path_test.py <out> [--faulted 1,5] [--gm-a 3] [--gm-b 6]
"""
import argparse
import os
import re
import statistics
import threading
import time

import serial

from boards import select

ANSI = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
SAMPLE = re.compile(r"\[(\d+):(\d+):(\d+)\.(\d+),\d+\].*(Offset|Delay) (-?\d+)ns")


class Board:
    def __init__(self, n, port, out):
        self.n = n
        self.ser = serial.Serial(port, 115200, timeout=0.1)
        self.log = open(os.path.join(out, f"b{n}.log"), "a", buffering=1)
        self.samples = []  # (host_time, kind, ns)
        threading.Thread(target=self.reader, daemon=True).start()
        self.ser.write(b"\r\n")

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
                if not line.strip():
                    continue
                now = time.time()
                self.log.write(f"{now:.3f} {line}\n")
                m = SAMPLE.search(line)
                if m:
                    self.samples.append((now, m[5], int(m[6])))

    def send(self, cmd):
        self.ser.write((cmd + "\r\n").encode())
        self.log.write(f"{time.time():.3f} >>> {cmd}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--faulted", default="1,5")
    ap.add_argument("--gm-a", type=int, default=3)
    ap.add_argument("--gm-b", type=int, default=6)
    ap.add_argument("--boards", default=None)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    boards = {n: Board(n, p, args.out) for n, p in select(args.boards).items()}
    faulted = [int(x) for x in args.faulted.split(",")]
    events = open(os.path.join(args.out, "events.txt"), "a", buffering=1)
    time.sleep(1)

    gm = [args.gm_a]

    def set_gm(new):
        boards[new].send("ptp_prio 32")
        time.sleep(0.2)
        for n, b in boards.items():
            if n != new:
                b.send("ptp_prio 128")
        events.write(f"{time.time():.3f} rotate gm={new} prev={gm[0]}\n")
        gm[0] = new

    def faults(on):
        for n in faulted:
            boards[n].send("ptp_fault drop dresp 100" if on else "ptp_fault off")
        events.write(f"{time.time():.3f} {'fault_on' if on else 'fault_off'} "
                     f"gm={gm[0]} prev={gm[0]}\n")

    plan = [(0, "setup", lambda: set_gm(args.gm_a)),
            (60, "faults on", lambda: faults(True)),
            (120, f"GM -> B{args.gm_b}", lambda: set_gm(args.gm_b)),
            (240, "faults off", lambda: faults(False)),
            (300, "faults on", lambda: faults(True)),
            (360, f"GM -> B{args.gm_a}", lambda: set_gm(args.gm_a)),
            (480, "faults off", lambda: faults(False))]
    start = time.time()
    marks = []
    for at, label, act in plan:
        while time.time() - start < at:
            time.sleep(0.05)
        marks.append((time.time(), label))
        print(f"{at:4d} s  {label}", flush=True)
        act()
    while time.time() - start < 540:
        time.sleep(0.2)
    for n in faulted:
        boards[n].send("ptp_fault")
    time.sleep(2)

    # per-phase medians (skip the first 20 s of each phase as settling)
    print("\nper-phase median delay / offset (ns), excluding 20 s after each change:")
    print("phase".ljust(22) + "".join(f"  B{n}{'*' if n in faulted else ' '}".rjust(16)
                                       for n in boards))
    for i, (t0, label) in enumerate(marks):
        t1 = marks[i + 1][0] if i + 1 < len(marks) else start + 540
        cells = []
        for n, b in boards.items():
            d = [v for t, k, v in b.samples if k == "Delay" and t0 + 20 <= t < t1]
            o = [v for t, k, v in b.samples if k == "Offset" and t0 + 20 <= t < t1]
            cells.append(f"{statistics.median(d) if d else 0:>7.0f}/{statistics.median(o) if o else 0:<6.0f}"
                         if (d or o) else "  (GM)".ljust(14))
        print(f"{label:22}" + "".join(f"  {c:>14}" for c in cells))

    print("\nfirst 8 offsets (us) after each change, faulted boards marked *:")
    for t0, label in marks[1:]:
        print(f"-- {label}")
        for n, b in boards.items():
            o = [v / 1000 for t, k, v in b.samples if k == "Offset" and t >= t0][:8]
            if o:
                print(f"   B{n}{'*' if n in faulted else ' '} " + " ".join(f"{x:+6.2f}" for x in o))


if __name__ == "__main__":
    main()
