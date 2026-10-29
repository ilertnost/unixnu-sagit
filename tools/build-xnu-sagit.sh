#!/bin/bash
# Build XNU for the Xiaomi Mi 6 (sagit) from the UniXNU base.
#
# Source tree: the UniXNU checkout that carries the Linux toolchain driver.
# The device profile lives in this repository under xnu/sagit, and everything
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
DEVICE_DIR=$(cd "$(dirname "$0")/.." && pwd)/xnu/sagit
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

cd "$SRC"

echo "== config =="
./unixnu defconfig sagit_defconfig -D "$DEVICE_DIR" 2>&1 | tail -1

echo "== kernel =="
./unixnu build kernel

KERNEL="$SRC/out/sagit/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
echo "== output =="
ls -l "$KERNEL"
sha256sum "$KERNEL"
