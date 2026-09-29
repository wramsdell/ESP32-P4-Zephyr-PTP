#!/usr/bin/env python3
"""Read 32-bit words from a running board via the Zephyr `devmem` shell
command, without resetting it.

usage: devmem_read.py <port> <addr>[:<nwords>] ...
Prints "addr: value" per word. Addresses are hex.

Peripheral registers only: on ESP32-P4, devmem reads of internal SRAM
(0x4FF..., or its uncached alias 0x8FF...) return 0 or stale data.
"""
import re
import sys
import time

import serial

port = sys.argv[1]
words = []
for spec in sys.argv[2:]:
    addr, _, n = spec.partition(":")
    base = int(addr, 16)
    words += [base + 4 * i for i in range(int(n) if n else 1)]

# Leave DTR/RTS alone: releasing DTR while RTS is still asserted pulls EN low
# through the auto-reset circuit and reboots the board.
s = serial.Serial(port, 115200, timeout=0.05)
s.write(b"\r\n")
time.sleep(0.2)
s.read(65536)

pat = re.compile(rb"Read value (0x[0-9a-fA-F]+)")
for a in words:
    s.write(f"devmem 0x{a:08x} 32\r\n".encode())
    buf = b""
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.0:
        buf += s.read(4096)
        m = pat.search(buf)
        if m:
            print(f"0x{a:08x}: 0x{int(m.group(1), 16):08x}")
            break
    else:
        print(f"0x{a:08x}: <no reply>")
