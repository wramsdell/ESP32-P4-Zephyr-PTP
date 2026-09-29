#!/usr/bin/env python3
"""Join the PTP IPv4 multicast groups on an interface address and hold them.

Makes an IGMP-snooping switch forward 224.0.1.129 (and optionally
224.0.0.107 for P2P) to this host so tshark can see PTP-over-UDP traffic
without running ptp4l.  usage: mcast_join.py <local_ipv4> [seconds]
"""
import socket
import struct
import sys
import time

local = sys.argv[1]
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 1e9
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for grp in ("224.0.1.129", "224.0.0.107"):
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                 struct.pack("4s4s", socket.inet_aton(grp), socket.inet_aton(local)))
print(f"joined PTP groups on {local}", flush=True)
time.sleep(secs)
