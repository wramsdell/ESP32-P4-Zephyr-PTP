#!/usr/bin/env python3
"""Discrete model of Zephyr's E2E PTP servo, for choosing PI gains.

Mirrors clock.c: one Sync per second, offset = (t2 - t1) - mean_delay, the
PI output sets the absolute rate offset (ppb), and mean_delay is the raw
latest delay sample computed from the latest Sync's t1/t2 and a Delay_Req
sent `phase` seconds after that Sync. `--median N` models a moving-median
delay filter instead.

Reports the response to a single bad Sync (all slaves at 12:29 saw +59.5 us)
and the steady-state offset spread with white timestamp noise.

usage: servo_sim.py [--gains kp,ki ...] [--median N] [--phase S]
"""
import argparse
import random
import statistics


def run(kp, ki, median=1, phase=0.9, n=400, bad_at=None, bad_ns=59500.0,
        noise_ns=0.0, drift_ppb=2400.0, seed=1):
    rng = random.Random(seed)
    path = 11250.0            # true one-way delay, ns
    x = 0.0                   # true slave - master offset, ns
    rate = -drift_ppb         # servo starts locked (integral absorbs drift)
    integral = -drift_ppb
    delays = [path] * median
    measured = []
    t2_minus_t1 = None

    for k in range(n):
        # Sync k arrives: t2 - t1 = path + x (+ noise, + a single bad sample)
        err = rng.gauss(0, noise_ns) if noise_ns else 0.0
        if k == bad_at:
            err += bad_ns
        t2_minus_t1 = path + x + err
        mean_delay = statistics.median(delays)
        offset = t2_minus_t1 - mean_delay
        measured.append(offset)

        e = -offset
        integral += ki * e
        rate = kp * e + integral

        # clock runs until the Delay_Req, `phase` s after the Sync
        x_dreq = x + (drift_ppb + rate) * phase
        noise_d = rng.gauss(0, noise_ns) if noise_ns else 0.0
        t4_minus_t3 = path - x_dreq + noise_d
        delay = (t2_minus_t1 + t4_minus_t3) / 2
        delays = (delays + [delay])[-median:]

        x = x + (drift_ppb + rate) * 1.0
    return measured


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gains", nargs="+",
                    default=["0.7,0.3", "0.5,0.1", "0.3,0.05", "0.2,0.02", "0.1,0.01"])
    ap.add_argument("--median", type=int, default=1)
    ap.add_argument("--phase", type=float, default=0.9)
    args = ap.parse_args()

    for g in args.gains:
        kp, ki = (float(v) for v in g.split(","))
        imp = run(kp, ki, args.median, args.phase, n=60, bad_at=5)[5:15]
        peak = max(abs(v) for v in imp)
        settle = next((i for i in range(len(imp) - 1, -1, -1) if abs(imp[i]) > 1000), -1) + 1
        noisy = run(kp, ki, args.median, args.phase, n=4000, noise_ns=120.0)[500:]
        print(f"kp={kp:<4} ki={ki:<5} median={args.median:<2} bad-sync response (us): "
              + " ".join(f"{v / 1000:+.1f}" for v in imp[:8])
              + f" | peak {peak / 1000:.1f} us, >1 us for {settle} s"
              + f" | noise stdev {statistics.pstdev(noisy):.0f} ns")


if __name__ == "__main__":
    main()
