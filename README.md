# unixnu-sagit

Boot chain for running XNU (the iOS/macOS kernel) on the Xiaomi Mi 6
(`sagit`, Qualcomm MSM8998). This repository is the device-specific side:

```
  XBL  ->  ABL  ->  EDK2 (this repo's firmware pieces)  ->  UniXNU  ->  DarwinBoot  ->  XNU
```

It does **not** contain the graphics stack. The GPU runtime for Adreno a5xx
(Metal -> NIR -> PM4/Gallium) lives in a separate project, **Adrenoline**:
<https://github.com/ilertnost/Adrenoline>. The payload menu entry is still
called *Adrenoline* because that is the system being booted.

## Clean-room policy

This project is **not a fork** of <https://github.com/MC-XiaoXiao/ios_sagit>
and shares no code with it. That repository and other ports are read only to
understand how the hardware and Apple's boot chain are wired together; every
implementation here is written from scratch against the hardware and the
upstream sources (edk2-msm8998, UniXNU, XNU, Linux DTS). No vendor blobs,
proprietary headers or copied source are committed.

If a fact comes from another port, it is written down as a fact (an address, a
sequence, a device-tree property) and the code around it is ours.

## What is here

| Path | What it is |
|---|---|
| `firmware/SagitPkg` | EDK2 package with the pieces that are ours: `SagitRamDiskDxe` publishes the bootloader-placed payload volume, `SagitReportDxe` prints boot diagnostics on screen |
| `tools/build-sagit-edk2.sh` | builds EDK2, packs firmware + device tree + payload into an Android boot image |
| `tools/apply-edk2-unixnu-overlay.sh` | idempotent overlay that adds the UniXNU/DarwinBoot modules to the pinned EDK2 tree |
| `tools/mksagit-payload.py` | builds the FAT16 payload volume UniXNU reads (`unixnu.toml`, kernel, ADT) |
| `tools/make-sagit-adt.py`, `tools/adtinfo.py` | generate and inspect the Apple Device Tree for MSM8998 |
| `tools/build-xnu-sagit.sh` | cross-builds XNU for sagit |
| `kernel/sagit` | device tree, defconfig, board description and the UniXNU source lock |
| `docs` | boot chain ADRs, EDK2/ADT/payload/XNU notes, GPIO map |

## Requirements

Everything is expected next to this checkout in the work directory:

```
/data/opencode/
  unixnu-sagit/      this repository
  edk2-msm8998/      edk2-porting/edk2-msm8998, branch "new"
  unixnu-src/        UniXNU sources (boot manager + DarwinBoot)
  toolchains/        arm-gnu-toolchain-13.2 aarch64-none-linux-gnu
  tmp/               TMPDIR for the payload builder
```

## Build

```sh
# 1. payload volume (kernel Mach-O and its ADT)
python3 tools/mksagit-payload.py \
  --kernel path/to/kernel.macho \
  --adt path/to/kernel.macho.adt \
  --output out/sagit-payload-volume.img

# 2. firmware + Android boot image
PAYLOAD=$PWD/out/sagit-payload-volume.img tools/build-sagit-edk2.sh

# result
ls -l /data/opencode/edk2-msm8998/workspace/out/boot-sagit.img
```

Flash it into the `boot` partition:

```sh
fastboot flash boot boot-sagit.img
fastboot reboot
```

## Status

- EDK2 boots on hardware; UniXNU draws its menu from `EFI/UniXNU/unixnu.toml`
  on the payload volume.
- ABL does not pass the ramdisk bounds in the device tree and does not honour
  the ramdisk address from the boot image header. `SagitRamDiskDxe` therefore
  locates the volume by scanning conventional memory for its FAT label.
- DarwinBoot loads the kernel, validates the Mach-O, builds the boot args and
  exits boot services. First XNU handoff has happened; kernel output is the
  next milestone.

## Licence

GPL-3.0-or-later, see `LICENSE`.
