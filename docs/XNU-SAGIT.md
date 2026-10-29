# XNU for sagit

The kernel is the gate for everything else: without an XNU that boots on
MSM8998 there is nothing to mount a root on.

## What already exists

XNU cannot be built for a non-Apple SoC as it ships. The work of making it
build and boot on Qualcomm hardware is done in the open by
[UniXNU](https://github.com/UniXNU/UniXNU), under
BSD-2-Clause-Patent — a licence compatible with this project. Two of its
device ports are on **MSM8998**, our own SoC:

| port | device | state |
|---|---|---|
| `chiron` (`to-mid/ios-party`) | Xiaomi Mi MIX 2 | minimal 4 KiB XNU, own PID 1, heartbeat on hardware |
| `cheeseburger` (`DinnerABC/UniXNU-cheeseburger`) | OnePlus 5 | main branch tracks iOS 16.7.16 (20H392); XNU boots, root mounted, no GPU |

Neither is sagit, and neither does what this project is for — but they prove
the kernel side is reachable on this SoC, including on our exact OS build.

We do not copy their device code. We use their build driver and toolchain (both
BSD-2-Clause-Patent, kept under their own headers) and write our own board
description, device tree and later our own drivers.

## Toolchain

The kernel is built on Linux with a patched clang: XNU's `kalloc.h` calls
`__builtin_xnu_type_*`, which only exists in Apple's clang, so upstream LLVM is
built from source with that patch, plus cctools-port `ld64` (lld rejects
`-kernel`), `mig`, `iig`, and an `lzfse` for kernelcaches.

```sh
sudo env TMPDIR=/data/opencode/tmp \
  ./scripts/linux/setup-toolchain.py /data/opencode/unixnu-toolchain -j 16
```

Roughly an hour on sixteen cores, and the LLVM build tree alone is several GB,
so both the prefix and `TMPDIR` belong on `/data`. What it leaves behind:

```
llvm/bin/clang        clang 24.0.0git, 58c46bae, with __builtin_xnu_type_*
CommandLineTools/usr  ld64-956.6, ar/otool/lipo/nm/libtool, migcom, xcrun shims
bin/lzfse             kernelcache codec
```

The shims default to `/opt/unixnu-linux`; the build driver reads
`UNIXNU_TOOLCHAIN` instead, so export it rather than relocating the prefix.

## Version: iOS 17 by default, iOS 16 is the target

The driver compiles XNU `10063.141.1` (the iOS 17 base) and passes
`-target arm64-apple-ios17.0`; both are fixed in `scripts/unixnu/kernel.py`.
That is fine for a first compile and link, and it is what chiron does.

It is **not** fine for the real bring-up. 20H392 carries XNU **8796**, and the
prelink step merges our kernel with the kexts of *that* kernelcache; a 10063
source build does not match its ABI. Pinning `xnu-8796.101.5` and
`arm64-apple-ios16.0` is therefore a prerequisite for anything beyond stage 1.


## Our board

`xnu/sagit/` is a format-1 device profile: `board.toml`, a defconfig, a device
tree for the board and one for the SoC.

The reserved-memory map in `dts/sagit.dts` is not invented — it was extracted
from the Mi 6's own Android device tree, which is the authoritative source for
which ranges the kernel must keep its hands off:

```
removed_regions   0x85800000 + 0x03700000
pil_adsp          0x8b200000 + 0x01e00000
spss              0x8ab00000 + 0x00700000
modem             0x8d000000 + 0x07000000
pil_video         0x94000000 + 0x00500000
pil_mba           0x94500000 + 0x00200000
pil_slpi          0x94700000 + 0x00f00000
pil_ipa_gpu       0x95600000 + 0x00100000
splash            0x9d600000 + 0x02400000
```

The first stage declares only what a RAM-rooted bring-up needs: the ARM device
tree platform expert, the architectural CPU and interrupt provider, the
architectural timer, and a source crypto/pthread so the kernel links without
Apple's prelinked components. No storage, no display, no GPU.

The hardware DTB supplies the CPU/GIC/timer description; XNU is actually handed
an Apple device tree. `docs/ADT-SAGIT.md` documents that format, the generated
`kernel.macho.adt`, its iPhone10,1 identity, and the list of what is still
unverified.

## Build

```sh
tools/build-xnu-sagit.sh
```

which is, in full:

```sh
export TMPDIR=/data/opencode/tmp UNIXNU_TOOLCHAIN=/data/opencode/unixnu-toolchain
cd /data/opencode/unixnu-src
./unixnu defconfig sagit_defconfig -D ./kernel/sagit
./unixnu fetch
./unixnu build kernel
```

`defconfig` takes the config file name, not the board name. The first build
from scratch runs one pass that compiles everything and is expected to stop at
the link: the archive builders read the compile records that pass leaves
behind, so there is nothing to read on a fresh tree.

### What has to be named explicitly

A format-1 profile does not build drivers from their component entries. Two
switches decide what happens between the bootstrap pass and the final link:

- `[kernel] archives` in `board.toml` — the ordered list of builders.
- `CONFIG_XNU_BUILD_ARCHIVES=y` in the defconfig — without it the list is not
  read at all.

The order matters: the namespaced corecrypto pass consumes the plain one, and
`build-arm-driver.py` produces the platform expert, CPU provider and RAM block
that `ARM_DT_PLATFORM`, `ARMCPU` and `RAM_BLOCK` expect at link time. Names in
`archives` are scripts of the build tree plus their arguments.

### Result of the first complete build

```
mach.development.bcm2837.unstripped.noctf
  18 388 888 bytes
  sha256 73bef02088a2ec0335f6b85a36aafa0d640c812576094fa6f495e641b24bd8b7
  Mach-O 64-bit arm64, NOUNDEFS|PIE, minos 17.0
  __TEXT at 0xfffffff007004000, 48 512 exported symbols
  _corecrypto_kext_start and _mockfs_mountroot resolved
```

`NOUNDEFS` is the part that matters: the four symbols that the bootstrap pass
could not find are now supplied by the source crypto, the pthread archive and
the mockfs fixture.

### One patch to the driver

`build kernel` used to call `kernel.prelink` unconditionally, and a source-only
board has no `[kernel.prelink]`, so a successful link ended with a failed build
and a non-zero exit. `patches/0003-unixnu-prelink-when-specified.patch` makes
the prelink step conditional on the board asking for it. `tools/build-xnu-sagit.sh`
applies it idempotently.

## Kernel output

A `DEVELOPMENT` XNU build is silent unless it is told otherwise, and this board
has no UART for the fallback. `pexpert/arm/pe_kprintf.c` keeps two switches:

| Switch | Cleared by |
|---|---|
| `disable_serial_output` | `debug=` with `DB_KPRT` (`0x8`), e.g. `debug=0x14e` |
| `disable_kprintf_output` | `-enable_kprintf_spam` in a `DEVELOPMENT` build |

`kprintf` prints only when both are false. `-v` clears neither, which is why a
kernel that boots correctly still leaves the screen black.

With both set, `serial_init()` finds no `PI3_UART` / `VMAPPLE_UART` /
`DOCKCHANNEL_UART` / `APPLE_UART` node on MSM8998, returns 0, and `PE_kputc`
becomes `console_write_unbuffered`. `cons_ops_index` starts at `VC_CONS_OPS`, so
the text lands on the video console:

```sh
python3 tools/mksagit-payload.py \
  --kernel out/kernel.macho --adt out/adt/kernel.macho.adt \
  --output out/sagit-payload-volume.img \
  --args="-v -enable_kprintf_spam debug=0x14e"
```

`DarwinBoot` fills `boot_args.Video` from GOP with bit 0 of `v_baseAddr` set,
XNU's tag for "this is a physical address, map it yourself", and `v_display = 0`
for the text console. `PE_create_console` then maps the framebuffer with
`ml_io_map_unmappable`, and the screen behaves like a serial terminal: it is the
kernel log.

### Bring-up ladder

Text only appears in `kernel_bootstrap()`, after `initialize_screen(NULL,
kPEAcquireScreen)`; `PE_create_console` merely prepares the console. A fault
anywhere between the loader's branch and that point is therefore silent. The
kernel paints a checkpoint square into the handoff framebuffer at each stage
instead (`patches/0006-sagit-boot-ladder.patch`, installed as
`patches/xnu/zzzz-sagit-boot-ladder.patch` in the UniXNU tree so that it is
applied after every shared patch):

| Stage | Square | Where |
|---|---|---|
| 0 | white | `start_first_cpu`, stacks ready, MMU off |
| 1 | blue | after both bootstrap maps are built, MMU off |
| 2 | green | MMU on, about to enter `arm_init()` |
| 3 | cyan | first statement of `arm_init()` |
| 4 | magenta | `arm_vm_init()` returned |
| 5 | yellow | `PE_create_console()` returned |
| 6 | red | end of `arm_init()`, before `machine_startup()` |
| 7 | orange | screen acquired |

The squares are 48x48 at y=140, x=8+stage*56, so the count of squares is the
number of stages reached: a missing square is the first checkpoint that did not
run. The painter uses `boot_args.Video[0]` as a physical address (valid with the
MMU off and under the V=P bootstrap map) and translates it through
`gVirtBase`/`gPhysBase` once `arm_vm_init()` has installed the linear window,
cleaning the cache lines because the display controller scans DRAM directly.

A real terminal over USB is ADR-0006.

## Open questions

- **RAM size.** The owner's unit has 6 GB. The device tree declares the
  3.75 GiB window that the firmware itself advertises, which is safe but leaves
  the rest unused; the real map has to come from the UEFI memory map at
  handoff.
- **Storage.** The Mi 6 uses eMMC (SDHCI), not the UFS controller that the
  other MSM8998 ports drive. Irrelevant for a ramdisk root, blocking for
  anything persistent.
