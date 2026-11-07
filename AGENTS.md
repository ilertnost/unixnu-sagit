# UniXNU-sagit — Xiaomi Mi 6 (sagit), MSM8998

A UniXNU device port: XNU (iOS/iPadOS kernel) boots on the Xiaomi Mi 6
through UEFI. The repository follows the official format-2 device repository
layout (https://unixnu.com/porting/device-repository/).

## Scope

This repository carries the **boot chain** only: EDK2 firmware pieces, the
payload, ADT, device tree and the XNU build for sagit. The GPU stack for
Adreno a5xx (IOKit driver, firmware loading, Metal → NIR, PM4/Gallium
backends) lives in a separate project, **Adrenoline**
(<https://github.com/ilertnost/Adrenoline>) — see docs/ADR-0003-boot-chain.md.

## Build

The two working flows (used by the current bring-up):

```sh
# kernel (XNU for sagit, through the official UniXNU build driver)
SRC=/path/to/UniXNU TOOLCHAIN=/path/to/toolchain tools/build-xnu-sagit.sh

# firmware + Android boot image
PAYLOAD=out/sagit-payload-volume.img tools/build-sagit-edk2.sh
```

The official resolver commands from a UniXNU checkout:

```sh
./unixnu defconfig sagit_defconfig -D /path/to/unixnu-sagit
./unixnu fetch -D /path/to/unixnu-sagit
./unixnu status --json -D /path/to/unixnu-sagit
```

## Layout

| path | purpose |
|---|---|
| `board.toml` | format-2 device profile (board, components, kernel, DTS, boot image, payload) |
| `feeds.toml` | device source pins; the local MSM8998 SoC profile |
| `unixnu.lock` | resolved refer-source commits, written by `unixnu fetch` |
| `configs/sagit_defconfig` | kernel defconfig |
| `dts/sagit.dts` | board device tree (memory, reserved regions) |
| `soc/msm8998/` | local, unpublished SoC profile (soc.toml, config, dtsi) |
| `patches/` | device patches; `patches/xnu/series` is the ordered list |
| `firmware/SagitPkg/` | EDK2 package: ramdisk and boot-diagnostics drivers |
| `package/` | board package (becomes `kits/sagit.kit` in the assembled tree) |
| `tools/` | build scripts, ADT and payload tooling, licence audit |
| `docs/` | bring-up record, ADRs, subsystem notes |
| `porting.toml` | porting record: areas, statuses, evidence |
| `old/` | the legacy format-1 layout, kept for reference until the first official-path build verifies |

## Bring-up status

The boot ladder reaches checkpoint 35 (past `pmap_bootstrap()`); the current
wall is inside the `STARTUP_SUB_KPRINTF` startup batch. Read
`docs/XNU-SAGIT.md` for the checkpoint table and `porting.toml` for the
per-area record.

## Licences

GPL-3.0-or-later for this repository's own code; third-party reuse is decided
per file by the licence each file declares — see `docs/THIRD-PARTY.md`.
