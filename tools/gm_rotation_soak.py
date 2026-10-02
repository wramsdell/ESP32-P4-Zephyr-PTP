#!/usr/bin/env python3
"""Grandmaster-rotation soak: change the GM every --period seconds and,
every --reset-every events, reset all boards and change the GM.

Holds all four consoles for the whole run (one process, so commands and
logs don't fight over the ports), logs each to <out>/bN.log with host
timestamps, and records every event in <out>/events.txt as
"<epoch> <kind> gm=<n> prev=<n>".

GM selection uses the `ptp_prio` shell command: the new GM gets
priority1 32 and everyone else 128, so the choice never depends on the
build-time default (board 3 is 64). After a reset, priorities are sent
only once the PTP stack has initialized (--boot-wait), since
ptp_clock_init() would overwrite an earlier value.

Reset is the same RTS pulse as serlog.py (DTR low, RTS high 100 ms).
Nothing else touches DTR/RTS.

usage: gm_rotation_soak.py <out> [--hours 8] [--period 900]
                           [--reset-every 4] [--first-gm 1]
"""
import argparse
import os
import re
import threading
import time

import serial

PORTS = {
    1: "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90094322-if00",
    2: "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90094925-if00",
    3: "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90094401-if00",
    4: "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90038724-if00",
}
ANSI = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
GM_PRIO = 32
OTHER_PRIO = 128


class Board:
    def __init__(self, n, out):
        self.n = n
        self.ser = serial.Serial(PORTS[n], 115200, timeout=0.1)
        self.log = open(os.path.join(out, f"b{n}.log"), "a", buffering=1)
        self.lock = threading.Lock()
        threading.Thread(target=self.reader, daemon=True).start()

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

    def reset_assert(self):
        self.ser.dtr = False
        self.ser.rts = True

    def reset_release(self):
        self.ser.rts = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--period", type=float, default=900.0)
    ap.add_argument("--reset-every", type=int, default=4)
    ap.add_argument("--first-gm", type=int, default=1)
    ap.add_argument("--boot-wait", type=float, default=6.0)
    ap.add_argument("--diag-every", type=float, default=300.0)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    boards = {n: Board(n, args.out) for n in PORTS}
    events = open(os.path.join(args.out, "events.txt"), "a", buffering=1)

    def set_gm(gm, prev):
        # new GM first, then demote the rest
        boards[gm].send(f"ptp_prio {GM_PRIO}")
        time.sleep(0.2)
        for n, b in boards.items():
            if n != gm:
                b.send(f"ptp_prio {OTHER_PRIO}")

    n_events = int(args.hours * 3600 // args.period)
    start = time.time()
    gm = None
    next_diag = start + 60
    for k in range(n_events):
        t_ev = start + k * args.period
        while time.time() < t_ev:
            if time.time() >= next_diag:
                for b in boards.values():
                    b.send("ptp_diag")
                next_diag += args.diag_every
            time.sleep(0.5)

        prev = gm
        gm = (args.first_gm - 1 + k) % 4 + 1
        is_reset = k % args.reset_every == 0
        if is_reset:
            for b in boards.values():
                b.reset_assert()
            time.sleep(0.1)
            t0 = time.time()
            for b in boards.values():
                b.reset_release()
            events.write(f"{t0:.3f} reset gm={gm} prev={prev}\n")
            print(f"{time.strftime('%H:%M:%S')} event {k}: reset all, GM -> B{gm}", flush=True)
            time.sleep(args.boot_wait)
            set_gm(gm, prev)
        else:
            t0 = time.time()
            events.write(f"{t0:.3f} rotate gm={gm} prev={prev}\n")
            print(f"{time.strftime('%H:%M:%S')} event {k}: GM B{prev} -> B{gm}", flush=True)
            set_gm(gm, prev)

    end = start + n_events * args.period
    while time.time() < end:
        time.sleep(1)
    for b in boards.values():
        b.send("ptp_diag")
    time.sleep(3)
    print("rotation soak finished", flush=True)


if __name__ == "__main__":
    main()
