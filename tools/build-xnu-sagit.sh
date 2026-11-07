#!/bin/bash
# Build XNU for the Xiaomi Mi 6 (sagit) from the UniXNU base.
#
# Source tree: the UniXNU checkout that carries the Linux toolchain driver.
# The device repository is this repository itself (format-2 board.toml at its
# root), and everything in it is ours.
#
# Usage:
#   SRC=/path/to/unixnu-src TOOLCHAIN=/path/to/unixnu-toolchain \
#     tools/build-xnu-sagit.sh
#
# Output: $SRC/out/sagit/ipados-17.7.11-21H461/sagit/tree/build/xnu/
#         ios-obj/DEVELOPMENT_ARM64_BCM2837/
#         mach.development.bcm2837.unstripped.noctf

set -eo pipefail

SRC=${SRC:-/data/opencode/unixnu-src}
TOOLCHAIN=${TOOLCHAIN:-/data/opencode/unixnu-toolchain}
DEVICE_DIR=$(cd "$(dirname "$0")/.." && pwd)
PATCHES="$DEVICE_DIR/patches"

# LLVM is several gigabytes of intermediate objects; keep them off the root
# filesystem, which is small on this host.
export TMPDIR=${TMPDIR:-/data/opencode/tmp}
export UNIXNU_TOOLCHAIN="$TOOLCHAIN"

# In a UTF-8 locale the collation ignores the dash, so "zzyb-" sorts before
# "zzy-" and an optional observation hook would be applied before the patch
# its context comes from. C collation puts them back in byte order.
export LC_ALL=C

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

# The device patch series (patches/xnu/series) carries the bring-up ladder
# and the CPU-cluster fix. The resolver applies the full selected series —
# arch, SoC, OS, then device, ladder last — on a fresh clone whenever the
# inputs change, so no install-into-the-shared-glob dance is needed any more.

cd "$SRC"

echo "== config =="
./unixnu defconfig sagit_defconfig -D "$DEVICE_DIR" 2>&1 | tail -1

echo "== kernel =="
./unixnu build kernel

KERNEL="$SRC/out/sagit/ipados-17.7.11-21H461/sagit/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
if [ ! -f "$KERNEL" ]; then
  # Fall back to the format-1 output location while the first format-2 build
  # is being verified.
  KERNEL="$SRC/out/sagit/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
fi
echo "== output =="
ls -l "$KERNEL"
sha256sum "$KERNEL"
