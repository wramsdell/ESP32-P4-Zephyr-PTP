#!/usr/bin/env python3
"""Soak-test monitor for the PoE-ETH boards (default: tools/boards.py PORTS).

Keeps a reader on each board's console (full log to <out>/bN.log), sends
`ptp_diag` every --interval seconds, and flags a board as stalled when its
port's TX sequence counters (announce/delay/sync) haven't moved across two
consecutive polls. A healthy port always sends something each interval:
Delay_Req as receiver, Sync/Announce as transmitter.

On a stall it captures `kernel thread unwind <PTP tid>`, `ptp_diag`,
`net ptp port 1` and `kernel thread list` into <out>/stall_bN.txt,
symbolizes the unwind against build/bN, and exits so the caller notices.
It never touches DTR/RTS, which would reset the board.

usage: soak_monitor.py [--hours H] [--interval S] [--out DIR] [--boards 1,2,...]
                       [--build-suffix -smp]
"""
import argparse
import os
import re
import subprocess
import sys
import threading
import time

import serial

from boards import PORTS
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDR2LINE = os.path.expanduser(
    "~/zephyr-sdk-1.0.1/gnu/riscv64-zephyr-elf/bin/riscv64-zephyr-elf-addr2line")
ANSI = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
PORT_RE = re.compile(r"diag: port \d+ state=(\d+) timeouts=(0x[0-9a-f]+).*"
                     r"seq ann=(\d+) delay=(\d+) sync=(\d+)")
TID_RE = re.compile(r"diag: thread tid=(0x[0-9a-f]+)")
RX_RE = re.compile(r"received (Sync|Announce|Delay_Req|Delay_Resp)")


class Board:
    def __init__(self, n, out):
        self.n = n
        self.ser = serial.Serial(PORTS[n], 115200, timeout=0.1)
        self.log = open(os.path.join(out, f"b{n}.log"), "a", buffering=1)
        self.lock = threading.Lock()
        self.capture = None          # list collecting lines while capturing
        self.last_rx = {}
        self.last_diag = None
        self.tid = None
        self.stop = False
        threading.Thread(target=self.reader, daemon=True).start()

    def reader(self):
        buf = b""
        while not self.stop:
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
                m = RX_RE.search(line)
                if m:
                    self.last_rx[m.group(1)] = now
                with self.lock:
                    if self.capture is not None:
                        self.capture.append(line)

    def command(self, cmd, wait=2.0):
        with self.lock:
            self.capture = []
        self.ser.write((cmd + "\r\n").encode())
        time.sleep(wait)
        with self.lock:
            lines, self.capture = self.capture, None
        return lines

    def diag(self):
        lines = self.command("ptp_diag", 1.5)
        port = None
        for line in lines:
            m = PORT_RE.search(line)
            if m:
                port = dict(state=int(m.group(1)), timeouts=int(m.group(2), 16),
                            seq=(int(m.group(3)), int(m.group(4)), int(m.group(5))))
            m = TID_RE.search(line)
            if m:
                self.tid = m.group(1)
        return port, lines


def symbolize(n, lines, suffix=""):
    elf = os.path.join(REPO, "build", f"b{n}{suffix}", "zephyr", "zephyr.elf")
    addrs = sorted(set(re.findall(r"0x4[0-9a-f]{7}", "\n".join(lines))))
    if not addrs or not os.path.exists(elf):
        return []
    res = subprocess.run([ADDR2LINE, "-f", "-p", "-C", "-e", elf] + addrs,
                         capture_output=True, text=True)
    return [f"{a}: {s}" for a, s in zip(addrs, res.stdout.splitlines())]


def capture_stall(b, out, history, suffix=""):
    report = [f"board {b.n} stall detected at {time.ctime()}",
              "last diag snapshots:"] + history
    for cmd in ([f"kernel thread unwind {b.tid}"] if b.tid else []) + [
            "ptp_diag", "net ptp port 1", "kernel thread list", "net iface"]:
        report += ["", f"$ {cmd}"] + b.command(cmd, 3.0)
    report += ["", f"symbolized addresses (build/b{b.n}{suffix}):"] + symbolize(b.n, report, suffix)
    path = os.path.join(out, f"stall_b{b.n}.txt")
    with open(path, "w") as f:
        f.write("\n".join(report) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--out", default=os.path.join(
        REPO, "soak", time.strftime("%Y%m%d-%H%M%S")))
    ap.add_argument("--boards", default=",".join(str(n) for n in sorted(PORTS)))
    ap.add_argument("--build-suffix", default="",
                    help="build dir suffix for symbolizing, e.g. -smp for build/bN-smp")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    boards = [Board(int(n), args.out) for n in args.boards.split(",")]
    summary = open(os.path.join(args.out, "summary.log"), "a", buffering=1)
    prev = {b.n: None for b in boards}
    snaps = {b.n: [] for b in boards}
    end = time.time() + args.hours * 3600
    print(f"soak: logging to {args.out}", flush=True)

    while time.time() < end:
        t_poll = time.time()
        for b in boards:
            port, lines = b.diag()
            snap = " | ".join(l for l in lines if l.startswith("diag:"))
            snaps[b.n] = (snaps[b.n] + [f"{time.ctime()} {snap}"])[-5:]
            rx_age = {k: int(t_poll - v) for k, v in b.last_rx.items()}
            summary.write(f"{time.strftime('%H:%M:%S')} b{b.n} port={port} "
                          f"rx_age_s={rx_age}\n")
            if port is None:
                continue
            if prev[b.n] is not None and port["seq"] == prev[b.n]["seq"]:
                if prev[b.n].get("stuck"):
                    path = capture_stall(b, args.out, snaps[b.n], args.build_suffix)
                    summary.write(f"STALL b{b.n} captured -> {path}\n")
                    print(f"soak: STALL on board {b.n}, captured {path}", flush=True)
                    return 2
                port["stuck"] = True
            prev[b.n] = port
        time.sleep(max(0.0, args.interval - (time.time() - t_poll)))

    print("soak: finished without a stall", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
