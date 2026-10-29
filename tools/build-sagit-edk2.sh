#!/bin/bash
# Build EDK2 UEFI for Xiaomi Mi 6 (sagit) and package it as an Android boot image.
#
# Source tree: edk2-porting/edk2-msm8998, branch "new". sagit is supported
# upstream; the only local changes are the GCC 13 build fixes in patches/.
#
# Usage:
#   ROOT=/path/to/edk2-msm8998 TOOLCHAIN=/path/to/arm-gnu-toolchain \
#     tools/build-sagit-edk2.sh
#
# Output: $ROOT/workspace/out/boot-sagit.img
#
# This script lives in the unixnu-sagit repository; it expects the EDK2 tree and
# the UniXNU sources next to it (see README.md).

set -eo pipefail

ROOT=${ROOT:-/data/opencode/edk2-msm8998}
TOOLCHAIN=${TOOLCHAIN:-/data/opencode/toolchains/arm-gnu-toolchain-13.2.Rel1-x86_64-aarch64-none-linux-gnu}
DEVICE=${DEVICE:-sagit}
MODE=${MODE:-RELEASE}
UNIXNU_SRC=${UNIXNU_SRC:-/data/opencode/unixnu-src}
MU_BASECORE=${MU_BASECORE:-$UNIXNU_SRC/out/feeds/mu_basecore-89034cbb}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)

# The pinned EDK2 core predates the MdePkg FDT API the UniXNU modules use.
# The overlay is idempotent and touches nothing else in the core.
ROOT="$ROOT" MU_BASECORE="$MU_BASECORE" "$HERE/apply-edk2-unixnu-overlay.sh"

export PATH="$TOOLCHAIN/bin:$PATH"
export CROSS_COMPILE="aarch64-none-linux-gnu-"
export GCC5_AARCH64_PREFIX="$CROSS_COMPILE"

export WORKSPACE="$ROOT/workspace"
export CONF_PATH="$WORKSPACE/Conf"
export EDK_TOOLS_PATH="$ROOT/Common/edk2/BaseTools"
export PACKAGES_PATH="$ROOT/Common/edk2:$ROOT/Common/edk2-platforms:$ROOT/GPLDrivers/Library/simple-init:$ROOT"
# The UniXNU boot manager and the DarwinBoot loader are vendored read-only
# packages outside this tree; PACKAGES_PATH is what makes their INFs visible.
export PACKAGES_PATH="$PACKAGES_PATH:$UNIXNU_SRC/package:$UNIXNU_SRC/drivers/DarwinBoot"
# SagitPkg holds the firmware pieces that are ours (GPL-3.0-or-later).
export PACKAGES_PATH="$PACKAGES_PATH:$REPO/firmware"
export PYTHON_COMMAND=python3

mkdir -p "$WORKSPACE" "$CONF_PATH"

# Refresh every template each run. A stale build_rule.txt from a different
# EDK2 revision silently produces makefiles that reference variables the
# matching tools_def does not define, and the failure shows up far away.
cp -f "$EDK_TOOLS_PATH/Conf/tools_def.template"  "$CONF_PATH/tools_def.txt"
cp -f "$EDK_TOOLS_PATH/Conf/build_rule.template" "$CONF_PATH/build_rule.txt"
cp -f "$EDK_TOOLS_PATH/Conf/target.template"     "$CONF_PATH/target.txt"

echo "== toolchain =="
aarch64-none-linux-gnu-gcc --version | head -1

echo "== BaseTools =="
make -C "$EDK_TOOLS_PATH" -j"$(nproc)" >/dev/null

cd "$ROOT"

# SimpleInit embeds a generated rootfs; the generator has to run before the
# firmware build or SimpleInitRootFS.inf points at files that do not exist.
SIMPLE="$ROOT/GPLDrivers/Library/simple-init"
if [ ! -f "$SIMPLE/build/rootfs.c" ]; then
  echo "== SimpleInit rootfs =="
  mkdir -p "$SIMPLE/build" "$SIMPLE/root/usr/share/locale"
  for po in "$SIMPLE/po/"*.po; do
    [ -f "$po" ] || continue
    name=$(basename "$po" .po)
    mkdir -p "$SIMPLE/root/usr/share/locale/$name/LC_MESSAGES"
    msgfmt -o "$SIMPLE/root/usr/share/locale/$name/LC_MESSAGES/simple-init.mo" "$po"
  done
  bash "$SIMPLE/scripts/gen-rootfs-source.sh" "$SIMPLE" "$SIMPLE/build"
fi

source Common/edk2/edksetup.sh >/dev/null

DSC="Platforms/Msm8998Pkg/Device/xiaomi/${DEVICE}/${DEVICE}.dsc"

echo "== build ${DEVICE} (${MODE}) =="
build -a AARCH64 -t GCC5 -b "$MODE" -p "$DSC" -n "$(nproc)"

FV=$(find "$WORKSPACE/Build" -name "MSM8998PKG_UEFI.fd" -print -quit)
[ -n "$FV" ] || { echo "no FV produced" >&2; exit 1; }

echo "== package =="
OUT="$WORKSPACE/out"
mkdir -p "$OUT"
DTB="Platforms/Msm8998Pkg/Device/xiaomi/${DEVICE}/DeviceTreeBlob/Android/Android-${DEVICE}.dtb"

# The UniXNU payload volume (tools/mksagit-payload.py) goes in as the Android
# ramdisk. ABL copies it to the address in the header and patches
# linux,initrd-start/end into the device tree; SagitRamDiskDxe turns it
# back into a block device. Without PAYLOAD the image boots to the shell.
PAYLOAD=${PAYLOAD:-}
if [ -n "$PAYLOAD" ]; then
  [ -f "$PAYLOAD" ] || { echo "payload not found: $PAYLOAD" >&2; exit 1; }
  RAMDISK="$PAYLOAD"
else
  : > "$WORKSPACE/ramdisk"
  RAMDISK="$WORKSPACE/ramdisk"
fi

gzip -c "$FV" > "$OUT/uefi-${DEVICE}.img.gz"
cat "$OUT/uefi-${DEVICE}.img.gz" "$DTB" > "$OUT/uefi-${DEVICE}.img.gz-dtb"
# Addresses are spelled out with base 0: kernel and tags at 0x10000000 (the
# values this bootloader has been booting since the first flash), the payload
# at 0xE0000000, which is inside DRAM and far above the firmware's own copy at
# 0xD0000000.
python3 ./mkbootimg.py \
  --kernel "$OUT/uefi-${DEVICE}.img.gz-dtb" \
  --ramdisk "$RAMDISK" \
  --base 0x00000000 \
  --kernel_offset 0x10000000 \
  --ramdisk_offset ${RAMDISK_OFFSET:-0xE0000000} \
  --tags_offset 0x10000000 \
  --os_version 12.0.0 \
  --os_patch_level 2022-08 \
  --header_version 1 \
  -o "$OUT/boot-${DEVICE}.img"

ls -la "$OUT/boot-${DEVICE}.img"

ls -la "$OUT/boot-${DEVICE}.img"
