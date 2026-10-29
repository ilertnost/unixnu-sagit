#!/usr/bin/env python3
#
# Copyright (c) 2026 ilertnost
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Build the Apple device tree (ADT) for the Xiaomi Mi 6.

The hardware half is compiled from xnu/sagit/dts/sagit.dts and its MSM8998
SoC include. The identity half is data mirrored from Apple's own iPhone10,1
(D20AP) ADT, so XNU sees a platform it recognises. The two halves are joined by
the shared converter in unixnu-src/scripts/unixnu/devicetree.py; that file is
BSD-2-Clause-Patent and is not modified here.

Determinism: every byte is a pure function of the arguments, except
/chosen/random-seed, which the converter fills from os.urandom(64) on every run.
Two runs with the same arguments therefore have the same tree and property
values but different file bytes and SHA-256. The hardware DTB written by
--keep-dtb is byte-for-byte reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "kernel" / "sagit"
DTS = DEVICE / "dts" / "sagit.dts"
SOC_INCLUDE = DEVICE / "soc" / "msm8998" / "dts"
DEFAULT_UNIXNU = Path(os.environ.get("UNIXNU_SRC", "/data/opencode/unixnu-src")) / "scripts"

# /chosen is read by XNU before any driver exists. The identities below are
# literal values from the D20AP ADT extracted from 20H392 (see docs/ADT-SAGIT.md).
# Apple's shipped tree is a template: iBoot replaces the syscfg references and
# many zeros with fused data, so "mirroring a zero" here means "the shape is
# Apple's, the value must be supplied by DarwinBoot or is not meaningful on this
# board". Anything marked OURS is not from the iPhone tree.
IDENTITY = {
    "root": {
        # First entry is what XNU reports as the hardware model; the DTS's own
        # compatible strings are appended after these by the converter.
        "strings": {"compatible": ["D20AP", "iPhone10,1", "AppleARM"]},
        "string": {
            "model": "iPhone10,1",
            "target-type": "D20",
            "target-sub-type": "D20AP",
            "manufacturer": "Apple Inc.",
            "device_type": "bootrom",
        },
    },
    "chosen": {
        "string": {
            "boot-type": "local",
            "crypto-hash-method": "sha2-384",
            # OURS: this kernel is booted from the FAT payload, not from an
            # installed System volume. DarwinBoot may override it.
            "osenvironment": "ramdisk",
        },
        "u32": {
            "development-cert": 0,
            "disable-transport-rm": 0,
            "amfi-only-platform-code": 0,
            "amfi-allows-trust-cache-load": 0,
            "certificate-production-status": 0,
            "certificate-security-mode": 0,
            "debug-enabled": 0,
            "disable-accessory-firmware": 0,
            "disable-av-content-protection": 0,
            "effective-production-status-ap": 0,
            "effective-production-status-sep": 0,
            "effective-security-mode-ap": 0,
            "effective-security-mode-sep": 0,
            "gid-aes-key": 0,
            "isp-horizon": 0,
            "mix-n-match-prevention-status": 0,
            "max-env-partition": 0,
            "max-env-var-data-size": 0,
            "max-env-var-name-size": 0,
            "production-cert": 0,
            "protected-data-access": 1,
            "secure-boot": 0,
            "sepfw-load-at-boot": 1,
            "system-trusted": 0,
            "uid-aes-key": 0,
            "use-recovery-securityd": 0,
            # OURS: BuildManifest.plist, ApBoardID of the d20ap identity. The
            # Apple template carries zero because iBoot fills it from fuse data.
            "board-id": 0x02,
        },
        "u64": {"consistent-debug-root": 0},
        "bytes": {
            "allow-ap-nonce-retrieval": "",
            "boot-manifest-hash": "00" * 48,
            "boot-nonce": "00" * 8,
            "die-id": "00" * 8,
            "dram-vendor": "00" * 16,
            "dram-vendor-id": "00000000",
            "firmware-version": "00" * 256,
            "image4-supported": "",
            "unique-device-id": "00" * 16,
        },
    },
    # Platform defaults: literal u32s from the D20AP ADT. They are scheduler
    # and VM tunables, not hardware facts, so mirroring them is safe.
    "defaults": {
        "u32": {
            "cpx-encryption-mode": 1,
            "kern.io_throttle_period_tier3": 20,
            "kern.io_throttle_window_tier3": 100,
            "kern.max_task_pmem": 1450,
            "kern.maxkfsevents": 1024,
            "kern.maxvnodes": 10000,
            "kern.thread_group_extra_bytes": 64,
            "kern.vm_compressor": 32,
            "l2-ecc-correctable-panic": 1,
            "panic-reset-type": 2,
            "panic-reset-type_pre_p2": 1,
            "sep-is-firmware-payload": 1,
        },
        "bytes": {
            "content-protect": "",
            "data-journaling": "",
            "use-lwvm": "",
        },
    },
    # The D20AP /options node has no properties of its own. The converter still
    # has to emit it: ArmDTPlatform refuses to configure without /options.
    "options": {},
    # Product identity strings and the 20-byte product-id from the D20AP ADT.
    # Hardware capability flags and the camera/facetime/maps subtrees are not
    # mirrored: they describe iPhone 8 hardware this board does not have.
    "product": {
        "string": {
            "artwork-device-idiom": "phone",
            "artwork-display-gamut": "P3",
            "artwork-dynamic-displaymode": "0",
            "baseband-chipset": "mav17",
            "compatible-app-variant": "0",
            "compatible-device-fallback": "iPhone9,1",
            "graphics-featureset-class": "APPLE4",
            "graphics-featureset-fallbacks": "APPLE3:APPLE3v1:APPLE2:APPLE1:GLES2,0",
            "mobiledevice-beta-min-ver": "1190.100.2",
            "mobiledevice-min-ver": "1400",
            "product-description": "iPhone 8",
            "product-name": "iPhone 8",
            "wifi-chipset": "4357",
        },
        "bytes": {
            "product-id": "a9b014bc70d7a991a4db2d1a541562864426b318",
        },
    },
    # Only the descriptive properties are mirrored. The function-* values in
    # Apple's /buttons encode PMIC/GPIO operations for Apple's PMU; no such
    # hardware exists here, and a fake binding would be worse than none.
    "buttons": {
        "string": {"compatible": "buttons", "device_type": "buttons"},
        "strings": {"button-names": ["volup", "voldown", "hold", "ringerab"]},
        "u32": {
            "button-delay_hold": 75000,
            "button-delay_volup": 75000,
            "button-delay_voldown": 75000,
            "button-priority_hold": 1,
            "button-priority_volup": 2,
            "button-priority_voldown": 2,
            "home-button-type": 1,
            "opposed-power-vol-buttons": 1,
        },
    },
}


def configuration(dram_size: int, chip_id: int, unique_chip_id: int) -> dict:
    """The tables generate() consumes. Only payload.dram_size is a check."""
    return {
        "payload": {
            "dram_size": f"0x{dram_size:x}",
            "adt": {
                "chip_id": f"0x{chip_id:x}",
                "unique_chip_id": "required",
            },
        },
        "adt": {key: value for key, value in IDENTITY.items()},
    }


def validate_kernel_args(text: str) -> None:
    """XNU's boot-args live in the boot_args command line, not in the ADT.

    The limit is BOOT_LINE_LENGTH (1024 bytes including the NUL terminator).
    DarwinBoot passes the string; this tool only rejects values it could not
    hand over later.
    """
    if len(text) > 1023:
        raise SystemExit("--kernel-args is longer than 1023 characters "
                         "(XNU's BOOT_LINE_LENGTH is 1024 with the NUL)")
    if "\0" in text:
        raise SystemExit("--kernel-args must not contain a NUL byte")
    bad = sorted({character for character in text
                  if not (0x20 <= ord(character) <= 0x7E or character == "\t")})
    if bad:
        raise SystemExit("--kernel-args contains non-printable characters: "
                         + ", ".join(repr(character) for character in bad))


def hex_value(text: str, argument: str, maximum: int) -> int:
    try:
        value = int(text, 16)
    except ValueError:
        raise SystemExit(f"{argument}: {text!r} is not a hexadecimal number")
    if not 0 < value <= maximum:
        raise SystemExit(f"{argument}: value must be between 1 and 0x{maximum:x}")
    return value


def compile_dtb(source: Path, output: Path, include_dirs: list[Path]) -> None:
    dtc = shutil.which("dtc")
    if dtc is None:
        raise SystemExit("dtc is required (install the device-tree-compiler package)")
    command = [dtc, "-I", "dts", "-O", "dtb", "-o", str(output)]
    for directory in include_dirs:
        command += ["-i", str(directory)]
    command.append(str(source))
    result = subprocess.run(command, capture_output=True, text=True)
    if result.stdout:
        sys.stderr.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"dtc failed with status {result.returncode}")
    # Warnings are not fatal, but hiding them would hide a typo in a property.
    for line in result.stderr.splitlines():
        if line.strip():
            sys.stderr.write(f"dtc: {line}\n")


def load_converter(scripts: Path):
    if not (scripts / "unixnu" / "devicetree.py").is_file():
        raise SystemExit(f"the UniXNU converter was not found under {scripts} "
                         "(set UNIXNU_SRC to the unixnu-src checkout)")
    sys.path.insert(0, str(scripts))
    from unixnu import devicetree
    return devicetree


def generate(dtb: Path, dram_size: int, chip_id: int, unique_chip_id: int,
             devicetree) -> bytes:
    """Run the shared converter and cross-check the tree it read."""
    root = devicetree.read_fdt(dtb.read_bytes())
    memory = [node for node in root.children
              if node.strings("device_type") == ["memory"] and node.enabled()]
    if len(memory) != 1 or len(devicetree.registers(memory[0])) != 1:
        raise SystemExit("the DTS must declare exactly one contiguous /memory node")
    base, size = devicetree.registers(memory[0])[0]
    if size != dram_size:
        raise SystemExit(
            f"--dram-size 0x{dram_size:x} does not match the DTS window "
            f"0x{base:x}+0x{size:x}; edit memory@40000000 to change the window")
    nodes = devicetree.generate(
        dtb,
        configuration(dram_size, chip_id, unique_chip_id),
        unique_chip_id=f"{unique_chip_id:x}",
        data=False,
        trust=False,
        addon=False,
    )
    return nodes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the ADT for the Xiaomi Mi 6 (sagit).")
    parser.add_argument("--output", type=Path, required=True,
                        help="ADT file to write")
    parser.add_argument("--dram-size", default="0xf0000000", metavar="HEX",
                        help="must match the DTS memory window (default 0xf0000000)")
    parser.add_argument("--chip-id", default="0x8015", metavar="HEX",
                        help="ApChipID, 32-bit (default 0x8015 for A11)")
    parser.add_argument("--unique-chip-id", required=True, metavar="HEX",
                        help="platform seed, 64-bit, unique per build")
    parser.add_argument("--kernel-args", metavar="STR",
                        help="validated but not written to the ADT "
                             "(use --kernel-args=-v if the value starts with '-')")
    parser.add_argument("--keep-dtb", type=Path, metavar="DIR",
                        help="also write the intermediate hardware.dtb here")
    arguments = parser.parse_args(argv)

    dram_size = hex_value(arguments.dram_size, "--dram-size", 1 << 64)
    chip_id = hex_value(arguments.chip_id, "--chip-id", 0xFFFFFFFF)
    unique_chip_id = hex_value(arguments.unique_chip_id,
                               "--unique-chip-id", 0xFFFFFFFFFFFFFFFF)
    if arguments.kernel_args is not None:
        validate_kernel_args(arguments.kernel_args)

    devicetree = load_converter(DEFAULT_UNIXNU)

    temporary = None
    if arguments.keep_dtb is not None:
        dtb = arguments.keep_dtb / "hardware.dtb"
        dtb.parent.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(
            prefix="sagit-adt-", dir=os.environ.get("TMPDIR"))
        dtb = Path(temporary.name) / "hardware.dtb"

    try:
        compile_dtb(DTS, dtb, [SOC_INCLUDE])
        try:
            blob = generate(dtb, dram_size, chip_id, unique_chip_id, devicetree)
        except devicetree.Error as error:
            raise SystemExit(f"device tree: {error}")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(blob)
    finally:
        if temporary is not None:
            temporary.cleanup()

    print(f"ADT: {arguments.output}")
    print(f"  bytes: {len(blob)}")
    print(f"  sha256: {hashlib.sha256(blob).hexdigest()}")
    print(f"  dram: 0x40000000 + 0x{dram_size:x}")
    print(f"  chip-id: 0x{chip_id:08x}")
    print(f"  unique-chip-id: 0x{unique_chip_id:016x}")
    if arguments.keep_dtb is not None:
        print(f"  hardware dtb: {dtb}")
    print("  random-seed is fresh, so this SHA-256 changes on every run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
