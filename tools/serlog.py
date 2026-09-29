#!/usr/bin/env python3
"""Reset an ESP32 via RTS/DTR and log its console for N seconds.

usage: serlog.py <port> <seconds> [--no-reset] [--send "cmd"]...
"""
import argparse
import sys
import time

import serial

ap = argparse.ArgumentParser()
ap.add_argument("port")
ap.add_argument("seconds", type=float)
ap.add_argument("--no-reset", action="store_true")
ap.add_argument("--send", action="append", default=[],
                help="shell command to send (after --send-delay)")
ap.add_argument("--send-delay", type=float, default=3.0)
args = ap.parse_args()

s = serial.Serial(args.port, 115200, timeout=0.1)
if not args.no_reset:
    # EN low via RTS, IO0 high via DTR -> normal boot
    s.dtr = False
    s.rts = True
    time.sleep(0.1)
    s.rts = False

t0 = time.monotonic()
pending = list(args.send)
next_send = t0 + args.send_delay
while time.monotonic() - t0 < args.seconds:
    if pending and time.monotonic() >= next_send:
        s.write((pending.pop(0) + "\r\n").encode())
        next_send = time.monotonic() + 1.0
    data = s.read(4096)
    if data:
        sys.stdout.write(data.decode("utf-8", "replace"))
        sys.stdout.flush()
