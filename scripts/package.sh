#!/usr/bin/env bash
#
# Copyright (c) 2026 ilertnost
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Package the two boot artifacts from the current tree: the payload volume
# and the Android boot image. A thin wrapper around the sagit-specific
# builders until the official ./unixnu payload/bootimg steps take over with
# the Mu-Silicium firmware (see porting.toml, packaging area).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIXNU_SRC="${UNIXNU_SRC:-/data/opencode/unixnu-src}"
TMPDIR="${TMPDIR:-/tmp}"
export TMPDIR

cd "$ROOT"

K=/data/opencode/unixnu-src/out/sagit/ipados-17.7.11-21H461/sagit/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf
[ -f "$K" ] || K="$UNIXNU_SRC/out/sagit/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped.noctf"
[ -f "$K" ] || { echo "kernel not built yet; run tools/build-xnu-sagit.sh first" >&2; exit 1; }

cp "$K" out/kernel.macho
python3 tools/mksagit-payload.py --kernel out/kernel.macho --adt out/adt/kernel.macho.adt \
    --output out/sagit-payload-volume.img --size-mib 24 \
    --args="-v -enable_kprintf_spam debug=0x14e"

PAYLOAD="$ROOT/out/sagit-payload-volume.img" tools/build-sagit-edk2.sh "$@"

echo "boot image: /data/opencode/edk2-msm8998/workspace/out/boot-sagit.img"
sha256sum /data/opencode/edk2-msm8998/workspace/out/boot-sagit.img
