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

The line between the two is fixed: everything that talks to the GPU — the
IOKit driver, firmware loading, ring submission, shaders — is in Adrenoline.
This repository keeps the boot chain and, at most, basic software rendering
into the framebuffer: the bring-up ladder, console text, a blit or a simple
rasteriser for the panel before the driver exists. Bring-up diagnostics must
keep working while the GPU does not.

## Provenance and licences

This project is **not a fork** of <https://github.com/MC-XiaoXiao/ios_sagit>,
and what may be reused from it or from any other port is decided per file, by
the licence that file declares, not by the repository as a whole. Every
implementation written here is ours; the review that decides what may be
carried over is `docs/THIRD-PARTY.md`.

| category | what we do |
|---|---|
| permissive (`BSD-2-Clause`, `BSD-2-Clause-Patent`, `BSD-3-Clause`, `MIT`, `ISC`, `Apache-2.0`) | may be vendored into `third_party/` with its copyright header and SPDX line intact; the upstream commit and the sha256 of every file are recorded |
| `GPL-2.0-or-later`, `LGPL-2.1-or-later`, `MPL-2.0` | may be combined with this project, subject to their terms |
| `GPL-2.0-only`, `APSL-2.0` | never copied, never paraphrased. Only the facts are used |
| no licence line | not vendored until checked by hand |

Rewriting a file "so that none of the original code remains" is not an
exception to the third row: a paraphrase that keeps the structure and the
sequences is still a derivative work. `tools/licence-audit.py` applies the
rule mechanically, and `docs/THIRD-PARTY.md` records the audit of every tree
we read.

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
| `tools/startup-entries.py` | lists the kernel startup hook table in execution order, for reading the bring-up ladder |
| `tools/licence-audit.py` | per-file SPDX audit of the tree and the pinned third-party sources |
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
