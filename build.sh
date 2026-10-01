#!/usr/bin/env bash
# usage: ./build.sh <board 1-4> [flash] [extra west build args...]
#
# Board N gets IPv4 192.168.40.10N and builds into build/bN.  Board 2 is
# the default grandmaster (priority1 64); the rest use the Kconfig default.
# The native dwc_mac Ethernet driver is the default; DRIVER=esp32 selects
# the HAL-based eth_esp32.c instead (build/bN-esp32).
# TREE=smp builds against the ~/zephyr-p4/smp worktrees (Zephyr + SMP PRs,
# patched hal_espressif) with CONFIG_SMP=y into build/bN-smp.
# PI_KP / PI_KI (thousandths, e.g. PI_KP=300 PI_KI=50) override the PTP
# servo gains (CONFIG_PRECISION_TIMING_PI_KP/KI; defaults 700/300).
set -euo pipefail

WS=${ZEPHYR_WS:-$HOME/zephyr-p4}
source "$WS/.venv/bin/activate"
export ZEPHYR_BASE="$WS/zephyr"
tree_args=()
if [ "${TREE:-}" = smp ]; then
	export ZEPHYR_BASE="$WS/smp/zephyr"
	tree_args=(-DZEPHYR_MODULES="$WS/smp/hal_espressif" -DEXTRA_CONF_FILE=smp.conf)
fi
export ZEPHYR_SDK_INSTALL_DIR=${ZEPHYR_SDK_INSTALL_DIR:-$HOME/zephyr-sdk-1.0.1}
cd "$(dirname "$0")"

n=${1:?board number 1-4}
shift
declare -A PORT=(
	[1]=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90094322-if00
	[2]=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90094925-if00
	[3]=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90094401-if00
	[4]=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90038724-if00
)
prio=128
[ "$n" = 2 ] && prio=64

bdir="build/b$n"
extra_conf=()
if [ "${DRIVER:-dwc}" = esp32 ]; then
	bdir="build/b$n-esp32"
	extra_conf=(-DCONFIG_ETH_ESP32=y)
fi

[ "${TREE:-}" = smp ] && bdir="$bdir-smp"
# Gain overrides get their own build dir: -D values stick in the CMake cache
# and Zephyr doesn't reconfigure when they're removed, so sharing a dir would
# leave a stale override in the normal build.
gain_args=()
if [ -n "${PI_KP:-}${PI_KI:-}" ]; then
	bdir="$bdir-pi${PI_KP:-d}-${PI_KI:-d}"
	[ -n "${PI_KP:-}" ] && gain_args+=(-DCONFIG_PRECISION_TIMING_PI_KP="$PI_KP")
	[ -n "${PI_KI:-}" ] && gain_args+=(-DCONFIG_PRECISION_TIMING_PI_KI="$PI_KI")
fi

flash=0
if [ "${1:-}" = flash ]; then
	flash=1
	shift
fi

west build -b waveshare_esp32p4_eth/esp32p4/hpcore -d "$bdir" app "$@" -- \
	"${extra_conf[@]}" "${tree_args[@]}" "${gain_args[@]}" \
	-DCONFIG_NET_CONFIG_MY_IPV4_ADDR=\"192.168.40.10$n\" \
	-DCONFIG_PTP_PRIORITY1=$prio

if [ $flash = 1 ]; then
	west flash -d "$bdir" --esp-device "${PORT[$n]}"
fi
