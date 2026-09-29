#!/usr/bin/env bash
# Create the Zephyr workspace this project builds against and apply our
# patches.  usage: ./setup-workspace.sh [workspace dir, default ~/zephyr-p4]
#
# Zephyr is pinned to a main-branch SHA: v4.4.2 predates both the Waveshare
# ESP32-P4 boards and PTP support in eth_esp32.c.
set -euo pipefail

ZEPHYR_SHA=9f0253dcc66ccecc92a699dd81cb1034c3f6875b
WS=${1:-$HOME/zephyr-p4}
REPO=$(cd "$(dirname "$0")" && pwd)

if [ ! -d "$WS/.west" ]; then
	mkdir -p "$WS"
	python3 -m venv "$WS/.venv"
	"$WS/.venv/bin/pip" install -q west
	"$WS/.venv/bin/west" init -m https://github.com/zephyrproject-rtos/zephyr --mr main "$WS"
fi

source "$WS/.venv/bin/activate"
cd "$WS"
git -C zephyr fetch -q origin "$ZEPHYR_SHA"
git -C zephyr checkout -q "$ZEPHYR_SHA"
west config manifest.project-filter -- '-.*,+hal_espressif'
west update
pip install -q -r zephyr/scripts/requirements-base.txt \
	-r zephyr/scripts/requirements-build-test.txt esptool
west blobs fetch hal_espressif

for p in "$REPO"/patches/zephyr/*.patch; do
	if git -C zephyr apply --check "$p" 2>/dev/null; then
		git -C zephyr apply "$p"
		echo "applied $(basename "$p")"
	else
		echo "skipped $(basename "$p") (already applied or conflicts)"
	fi
done
