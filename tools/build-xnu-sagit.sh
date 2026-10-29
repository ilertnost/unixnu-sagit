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

# The build script applies $SRC/patches/xnu/*.patch with a shell glob. In a
# UTF-8 locale the collation ignores the dash, so "zzyb-" sorts before "zzy-"
# and the optional observation hooks are applied before the patch their context
# comes from. C collation puts them back in byte order, which is the order the
# script's own comment describes ("reverse newest first, re-apply in name
# order").
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

# The bring-up ladder is a device patch and has to be applied after every
# shared XNU patch, because it inserts code in the middle of hunks those touch
# (start.s bootstrap tables, arm_init's init sequence). The format-1 build
# applies $SRC/patches/xnu/*.patch in filename order and skips what is already
# applied, so a "zzzz-" name puts it last and keeps it idempotent.
LADDER="$SRC/patches/xnu/zzzz-sagit-boot-ladder.patch"
if ! cmp -s "$PATCHES/0006-sagit-boot-ladder.patch" "$LADDER"; then
  install -m 0644 "$PATCHES/0006-sagit-boot-ladder.patch" "$LADDER"
fi

# ...and it has to be *unapplied* before the build starts. The build script
# reverse-checks every patch to decide whether it still has to apply it, and
# the ladder inserts lines into the context that
# native-4k-bootstrap-capacity.patch and zzz-boot-progress.patch use for their
# own reverse-check. Leaving it in place makes those look unapplied, so the
# build tries to apply them again and fails. Reversing it here leaves a tree
# that is exactly "all shared patches applied"; the filename order then puts
# the ladder back on the very last pass.
WORK="$SRC/out/sagit/tree/.cache/xnu-work"
if git -C "$WORK" rev-parse --git-dir >/dev/null 2>&1 &&
   git -C "$WORK" apply --reverse --check "$LADDER" 2>/dev/null; then
  git -C "$WORK" apply --reverse "$LADDER"
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
