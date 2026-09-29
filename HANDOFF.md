# Handoff: Zephyr PTP proof-of-concept for ESP32-P4

Written from a sibling session working in `~/owl-ptp-test` (the ESP-IDF-based
"Owl" project). That project has been evaluating ESP32-P4 boards for IEEE 1588
PTP time sync as part of a pivot from RP2350. This document hands off a single
open question to a fresh Zephyr-based project: **can we get real
hardware-timestamped PTP over UDP/IPv4 on this hardware**, which ESP-IDF
cannot currently do.

## Why Zephyr, in one paragraph

ESP-IDF's only PTP daemon (`examples/ethernet/ptp/components/ptpd`, a port of
NuttX's `ptpd`) is L2-only by construction — a macro (`ESP_PTP`) is
unconditionally defined, which compiles out the only code that would open real
UDP sockets. Getting real UDP transport by patching ESP-IDF is possible but
splits into two very different jobs: **software-timestamped UDP is a few days
of work** (port the already-drafted-but-dead NuttX `AF_INET` branch off
NuttX-only calls onto lwIP-native ones); **hardware-timestamped UDP is weeks**,
because lwIP has no equivalent of Linux's `SO_TIMESTAMPING`/cmsg mechanism to
deliver a hardware RX/TX timestamp back through a normal BSD socket — ESP-IDF's
only hardware-timestamp delivery path (L2TAP) is wired specifically to its
raw-Ethernet-frame ioctl interface, not to sockets. Zephyr's native networking
stack already has that hard part built: its PTP subsystem
(`subsys/net/lib/ptp`) supports genuine UDP/IPv4 transport **as the default**,
with real `SO_TIMESTAMPING` + cmsg hardware-timestamp delivery wired for both
L2 and UDP alike. So the same goal that's a multi-week stack-surgery project
under ESP-IDF may be a small, contained driver patch under Zephyr.

## Full rationale (how we got here)

The parent project spent a long investigation trying to get real L3/UDP PTP
transport working on ESP32-P4 under ESP-IDF (v5.5.5, then upgraded to v6.1
partly for this reason). Three independent leads were traced to ground truth
by reading actual source, not documentation claims:

1. **Espressif's official `ptpd` example component**
   (`examples/ethernet/ptp/components/ptpd/ptpd.c` in the esp-idf tree).
   `ptpd.h` unconditionally does `#define ESP_PTP 1`. Every place in `ptpd.c`
   that reads `state->config->af` (the field that's supposed to pick
   `AF_PACKET` vs `AF_INET`) is inside `#ifndef ESP_PTP` / the `#else` of
   `#ifdef ESP_PTP` — dead code on this target, always. The live path
   (`#ifdef ESP_PTP`) opens `/dev/net/tap` (L2TAP), filters on
   `ETHERTYPE_PTP`, and hand-builds raw Ethernet frames. Confirmed against
   the upstream `README.md` at the `v6.1` tag via the GitHub API — it says
   outright: *"The PTP protocol is transported over **Ethernet at Layer 2
   (L2)**... via the **L2 TAP interface**."* `AF_PACKET` also doesn't compile
   in application code that sets `.af = AF_PACKET`, simply because lwIP never
   defines that constant at all (no raw-packet-socket support exists in
   lwIP) — confirmed via full-tree grep of the esp-idf checkout.

2. **`scrambletools/esp_ptp`** (a third-party fork registered on the ESP
   Component Registry, README claims "Standard PTP (IEEE 1588) — UDP-based
   client/server, default profile"). Downloaded and grepped every `.c`/`.h`
   file in the repo: zero occurrences of `AF_INET`, `AF_PACKET`, or `socket(`
   anywhere. It's the same NuttX-derived `ptpd.c` (still carries the
   unconditional `ESP_PTP` macro), enhanced with gPTP/AVB features
   (peer-delay, multi-port, Wi-Fi FTM/beacon carrier), but the UDP claim in
   its README does not match its code.

3. **`DatanoiseTV/aes67-esp32p4`** (an AES67 audio-over-IP project, subject of
   an Espressif developer blog post, June 2026). Its PTP wrapper
   (`components/aes67/src/aes67_ptp.c`) directly includes `esp_vfs_l2tap.h`
   and calls `ptpd_start(PTP_INTERFACE_NAME)` (old single-arg signature). Its
   vendored `ptpd.c` is a pre-6.1-vintage copy of the same Espressif file,
   same unconditional `ESP_PTP` gate. AES67 mandates PTPv2 over UDP multicast
   for interop with third-party gear (Dante, Ravenna, etc.) — this project's
   PTP is L2-only, so despite the branding it cannot actually join a
   standard AES67 clock domain with non-ESP32 hardware.

   (Separately, during this same conversation the user was given a fabricated
   menuconfig transcript — supposedly showing a "Component config → PTP
   Configuration → Transport Protocol: Layer 2 / Layer 3 (UDP/IPv4)" menu —
   by some other source. A full-tree grep across all of ESP-IDF's `Kconfig*`
   files for "Transport Protocol", "PTP Configuration", and "Layer 3 (UDP"
   found zero matches anywhere. That menu does not exist; treat any
   secondhand claims about this codebase's capabilities with the same
   skepticism until verified against actual source.)

**The hardware itself is not the limitation.** The ESP32-P4 EMAC (a Synopsys
DesignWare-class GMAC) has a real, currently-unused register bit for exactly
this:

- `timestamp_ctrl.en_proc_ptp_ipv4_udp` —
  `esp-idf/components/soc/esp32p4/register/hw_ver3/soc/emac_ptp_struct.h:29`
  ("Enable Processing of PTP Frames Sent over IPv4-UDP")
- Low-level setter already exists: `emac_ll_ts_ptp_ip4_enable()` —
  `esp-idf/components/esp_hal_emac/esp32p4/include/hal/emac_ll.h:696`
- But `emac_hal_ptp_start()` —
  `esp-idf/components/esp_hal_emac/emac_hal.c:388` — only ever calls
  `emac_ll_ts_ptp_ether_enable(hal->ptp_regs, true)` (line 393). The IPv4/UDP
  enable is never called anywhere in the ESP-IDF driver tree.

So: real hardware support exists, ESP-IDF's driver never turns it on, and even
if it did, lwIP has nowhere to deliver that timestamp to a UDP socket.

## Why Zephyr specifically closes this gap

Checked against the upstream `zephyrproject-rtos/zephyr` repo (`main` branch,
latest release `v4.4.2`, published 2026-08-07):

- **Board support already exists for this exact hardware family**:
  `boards/waveshare/esp32p4_eth` (matches the Waveshare ESP32-P4-PoE-ETH
  boards this project already owns) and
  `boards/waveshare/esp32p4_wifi6_dev_kit` (matches the newer WiFi6 dev kit
  board). Likely board target strings:
  `waveshare_esp32p4_eth/esp32p4/hpcore` and
  `esp32p4_wifi6_dev_kit/esp32p4/hpcore` (verify exact identifiers with
  `west boards` once the SDK is set up — not yet confirmed in this session).

- **The Ethernet driver already wires hardware timestamps into the OS**:
  `drivers/ethernet/eth_esp32.c` has real `CONFIG_PTP_CLOCK_ESP32` support —
  TX timestamp requested per-packet via `net_pkt_is_tx_timestamping()`,
  latched from `TDES0.TxTimestampStatus`/`TimeStampHigh/Low` on the transmit
  descriptor and reported back via `net_pkt_set_timestamp()` +
  `net_if_add_tx_timestamp()`; RX timestamps latched from
  `RDES0.TSAvailIPChecksumErrGiantFrame` similarly. A full Zephyr
  `ptp_clock` device is registered (`eth_esp32_ptp_clock_set/get/adjust/
  rate_adjust`), which itself calls **the same `emac_hal_ptp_start()`
  ESP-IDF C function** (`emac_hal_ptp_config_t`, same struct/API) — Zephyr
  vendors ESP-IDF's `esp_hal_emac` HAL as a shared module rather than
  reimplementing it.

- **Zephyr's native PTP subsystem genuinely supports UDP as the default
  transport**, not a dead branch: `subsys/net/lib/ptp/Kconfig` line 183-185:
  ```
  choice PTP_NETWORKING_PROTOCOL
      prompt "PTP Networking Protocol used by PTP Stack"
      default PTP_UDP_IPV4_PROTOCOL
  ```
  with `PTP_UDP_IPV6_PROTOCOL` and `PTP_IEEE_802_3_PROTOCOL` (L2, 802.1AS
  style) as the other choices. In `subsys/net/lib/ptp/transport.c`,
  `ptp_transport_recv()` dispatches to `transport_recv_l2_msg()` or
  `transport_recv_udp_msg()` purely based on that Kconfig choice, and **both**
  paths retrieve hardware timestamps identically: real `zsock_socket()`/
  `zsock_recvmsg()` BSD-style sockets, `ZSOCK_SO_TIMESTAMPING` set via
  `zsock_setsockopt()` (lines ~94, ~326), timestamp extracted from
  `NET_CMSG_SPACE(sizeof(struct net_ptp_time))` ancillary data on both the L2
  and UDP recv functions. This is exactly the missing piece under ESP-IDF —
  here it's already built and (for other boards, at least) already proven to
  work.

- **The one real unknown, and the actual point of the experiment**: since
  `eth_esp32.c`'s PTP clock init calls the *same* `emac_hal_ptp_start()` that
  ESP-IDF calls, and that function still only enables
  `en_proc_ptp_ether_frm`, selecting Zephyr's default `PTP_UDP_IPV4_PROTOCOL`
  transport on this board almost certainly will **not** get real hardware
  timestamps out of the box — the EMAC hardware won't recognize
  UDP-encapsulated PTP frames as PTP at all, so RX timestamp capture will
  likely come back invalid/absent for the UDP path specifically. This has not
  been tested; it's a prediction from reading the driver, not an observed
  result. Zephyr's own PTP sample README lists only ST Nucleo H5/H7/F7 boards
  and `frdm_mcxn947` as tested hardware for the `samples/net/ptp` app —
  ESP32-P4 is not among them, so this combination is genuinely unproven
  territory upstream, not just untested by us.

## Proposed experiment (single board, narrow scope)

Goal: answer one question — *can this hardware deliver real hardware
timestamps over a UDP-transported PTP socket, and if not out of the box, does
patching the one missing register-enable call fix it?* — before committing to
porting the rest of the Owl PTP application (GPIO pulse-train generation,
EMAC-target-time-busy-bit watchdog + forensic ring buffer, multi-board fleet
management) to Zephyr.

1. **Bring up the SDK and confirm board identity.** `west init`, install the
   Zephyr SDK, then `west boards | grep -i esp32p4` to get the exact target
   strings for `waveshare_esp32p4_eth` and `esp32p4_wifi6_dev_kit`. Build and
   flash `samples/hello_world` to one board (old-kit PoE-ETH board first,
   since that's the majority of the fleet) as a pure bring-up sanity check
   before touching networking.

2. **Confirm baseline Ethernet works.** Build/flash a basic networking sample
   (e.g. `samples/net/dhcpv4_client` or similar) to confirm link-up, PHY
   negotiation (IP101GRI over RMII — same PHY/interface this project already
   validated works under ESP-IDF), and basic IP connectivity, independent of
   PTP.

3. **Build `samples/net/ptp` for this board** with default config
   (`CONFIG_PTP_UDP_IPV4_PROTOCOL=y`, the Kconfig default — no override
   needed). Point it at the same Linux PTP master infrastructure already
   validated in the ESP-IDF project, but note: that master was run as
   `ptp4l -2` (802.3/L2 domain) for the old L2-only firmware. **For this
   experiment, run `ptp4l -4` instead** (or whatever flag selects UDP/IPv4
   transport) since we're now testing genuine UDP. Confirm via the sample's
   shell (`net ptp`) that Announce/Sync messages are being exchanged and the
   clock is converging — this validates the transport layer end-to-end even
   before hardware timestamps are addressed.

4. **Check whether hardware RX timestamps are actually populated** for the
   UDP path. The prediction above is that they won't be, and traffic will
   silently fall back to host/software timestamps (Zephyr's transport code
   has a `transport_set_host_timestamp_now()`-style fallback for exactly this
   case on the L2 path — check whether an equivalent fallback exists for the
   UDP path, or whether it just reports `rx_timestamp_valid = false` and lets
   the PTP servo decide). Confirm/refute this prediction with actual
   hardware.

5. **If timestamps are absent, apply the driver patch**: in the vendored
   `hal_espressif`/`esp_hal_emac` copy that ships as a Zephyr module, add a
   call to `emac_ll_ts_ptp_ip4_enable(hal->ptp_regs, true)` inside (or
   alongside) `emac_hal_ptp_start()` — likely gated behind a new Kconfig
   option so it doesn't silently change ESP-IDF's own behavior if this
   module is shared/synced with upstream ESP-IDF sources. Rebuild, reflash,
   and re-check step 4.

6. **Once hardware timestamps are confirmed live over UDP**, run the same
   statistical comparison methodology already validated in the ESP-IDF
   project (paired steady-state capture windows, offset stdev and worst-case
   excursion per board) to compare: UDP+software-timestamp vs
   UDP+hardware-timestamp vs the existing ESP-IDF L2+hardware-timestamp
   baseline (already measured there: e.g. board 2 stdev 153.7 ns
   hardware-timestamped L2 vs 193.4 ns software-timestamped L2, worst-case
   405 ns vs 741 ns). This tells us whether the UDP path is competitive with
   what we already have, not just "does it technically work."

7. **Decision point**: only after steps 1-6 validate the core hypothesis does
   it make sense to scope porting the rest of the application (GPIO
   pulse-train generation and its phase-alignment logic, the watchdog/ring
   buffer built to work around ESP-IDF's `emac_hal_ptp_set_target_time()`
   busy-bit driver bug — check whether that same bug exists in Zephyr's
   shared HAL, since it may already be present or may already be fixed
   independently — and eventually the full 4-board fleet) to Zephyr.

## Hardware/environment facts carried over from the ESP-IDF project

- 4x Waveshare ESP32-P4-PoE-ETH boards (IP101GRI PHY, RMII interface), plus at
  least one ESP32-P4-WIFI6-DEV-KIT (newer silicon revision, v3.x).
  MAC-to-board-number mapping is permanent (cable color is not):
  Board 1 = `e8:f6:0a:e4:44:e1`, Board 2 = `e8:f6:0a:e7:1f:39`,
  Board 3 = `e8:f6:0a:e4:45:31`, Board 4 = `e8:f6:0a:e4:28:86`.
  New WiFi6 kit MAC: `e8:f6:0a:e2:c7:24`.
- Dev machine (`rds-gt105`) has no passwordless sudo — any `sudo`-requiring
  step (e.g. `tcpdump`, flashing permissions) needs the user to run it
  themselves in their own terminal, or use tools available to the `wireshark`
  group (e.g. `tshark` works directly without sudo for capture).
- The Linux PTP master used for comparison testing will need `-4` (UDP/IPv4)
  instead of the `-2` (802.3) flag used throughout the ESP-IDF project's
  history for this reason.
- No oscilloscope currently available at this location (moved); a Digilent
  Digital Discovery was mentioned as a future acquisition for PPS/edge-timing
  capture, pending header soldering — status unknown as of this handoff.

## What's explicitly out of scope for this handoff

- Multi-board fleet bring-up, GPIO pulse-train generation/phase alignment,
  and the EMAC busy-bit watchdog workaround are deliberately deferred until
  the core transport question above is answered on a single board.
- No code has been written yet in this repository — this document is the
  starting point, not a summary of work already done here.
