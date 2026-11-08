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
# Output: $SRC/out/sagit/ios-16.7.16-20H392/sagit/tree/build/xnu/
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

# The OS profile lives in this repository. The driver resolves os:<path>
# against $SRC/profiles/os/<name>, so install it every run and let a changed
# profile replace the previous copy; other profiles are left alone.
mkdir -p "$SRC/profiles/os"
rm -rf "$SRC/profiles/os/ios-16.7.16-20H392"
cp -a "$DEVICE_DIR/profiles/os/ios-16.7.16-20H392" "$SRC/profiles/os/"

# The 20H392 thin kernelcache is unpacked from the user's IPSW. Prelink pins
# its SHA-256, so a different cache fails before anything is written.
export UNIXNU_KERNELCACHE=${UNIXNU_KERNELCACHE:-/data/opencode/ipsw/dmg/kernelcache.arm64}

# Local changes to the driver. It prelinks unconditionally, and a source-only
# board has no [kernel.prelink] and no Apple kexts to merge, so a successful
# link used to end in a failed build. Applied idempotently.
if ! grep -q 'A source-only board has no \[kernel.prelink\]' "$SRC/scripts/unixnu/cli.py"; then
  git -C "$SRC" apply "$PATCHES/0003-unixnu-prelink-when-specified.patch"
fi

# The driver hard-codes the iOS 17 target triple. The OS profile carries the
# target and SDK version instead, so the 8796 build is compiled for iOS 16 and
# an iOS 17 profile still gets its own triple.
if ! grep -q "the profile's iOS version" "$SRC/scripts/unixnu/kernel.py"; then
  git -C "$SRC" apply "$PATCHES/0011-unixnu-ios-target.patch"
fi

# The archive builders assert the iOS 17 deployment they were written against
# and compile their own sources with arm64-apple-ios17.0. Take the target from
# the same OS profile as the kernel instead, so a 16.0 build stays 16.0 from
# the first object to the final link.
if ! grep -q "DEPLOYMENT_TARGET = os.environ" "$SRC/scripts/build-libpthread-kernel.py"; then
  git -C "$SRC" apply "$PATCHES/0018-unixnu-archive-deployment.patch"
fi

# 8796's MakeInc.kernel predates the .noctf kernel naming; the seal step and
# the prelink read that variant, so the CTF-free .unstripped is aliased to it.
if ! grep -q "predates the .noctf naming" "$SRC/scripts/build-xnu-4k-baseline.sh"; then
  git -C "$SRC" apply "$PATCHES/0022-unixnu-8796-noctf-alias.patch"
fi

# The 20H392 Libm kext carries math tables the prelink kit's literal-pool list
# predates; its scanner reads the new records as out-of-range encodings.
if ! grep -q "newer Libm whose tables" "$SRC/kits/XnuPrelink/emit_prelink.py"; then
  git -C "$SRC/kits/XnuPrelink" apply "$PATCHES/0025-unixnu-prelink-20h392-libm-pools.patch"
fi

# The coalesced prelink segments are bound to the occupied span instead of the
# whole original cache, so the file does not carry 30 MiB of zero fill.
if ! grep -q "occupied coalesced span" "$SRC/kits/XnuPrelink/emit_prelink.py"; then
  git -C "$SRC/kits/XnuPrelink" apply "$PATCHES/0026-unixnu-prelink-span-shrink.patch"
fi

# The Mi 6 bootloader refuses boot images whose ramdisk section exceeds
# roughly 36 MiB, so UniXNURamDiskDxe learns to expand an LZMA-alone payload.
if ! grep -q "UNXLZMA1" "$SRC/package/UniXNUPkg/Driver/UniXNURamDiskDxe/UniXNURamDiskDxe.c"; then
  git -C "$SRC" apply "$PATCHES/0027-unixnu-ramdisk-lzma.patch"
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

KERNEL="$SRC/out/sagit/ios-16.7.16-20H392/sagit/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
echo "== output =="
ls -l "$KERNEL"
sha256sum "$KERNEL"
