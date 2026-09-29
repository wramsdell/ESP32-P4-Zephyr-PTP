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
