#!/usr/bin/env python3
"""Step the grandmaster's PTP clock and log every board's response.

Opens all four consoles (never touching DTR/RTS), logs each to
<out>/stepN.log with host timestamps, and at scheduled times sends
`ptp_clock adj ptp-clock <ns>` to the GM. Step times go to <out>/steps.txt.

usage: gm_step_test.py <out_dir> [--gm 2] [--step-ns 20000] [--cycles 2]
                       [--hold 180]
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


def reader(ser, path, stop):
    with open(path, "a", buffering=1) as log:
        buf = b""
        while not stop.is_set():
            data = ser.read(4096)
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = ANSI.sub(b"", raw).decode("utf-8", "replace").rstrip("\r")
                line = line.replace("uart:~$ ", "")
                if line.strip():
                    log.write(f"{time.time():.3f} {line}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--gm", type=int, default=2)
    ap.add_argument("--step-ns", type=int, default=20000)
    ap.add_argument("--cycles", type=int, default=2)
    ap.add_argument("--hold", type=float, default=180.0)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    stop = threading.Event()
    sers = {}
    for n, port in PORTS.items():
        sers[n] = serial.Serial(port, 115200, timeout=0.1)
        threading.Thread(target=reader, args=(sers[n], os.path.join(args.out, f"step{n}.log"),
                                               stop), daemon=True).start()

    steps = open(os.path.join(args.out, "steps.txt"), "a", buffering=1)
    gm = sers[args.gm]

    def send(cmd):
        gm.write((cmd + "\r\n").encode())
        steps.write(f"{time.time():.3f} {cmd}\n")
        print(f"{time.strftime('%H:%M:%S')} GM <- {cmd}", flush=True)

    time.sleep(5)
    send("ptp_clock get ptp-clock")
    time.sleep(args.hold / 3)
    for _ in range(args.cycles):
        for sign in (1, -1):
            send(f"ptp_clock adj ptp-clock {sign * args.step_ns}")
            time.sleep(args.hold)
    send("ptp_clock get ptp-clock")
    time.sleep(5)
    stop.set()
    time.sleep(0.3)


if __name__ == "__main__":
    main()
