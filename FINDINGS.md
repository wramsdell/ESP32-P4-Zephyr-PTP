# Findings: hardware-timestamped PTP over UDP/IPv4 on ESP32-P4 under Zephyr

Answers the question posed in `HANDOFF.md`. Short answer: **yes**. Hardware
TX and RX timestamps both work over UDP/IPv4, and two boards lock to each other
at ~100 ns stdev. It took four fixes (two for correctness, one for
robustness, one for board bring-up), and none of them was the fix the handoff
predicted.

## Setup

- Zephyr `main` @ `9f0253dcc66c` (v4.4.2 has neither the Waveshare ESP32-P4
  boards nor PTP support in `eth_esp32.c`), SDK 1.0.1, workspace
  `~/zephyr-p4`, recreated by `./setup-workspace.sh`.
- Target `waveshare_esp32p4_eth/esp32p4/hpcore`, all four PoE-ETH boards
  are silicon v1.3.
- App = upstream `samples/net/ptp` (`app/`), default
  `CONFIG_PTP_UDP_IPV4_PROTOCOL=y`, E2E, hybrid network mode, static
  `192.168.40.10N`. Build/flash: `./build.sh <N> flash`.
- Board 2 is GM (priority1 64), Board 1 slave. Both hardware-timestamped.

## Handoff predictions vs. what happened

| Handoff claim | Result |
|---|---|
| Boards exist in v4.4.2 | No, `main` only. |
| `en_proc_ptp_ipv4_udp` never enabled, so no UDP RX timestamps | **Wrong.** The bit resets to 1 in silicon (read back `timestamp_ctrl = 0x00052E03`, bit 13 set, nothing in HAL/driver writes it). UDP **multicast** PTP event frames are timestamped out of the box. The proposed `emac_ll_ts_ptp_ip4_enable()` patch is unnecessary. |
| UDP path may fall back to software timestamps | There is no fallback on the UDP path: a Sync without an RX timestamp is dropped (`port_sync_rx_timestamp_valid()`). So "it synced" does imply hardware RX timestamps. |

## Fixes (in `patches/zephyr/` and `app/boards/`)

Fixes 2–4 (`eth_esp32.c`) have been reverted from the workspace since
switching to the native `dwc_mac` driver. They're kept for reference in
`docs/eth_esp32-ptp-fixes.reference.patch` and aren't applied by
`setup-workspace.sh`.

1. **PHY reset timing (board DTS)**. The upstream board leaves
   `reset-assert-duration-us` / `reset-deassertion-timeout-ms` at 0. The
   IP101GRI got a zero-length reset and was probed immediately, giving
   `phy_mii: No PHY found at address 1` on 10/10 cold boots (one lucky boot
   right after ESP-IDF firmware came up as "10 Mbit half" instead).
   Overlay sets 10 ms / 20 ms, and it now works 10/10 at 100/full.
   → `app/boards/waveshare_esp32p4_eth_esp32p4_hpcore.overlay`

2. **PTP addend saturated (driver)**. `eth_esp32.c` set the sub-second
   increment equal to the 25 ns source period, giving addend = 2³² →
   0xFFFFFFFF. The clock could never be sped up, and with the HAL's
   `emac_hal_ptp_adj_freq()` any positive correction **wrapped** the addend
   to near zero and nearly stopped the PHC. Increment is now 2× period
   (addend 2³¹). ESP-IDF itself uses 40 ns, so it doesn't hit this.

3. **Rate adjust compounding (driver)**. The `ptp_clock` API's `rate_adjust` is
   "based on its nominal frequency" (STM32 reference driver agrees), but
   `emac_hal_ptp_adj_freq()` multiplies the *current* addend, so every
   servo update compounded. The driver now scales the addend captured at init.

4. **Unicast PTP not timestamped (driver)**. The EMAC's PTP-over-UDP
   detector only matches multicast-addressed frames: the GM got no RX
   timestamp for hybrid-mode unicast Delay_Req (the stack fell back to
   multicast after 3 misses). The driver now enables `en_ts4all`, and the
   misses are gone (verified live via devmem before patching).

5. **Servo stuck between 10 ms and 1 s (PTP stack, not ESP32-specific)**.
   `clock.c` only steps above 1 s. Anything between the 10 ms lock
   threshold and 1 s goes to the PI loop, which saturates, resets, and
   repeats forever. It only bit here because both PHCs start near 0 and
   the boards booted 0.5 s apart (a TAI Linux GM always triggers the 1 s
   step). Now it steps above 10 ms until the servo has locked, the same idea
   as ptp4l's `first_step_threshold`.

## Results (board-to-board, UDP/IPv4, HW timestamps both ends)

10-minute run, all fixes applied, steady state = samples after 60 s uptime:

| metric | value |
|---|---|
| offset stdev | **121.2 ns** |
| offset worst | **+350 ns** |
| offset p99 \|x\| | 300 ns |
| mean | −0.2 ns |
| samples | 539 (1 Hz Sync) |
| path delay | 11.26 µs ± 49.5 ns |
| time to first servo sample | 3 s after entering TIME RECEIVER (step at +1 s) |
| GM warnings / slave outliers / servo resets | 0 / 0 / 0 |

Earlier 4-minute run (fixes 1–3, 5): stdev 115.7 ns, worst −325 ns,
p99 275 ns, n = 240.

ESP-IDF baseline from the Owl project (Board 2 vs Linux **software-timestamped**
master, L2): HW 153.7 ns stdev / 405 ns worst, SW 193.4 / 741. These **aren't
apples-to-apples** (the master and its timestamping differ). Both sets are the
slave's self-reported servo offsets, which measure how well the servo tracks,
not true time error; that needs a PPS/scope measurement.

## Native `dwc_mac` driver (upstream direction, see zephyr#111682)

This is the default build (`CONFIG_ETH_ESP32=n` in the board `.conf`; `DRIVER=esp32 ./build.sh <N>` for the old driver). It selects
the generic Synopsys driver (`drivers/ethernet/dwc_mac`, ESP32 glue from
zephyr#111798, PTP from #114543). Only the PHY-reset overlay and the
`clock.c` step fix apply; the `eth_esp32.c` patches aren't compiled.

- Already gets fixes 2a/2b right on its own (increment 2 × period = 50 ns,
  rate scaled from `default_addend`). `timestamp_ctrl = 0x2603`.
- **Still misses unicast Delay_Req** (3 misses, then multicast fallback), the
  same as fix 4's finding. Open zephyr#120248 makes the RX filter configurable,
  but it defaults to PTP-only when PTP is enabled, so this would persist.
- 5-min two-board run, samples after 60 s: offset stdev **123.0 ns**, worst
  +350 ns, p99 275 ns (n = 239); delay 11.25 µs, stdev 66.8 ns excluding 4
  startup outliers. That matches the patched `eth_esp32.c` (121.2 / +350).

## Four-board run (native driver, multicast delay mode)

`app/prj.conf` no longer sets `PTP_NETWORK_MODE_HYBRID` (it came from the
upstream sample). The stack default is multicast, which avoids the unicast
Delay_Req timestamp miss without any driver change. All four boards were reset
together, Board 2 GM, 5 min, samples after 60 s:

| slave | offset stdev | worst | p99 \|x\| | delay median | delay stdev |
|---|---|---|---|---|---|
| B1 | 156.6 ns | +425 ns | 375 ns | 11.275 µs | 74.1 ns |
| B3 | 145.7 ns | −500 ns | 375 ns | 11.250 µs | 66.4 ns |
| B4 | 119.3 ns | +325 ns | 275 ns | 11.250 µs | 53.8 ns |

- 0 warnings/errors on all four boards. All traffic was multicast; the GM sent
  861 Delay_Resp = 3 × 287 Delay_Req, one per request.
- No clock step. With a common reset the initial offsets were ~1 ms, under the
  10 ms threshold, so the servo slewed.
- Each slave had 11 path-delay outliers, all within the first ~30 s, while the
  servo was slewing hard. There were none afterwards. These are per-slave
  offsets to the GM. Slave-to-slave error isn't measured directly; it
  needs PPS output on a scope.

## PPS output on v1.3 silicon

A temporary shell command (since removed from the app) set PPS0 to a 1 Hz
square wave (`pps_ctrl` at 0x5009872C, `pps_cmd0 = 1`), routed GPIO-matrix
signal 243 (`EMAC_PTP_PPS_PAD_OUT_IDX`) to a pin with input enabled via
`esp_rom_gpio_connect_out_signal()`, and sampled the pad and the internal
`HP_SYSTEM_GMAC_CTRL0.PTP_PPS` bit (0x500E514C bit 0).
Board 1 (v1.3), locked to Board 2, GPIO20, 5 s:

- Pin loopback self-check passes (drive 1/0 → read 1/0).
- Internal PTP_PPS bit: 10 edges (1 Hz square), so the generator runs.
- GPIO20 pad: **0 edges**. The PPS signal isn't connected to the GPIO
  matrix on v1.3, which matches ESP-IDF's "PPS on GPIO from rev 3" note and
  its HAL (`ptp_pps_idx = SIG_GPIO_OUT_IDX` below rev 3.0).

The pulse output the Owl project got on these boards is software: the EMAC
target-time interrupt triggers an ISR that calls `gpio_set_level()`. On the
native `dwc_mac` driver that needs a driver change, because the driver owns
the MAC IRQ and doesn't handle the timestamp/target-time interrupt. On
v3.x boards, hardware PPS needs only app code (signal 243 isn't in Zephyr's
pinctrl sigmap, so route it in C).

## 8-hour soak, four boards (2026-09-29 11:08–19:08)

Native driver, multicast delay mode, B2 GM, `tools/soak_monitor.py`
polling `ptp_diag` every 60 s. **No stall, and 0 warnings/errors on all four
boards.** No board lost multicast RX either, so neither the overnight IGMP
aging nor the B4 lost-wakeup stall reproduced. Why this run differs from
the overnight one is unknown.

| slave | offset stdev (excl. events) | p99 | p99.9 | worst (excl.) | events | stdev incl. events |
|---|---|---|---|---|---|---|
| B1 | 201.7 ns | 500 ns | 675 ns | +4.7 µs | 3 | 1096 ns |
| B3 | 144.0 ns | 350 ns | 450 ns | +3.1 µs | 2 | 798 ns |
| B4 | 194.6 ns | 500 ns | 675 ns | +3.0 µs | 3 | 1146 ns |

About 28.5k samples per slave, excluding each board's first 60 s. An "event"
is a ≥5 µs excursion; its 60 s window is excluded above. All four event
times (11:24 B4, 12:29 all, 13:11 B1, 14:51 all) are explained, or at 11:24
and 12:29 most likely explained, by LAN broadcasts colliding with PTP
event messages in the switch (see below). None occurred in the last 4 h
17 min.

## IGMP snooping: what actually cuts boards off (observed 2026-09-29 19:08–19:14)

After the soak, reflashing only Board 1 (it sends an IGMP join on boot),
plus a 14 s `mcast_join.py` on the host, cut Boards 2–4 off from multicast
within a minute. B3/B4 timed out and became grandmasters, and nobody
answered Delay_Req. With no host involvement, everything recovered at
19:13:36, ~4.5 min after the last join. The switch's behavior that fits:

- With **no registered members** for 224.0.1.129, the switch **floods** the
  group to all ports. The soak ran 8 h this way. The boards' boot-time
  joins aged out, and nothing broke.
- **Any new join** (one board rebooting, or the host joining) registers
  the group with just that port, so every board whose join has aged out
  stops receiving.
- With no IGMP querier, nobody re-reports, so the new entry ages out after
  ~260 s and flooding resumes.

This also explains the overnight failure (Boards 2/3 deaf after individual
boards were reset/probed) and why it didn't reproduce in the soak (all
four reset together, nothing joined afterwards). **Operational rule on this
network:** resetting any single board, or joining from the host, can blind
the rest for ~4–5 min. Real fixes: an IGMP querier on the LAN (the switch's
own, if it has one), periodic IGMP re-reports from the boards (the
`net_ipv4_igmp_resend_reports()` approach from the Pico project), or
disabling snooping for this VLAN.

## SMP (zephyr#120181), first run on Board 1

`TREE=smp` build on v1.3 silicon: "Multicore bootloader", CPU1 up
(`idle 01` present), Ethernet/PTP came up normally. Synced to B2 (the rest
still single-core): 150 s, offset stdev 224 ns, worst −650 ns, p99 550 ns,
delay 11.275 µs ± 114 ns, 0 warnings. That's comparable to B1's single-core
soak (202 ns, p99 500 ns), but too short to call a difference. Not yet tried:
pinning a thread to CPU1, or a long soak under SMP.

## Unfiltered E2E path delay (soak observation)

`ptp_clock_delay()` (`clock.c`) stores each new sample straight into
`current_ds.mean_delay`, so a single bad Delay_Req/Delay_Resp exchange
feeds directly into the next offset. In the 2026-09-29 soak, Board 4 had
one 20.9 µs delay sample (normally 11.25 µs) at ~16 min uptime, and the
servo rang for ~5 s with offsets up to +14.5 µs. That one event took its
offset stdev from ~140 ns to 442 ns. Boards 1 and 3 had no such event in
the first 51 min. ptp4l runs delay through a moving median
(`delay_filter`, length 10), which would have rejected it. This is a
candidate stack fix alongside the step-before-lock one. The cause of the
bad sample (switch queuing or a late timestamp) is unknown.

A second event at 12:29:06 hit **all three slaves at once**: one Sync read
+59.6–59.8 µs on every slave while delay was still normal. The slaves then
rang for ~5–10 s (B1: +59.6, −115.9, +81.6, −55.0 … µs). An identical error on
every slave points upstream of them. The GM's log shows a normal Sync with
no other transmit within 64 ms, so the most likely cause is the switch
holding that one multicast Sync for ~60 µs on all ports, e.g. behind a
~750-byte LAN broadcast. That's unconfirmed; the next soak should capture
non-PTP broadcast/multicast on the host to correlate. It exposes two stack
weaknesses: no rejection of a single out-of-family Sync (the post-lock
outlier threshold is 100 ms), and an underdamped servo (each swing ~0.7–1.9×
the previous, sign-alternating). Excluding the 40 s around the event, B1/B3
over the first 1 h 39 min were 198/142 ns stdev (p99 500/350 ns).

**Confirmed with a host capture at 13:11:42** (B1 only; first delay sample
24.6 µs vs 11.2 µs, then ringing to −13.5/+19.4 µs). The host saw B1's
Delay_Req at .3953938, between a 345-byte DHCP Boot Request (.3953924) and
a 342-byte Boot Reply (.3953940), all broadcast. A Delay_Req queued behind
~350 bytes at 100 Mbit/s loses ~29 µs, matching the ~27 µs of extra
one-way delay the bad sample implies. So these events are **LAN broadcasts
colliding with PTP event messages in a non-PTP-aware switch**, not a board
or driver fault. They'll recur on this shared LAN: DHCP broadcast pairs
show up about once a minute. DSCP/802.1p priority can't fully fix it,
because a frame already on the wire isn't preempted (up to ~123 µs for 1500
bytes at 100 Mbit/s). The robust mitigations are an isolated PTP
network/VLAN or a transparent-clock switch, plus stack-side filtering
(delay median, rejecting single out-of-family Syncs).

**Second all-slave event, also correlated (14:51:04).** B1/B3/B4 all read
a Sync at +16.8–17.2 µs. The host saw that Sync 1.4 µs after a 323-byte
DHCP Boot Request broadcast. That frame occupies a 100 Mbit/s port for
~27 µs, so a Sync arriving mid-frame waits out the remainder (~17 µs).

**The LAN's broadcast load is abnormal:** ~97 non-PTP broadcasts ≥300
bytes per minute. `b0:19:21:23:f7:85` sends a DHCP request every 5.0 s
exactly (1,785 in 149 min), apparently stuck retrying. `52:17:7d:96:e2:aa`
(sends Netgear STP) and the router `74:24:9f:ab:0c:dc` exchange ~38 DHCP
broadcasts/min each. Fixing that device would make these events much
rarer on this network, but only isolation or stack filtering removes the
failure mode.

## APLL for PTP-disciplined I2S (desk study, not measured)

Can the P4's audio PLL be servoed to the PTP clock? Probably yes, but it
needs Zephyr I2S driver changes and one hardware test. From the HAL and
ESP-IDF sources:

- **What it is:** I2S on the P4 can be clocked from `SOC_MOD_CLK_APLL`
  (`SOC_I2S_CLKS`; `SOC_I2S_SUPPORTS_APLL`). The APLL is a fractional-N PLL
  off the 40 MHz crystal:
  `f = 40 MHz × (4 + sdm2 + sdm1/256 + sdm0/65536) / (2 × (o_div + 2))`,
  with the multiplier output 350–500 MHz and the final output 5.3–125 MHz
  (`rtc_clk.c` `rtc_clk_apll_coeff_calc`, `clk_tree_ll.h`).
- **Step size:** the 16-bit fraction gives 1/(65536 × M) with M ≈
  8.75–12.5, i.e. **~1.2–1.7 ppm per LSB**. That's coarse for ppb-level
  steering; toggling between adjacent codes would average finer at the cost
  of slow frequency modulation.
- **Retuning while running is the key unknown.**
  `rtc_clk_apll_coeff_set()` rewrites all coefficients and re-runs PLL
  calibration, which likely glitches the clock. Whether writing only
  `sdm0` on the fly is glitch-free on the P4 is untested.
- **Shared resource:** LCD, CAM and MIPI-DSI can also use the APLL, and
  ESP-IDF locks its frequency once more than one peripheral holds it.
- **Same crystal as the PHC:** the EMAC PTP reference is the same 40 MHz
  crystal (measured 40 MHz). The PTP servo's frequency correction is
  therefore also the crystal's error relative to the master and can be applied
  to the APLL fraction as feed-forward, leaving only a slow phase-trim loop.
- **Measuring phase on v1.3:** there is no hardware capture path (no
  timestamp-capture input on the GPIO matrix, no PPS pin). The I2S TX sync
  counter (`SOC_I2S_SUPPORTS_TX_SYNC_CNT`: BCLK/FIFO counts) read together
  with the PHC in a short critical section gives sub-µs per-sample jitter,
  which is enough for ppb-level frequency and µs-level phase over
  multi-second averaging.
- **Alternative knob:** ESP-IDF's `i2s_channel_tune_rate()` steers MCLK in
  1 Hz steps via the I2S fractional divider. It's digital and glitch-free,
  but adds divider pattern jitter.
- **Zephyr today:** `drivers/i2s/i2s_esp32.c` hard-codes
  `I2S_CLK_SRC_PLL_160M` on the P4, with no APLL option and no rate-tuning
  API. ESP-IDF marks PLL_F160M for I2S as "only supported on P4 hw_ver3",
  while the Zephyr driver comment says it works on the supported revisions, so
  **I2S may not work at all on these v1.3 boards as-is** (untested).
- **Proposed test:** clock I2S from the APLL at 12.288 MHz, step `sdm0`
  repeatedly while running and watch the sync counters for slips. If
  clean, feed the PTP servo's frequency correction into the APLL and
  measure the residual drift.

## Host-side gotchas

- The switch does **IGMP snooping**. UDP PTP multicast only reaches ports that
  joined 224.0.1.129, so passive tshark sees nothing unless
  `tools/mcast_join.py` holds the group. L2 PTP (`01:1b:19:…`) was always
  flooded, which is why this never came up under ESP-IDF.
- `br0` has no software TX timestamping; `ptp4l -i br0` fails with
  "does not support requested timestamping mode". A Linux master needs a
  non-bridged NIC. `tools/ptp4l-udp-master.conf` is ready for that.

## Not done yet

- Step 6 apples-to-apples comparison against the Linux master (needs a
  non-bridged NIC + sudo).
- Target-time / PPS output (and whether the ESP-IDF busy-bit bug exists
  here). Zephyr's driver has no target-time support at all yet.
- Upstreaming fixes 1–5.
- APLL glitch test and PTP-disciplined I2S (see the APLL section).
