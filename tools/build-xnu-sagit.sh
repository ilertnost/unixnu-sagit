#!/bin/bash
# Build XNU for the Xiaomi Mi 6 (sagit) from the UniXNU base.
#
# Source tree: the UniXNU checkout that carries the Linux toolchain driver.
# The device profile lives in this repository under kernel/sagit, and everything
# in it is ours.
#
# Usage:
#   SRC=/path/to/unixnu-src TOOLCHAIN=/path/to/unixnu-toolchain \
#     tools/build-xnu-sagit.sh
#
# Output: $SRC/out/sagit/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/
#         mach.development.bcm2837.unstripped.noctf

set -eo pipefail

SRC=${SRC:-/data/opencode/unixnu-src}
TOOLCHAIN=${TOOLCHAIN:-/data/opencode/unixnu-toolchain}
DEVICE_DIR=$(cd "$(dirname "$0")/.." && pwd)/kernel/sagit
PATCHES=$(cd "$(dirname "$0")/.." && pwd)/patches

# LLVM is several gigabytes of intermediate objects; keep them off the root
# filesystem, which is small on this host.
export TMPDIR=${TMPDIR:-/data/opencode/tmp}
export UNIXNU_TOOLCHAIN="$TOOLCHAIN"

# The shims in the toolchain default to /opt/unixnu-linux, which is not where
# this prefix is installed. The driver reads UNIXNU_TOOLCHAIN, so exporting it
# is enough; nothing needs to live in /opt.
test -x "$TOOLCHAIN/llvm/bin/clang" || {
  echo "toolchain missing at $TOOLCHAIN (scripts/linux/setup-toolchain.py builds it)" >&2
  exit 1
}

# One local change to the driver. It prelinks unconditionally, and a
# source-only board has no [kernel.prelink] and no Apple kexts to merge, so a
# successful link used to end in a failed build. Applied idempotently.
if ! grep -q 'A source-only board has no \[kernel.prelink\]' "$SRC/scripts/unixnu/cli.py"; then
  git -C "$SRC" apply "$PATCHES/0003-unixnu-prelink-when-specified.patch"
fi

# The bring-up ladder is a device patch and has to be applied after every
# shared XNU patch, because it inserts code in the middle of hunks those touch
# (start.s bootstrap tables, arm_init's init sequence). The format-1 build
# applies $SRC/patches/xnu/*.patch in filename order and skips what is already
# applied, so a "zzzz-" name puts it last and keeps it idempotent.
LADDER="$SRC/patches/xnu/zzzz-sagit-boot-ladder.patch"
if ! cmp -s "$PATCHES/0006-sagit-boot-ladder.patch" "$LADDER"; then
  install -m 0644 "$PATCHES/0006-sagit-boot-ladder.patch" "$LADDER"
fi

cd "$SRC"

echo "== config =="
./unixnu defconfig sagit_defconfig -D "$DEVICE_DIR" 2>&1 | tail -1

echo "== kernel =="
./unixnu build kernel

KERNEL="$SRC/out/sagit/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
echo "== output =="
ls -l "$KERNEL"
sha256sum "$KERNEL"
