#!/usr/bin/env python3
"""Minimal unprivileged PTPv2-over-UDP/IPv4 "grandmaster" for RX-path tests.

Sends Announce (general, port 320), two-step Sync (event, port 319) and
Follow_Up (general, port 320) to 224.0.1.129 once per second.  It does not
answer Delay_Req and its timestamps are plain host time, so it cannot be
used to measure sync quality -- only to check whether the slave accepts
Syncs, i.e. whether its hardware RX timestamping sees UDP PTP frames.

usage: fake_gm.py <local_ipv4> [seconds]
"""
import socket
import struct
import sys
import time

PTP_MCAST = "224.0.1.129"
CLOCK_ID = bytes.fromhex("aabbccfffe000001")
PORT_NUM = 1


def header(msg_type, length, seq, control, log_interval, flags0=0x00, flags1=0x08):
    return struct.pack(
        ">BBHBBBBq4s8sHHBb",
        msg_type & 0x0F,          # transportSpecific=0 | messageType
        2,                        # versionPTP
        length,
        0,                        # domainNumber
        0,
        flags0, flags1,           # flags (twoStep in flags0, ptpTimescale in flags1)
        0,                        # correctionField
        b"\0" * 4,
        CLOCK_ID, PORT_NUM,       # sourcePortIdentity (split below)
        seq,
        control,
        log_interval,
    )


def ts(ns):
    sec, nsec = divmod(ns, 1_000_000_000)
    return struct.pack(">HIL", sec >> 32, sec & 0xFFFFFFFF, nsec)


def announce(seq):
    body = ts(0) + struct.pack(
        ">hBBBBHB8sHB",
        37,                        # currentUtcOffset
        0,
        0,                         # grandmasterPriority1 (best possible)
        6, 0x21, 0x4E5D,           # clockClass, clockAccuracy, offsetScaledLogVariance
        0,                         # grandmasterPriority2
        CLOCK_ID,
        0,                         # stepsRemoved
        0xA0,                      # timeSource INTERNAL_OSCILLATOR
    )
    return header(0xB, 64, seq, 5, 1) + body


def sync(seq):
    return header(0x0, 44, seq, 0, 0, flags0=0x02) + ts(0)


def follow_up(seq, t_ns):
    return header(0x8, 44, seq, 2, 0) + ts(t_ns)


def main():
    local = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 1e9
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
    s.bind((local, 0))

    t_end = time.monotonic() + secs
    seq = 0
    while time.monotonic() < t_end:
        s.sendto(announce(seq), (PTP_MCAST, 320))
        t = time.time_ns()
        s.sendto(sync(seq), (PTP_MCAST, 319))
        s.sendto(follow_up(seq, t), (PTP_MCAST, 320))
        seq = (seq + 1) & 0xFFFF
        time.sleep(1.0)


if __name__ == "__main__":
    main()
