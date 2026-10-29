# EDK2 for Mi 6 (sagit)

The boot chain from ADR-0003, built from source and reproducible.

## The platform already exists

`edk2-porting/edk2-msm8998` (branch `new`) ships Mi 6 support natively, at
`Platforms/Msm8998Pkg/Device/xiaomi/sagit`. Nothing in that layer had to be
written. The platform code carries BSD headers.

What it already contains, and matters for us:

| piece | why it matters |
|---|---|
| `SimpleFbDxe` | framebuffer console before any OS exists |
| `FrameBufferSerialPortLib` | debug output on the panel, no UART needed |
| `KeypadDxe`, `GenericKeypadDeviceDxe` | power and volume keys as UEFI input |
| `SynapticsRmi4Dxe` | touchscreen as a UEFI pointer |
| `Shell.inf` | a real UEFI shell on the phone |
| `AndroidFastbootApp` | Fastboot from inside the firmware |
| `LinuxSimpleMassStorage` | UFS exposed over USB |

`GPLDrivers/Library/simple-init` (BigfootACA/simple-init, GPL-2.0-only) is
pulled in through `INF src/main/SimpleInitMain.inf`. It exists to chain-load
Linux and is not part of our code; it stays under its own licence.

## Toolchain

The pinned EDK2 is from January 2023 and has no AARCH64 `CLANGDWARF`, so the
supported path is `GCC5` with a cross compiler:

```
Arm GNU Toolchain 13.2.Rel1, aarch64-none-linux-gnu
```

## Local changes

Two patches, both build fixes for a modern GCC, neither touching behaviour:

- `patches/0001-edk2-msm8998-gcc13.patch` — `-Wno-dangling-pointer` in
  `Msm8998Pkg/Msm8998.dsc`. GCC 13 enables that diagnostic and the ARM platform
  library deliberately stores the address of a local memory map in a static.
- `patches/0002-edk2-basetools-gcc13.patch` — `-std=gnu17 -Wno-error` and
  `-Wno-incompatible-pointer-types` in the BaseTools makefiles. The 2023
  BaseTools are K&R-era C in `Pccts`, and GCC 14+ makes those constructs hard
  errors.

## Local changes

The pinned core is January 2023. Two things have to be added before the UniXNU
boot manager and the DarwinBoot loader will build against it, and neither can be
fixed from a DSC:

- `MdePkg/Include/Guid/Fdt.h`, `MdePkg/Include/Library/FdtLib.h` and
  `MdePkg/Library/BaseFdtLib` (with `libfdt`) — the 2023 FDT API
  (`FdtCheckHeader`, `FdtSetProp`, `FdtOverlayApply`, `Fdt32ToCpu`, …). The
  tree only has `EmbeddedPkg`'s older lowercase `fdt_*` library.
- `gFdtTableGuid`, which those headers reference and `MdePkg.dec` does not
  declare.

`tools/apply-edk2-unixnu-overlay.sh` copies exactly those items from the Mu
core checkout that the UniXNU fetch already puts on disk
(`out/feeds/mu_basecore-*`, `release/202608`, BSD-2-Clause-Patent), appends the
two `MdePkg.dec` entries, and wires the device DSC and FDF. It is idempotent and
touches nothing else in the pinned core; `tools/build-sagit-edk2.sh` runs it
before every build.

`ArmPkg/Include/AsmMacroLib.h` needs a note. Upstream Mu keeps it at
`MdePkg/Include/AArch64/`, and it uses `_ASM_TYPE` and `AARCH64_BTI`, which a
newer `ProcessorBind.h` defines and the pinned one does not. The overlay
installs the file under `ArmPkg/Include` (where DarwinBoot's `Entry.S` looks for
it) and appends a marked fallback block guarded by `#ifndef`, so it is a no-op on
any newer core.

The vendored packages themselves are never edited. Where a module needs the new
FDT library and the rest of the platform needs the old one, the DSC gives a
per-module `<LibraryClasses>` override instead of changing the global mapping.

## Build

```sh
ROOT=/data/opencode/edk2-msm8998 \
TOOLCHAIN=/data/opencode/toolchains/arm-gnu-toolchain-13.2.Rel1-x86_64-aarch64-none-linux-gnu \
  tools/build-sagit-edk2.sh
```

Result:

```
workspace/out/boot-sagit.img   11 067 392 bytes
```

Layout, verified from the header: Android boot image v1, page size 2048,
`kernel_addr 0x10000000`, kernel = gzip of `MSM8998PKG_UEFI.fd` followed by
`Android-sagit.dtb`. An 11 MB image for a boot partition that is far larger.

## Two traps worth remembering

**Stale `Conf/` files.** `build_rule.txt` and `tools_def.txt` must come from the
same tree. Mixing them produces makefiles that call `GenFw` with a variable that
the other revision never defines, and the error surfaces as
`GenFw: ERROR 1001: Missing option` in a module that has nothing to do with it.
The script refreshes all three templates on every run.

**`SimpleInit` rootfs generation.** `SimpleInitRootFS.inf` lists
`../build/rootfs.c`, which only exists after `scripts/gen-rootfs-source.sh`
runs. Without it the build fails at metadata time with a path that looks like a
missing checkout.

## Flashing

```sh
fastboot -S 0 flash boot workspace/out/boot-sagit.img
fastboot reboot
```

`-S 0` disables sparse splitting. The restore point is the stock `boot.img` from
an official Mi 6 fastboot ROM; it has to be on hand before the first write.

## Where this leaves the chain

The firmware boots, the platform is upstream, and the image is reproducible.
Inside the main firmware volume now sit five modules: four from UniXNU
(BSD-2-Clause-Patent, read-only inputs) and one of ours.

| module | licence | role |
|---|---|---|
| `UniXNUDxe` | BSD-2-Clause-Patent | boot manager: registers the UniXNU application as the first boot option |
| `UniXNU` | BSD-2-Clause-Patent | the menu application and the configuration it reads |
| `DarwinBoot` | BSD-2-Clause-Patent | produces `gUniXNULoaderProtocolGuid` and loads an XNU Mach-O |
| `SagitRamDiskDxe` | GPL-3.0-or-later | publishes the bootloader-placed payload volume as a block device |
| `SagitPkg` (DEC) | GPL-3.0-or-later | token space and PCDs for the module above |

`UniXNURamDiskDxe` is deliberately **not** used. It reads the initrd bounds from
a Mu-Silicium SEC handoff HOB (`DAEE82E6-8090-4E72-…`) that this EDK2 port never
creates, so on this firmware it does nothing but print an error. Ours reads the
same bounds from the device tree instead: `edk2-msm8998` stores the pointer ABL
passes in `x0` at `PcdFdtStoreAddress`, ABL writes `linux,initrd-start` and
`linux,initrd-end` into `/chosen` when the boot image carries a ramdisk, and
that is all the driver needs. See [FIRMWARE-PAYLOAD.md](FIRMWARE-PAYLOAD.md).

Sizes: FD unchanged at 6 291 456 B, the compressed volume at 5 648 280 of
6 258 688 (610 408 B free). Without a payload the boot image is 11 104 256 B,
`ANDROID!` v1, 2048-byte page, kernel at `0x10000000`; with the payload it is
36 270 080 B and the ramdisk is at `0xE0000000`.
