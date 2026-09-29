#!/usr/bin/env bash
# Create the ~/zephyr-p4/smp worktrees used by TREE=smp ./build.sh:
#   zephyr         = our pin + zephyr#120082 (esp32s3 SMP, shared SMP plumbing)
#                    + zephyr#120181 (esp32p4 SMP) + patches/zephyr/*.patch
#   hal_espressif  = our pin + the 5 commits zephyr#120082's manifest points at
#                    (edd876f7, hal_espressif#658 before it was force-pushed)
# Conflicts in west.yml and doc/ are resolved with ours; the manifest is not
# used here and the docs don't matter. One esp32s3 Kconfig conflict needs
# both sides kept and is fixed up automatically.
# Everything stays on local branches named smp-exp; nothing is pushed.
set -euo pipefail

WS=${ZEPHYR_WS:-$HOME/zephyr-p4}
REPO=$(cd "$(dirname "$0")" && pwd)
PIN=9f0253dcc66ccecc92a699dd81cb1034c3f6875b
HAL_PIN=0bd7ba4cdeb218fad291ae06f728c335a97e588d
HAL_SMP=edd876f7a16502b7e78b6844ac0532e05f211d16
Z=$WS/zephyr
H=$WS/modules/hal/espressif

git -C "$Z" fetch -q origin pull/120082/head:pr-120082 pull/120181/head:pr-120181
git -C "$H" fetch -q https://github.com/zephyrproject-rtos/hal_espressif "$HAL_SMP"

pick() {
	local gd
	gd=$(git rev-parse --git-dir)
	git cherry-pick "$1" >/dev/null 2>&1 || true
	while [ -e "$gd/CHERRY_PICK_HEAD" ] || [ -d "$gd/sequencer" ]; do
		for u in $(git diff --name-only --diff-filter=U); do
			case "$u" in
			west.yml|doc/*)
				git checkout -q --ours -- "$u"
				;;
			soc/espressif/esp32s3/Kconfig)
				# keep both sides of the select/imply block
				sed -i '/^<<<<<<< /d; /^=======$/d; /^>>>>>>> /d' "$u"
				;;
			*)
				echo "unexpected conflict: $u" >&2
				exit 1
				;;
			esac
			git add -- "$u"
		done
		if git diff --cached --quiet; then
			git cherry-pick --skip >/dev/null 2>&1 || true
		else
			GIT_EDITOR=true git cherry-pick --continue >/dev/null 2>&1 || true
		fi
	done
}

if [ ! -e "$WS/smp/zephyr" ]; then
	git -C "$Z" worktree add -q -b smp-exp "$WS/smp/zephyr" "$PIN"
	(
		cd "$WS/smp/zephyr"
		pick "$PIN..pr-120082"
		pick "$(git merge-base "$PIN" pr-120181)..pr-120181"
		for p in "$REPO"/patches/zephyr/*.patch; do
			git apply "$p"
			git add -A
			git commit -q -m "local: $(basename "$p" .patch)"
		done
	)
fi

if [ ! -e "$WS/smp/hal_espressif" ]; then
	git -C "$H" worktree add -q -b smp-exp "$WS/smp/hal_espressif" "$HAL_PIN"
	(cd "$WS/smp/hal_espressif" && git cherry-pick "$HAL_PIN..$HAL_SMP" >/dev/null)
fi

git -C "$WS/smp/zephyr" log --oneline -1
git -C "$WS/smp/hal_espressif" log --oneline -1
