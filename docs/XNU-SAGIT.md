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
anywhere between the loader's branch and that point is therefore silent, so the
kernel reports progress straight into the handoff framebuffer
(`patches/0006-sagit-boot-ladder.patch`, installed as
`patches/xnu/zzzz-sagit-boot-ladder.patch` in the UniXNU tree so that it is
applied after every shared patch).

Reading a photograph of a dim screen turned out to be the hard part: adjacent
coloured squares blur into each other and magenta, purple and teal are the same
colour on a phone camera. So the indicator is *position* encoded, not colour
encoded. It has two parts.

**Row of six squares**, 64x64 at y=140, x=64+stage*80, painted from assembler
(stage 5 from C, through `sagit_mark_args`):

| Stage | Colour | Where |
|---|---|---|
| 0 | white | `start_first_cpu`, stacks ready, MMU off |
| 1 | yellow | both bootstrap maps built, MMU off |
| 2 | cyan | translation registers and vectors set, MMU still off |
| 3 | green | MMU on, execution continued through the new tables |
| 4 | blue | end of `start.s`, about to call into C |
| 5 | orange | C entry, framebuffer recorded |

Six squares mean the bootstrap page tables, the MMU and the framebuffer mapping
survived the handover; anything less is a boot-chain regression and the count
tells which stage died.

**Column of bars**, 320x36, first bar at (64, 320), one bar per checkpoint, 45
pixels per step, with nine black rows between bars so the column reads as a
stack of blocks. Twenty-one rows per column, second column at x=432, so bar `n`
sits at `y = 320 + (n mod 21) * 45` in column `n / 21` and ends 36 pixels below.
Measure the bottom edge and divide by 45; no counting required. Bars are green
until `arm_vm_init()` replaces TTBR0 and cyan afterwards, which also says whether
the post-handover framebuffer path works. If a checkpoint is ever added past the
forty-second, the last bar turns red instead of silently dropping bars, which
would read as a hang.

| Bar | Checkpoint |
|---|---|
| 0 | `arm_init()` entered |
| 1 | image rebased and signed, constant boot args in place |
| 2 | `PE_init_platform()` |
| 3 | timer registers, WFE timeout, misc Apple registers |
| 4 | `/chosen` lookup, page shift decided |
| 5 | `ml_parse_cpu_topology()` entered, boot args parsed |
| 6 | `/cpus` node found, iterator started |
| 7 | first cpu node parsed |
| 8 | all cpu nodes parsed |
| 9 | cluster offsets built, boot cpu found |
| 10 | `TPIDR_EL0` set, topology done |
| 11 | `ml_get_boot_cpu_number()` |
| 12 | boot cpu data and `CpuDataEntries` |
| 13 | bootstrap thread created and installed |
| 14 | `cpu_bootstrap()` |
| 15 | `rtclock_early_init()` |
| 16 | `kernel_startup_bootstrap()` |
| 17 | `timer_call_init()` |
| 18 | `cpu_init()` |
| 19 | `processor_bootstrap()` |
| 20 | about to call `arm_vm_init()` (first bar of the right column) |
| 21 | `arm_vm_init()` entered |
| 22 | memory geometry, physmap range decided |
| 23 | `cpu_tte` and `invalid_tte` allocated |
| 24 | kernel window mapped into the new tables |
| 25 | Mach-O segments resolved |
| 26 | `arm_vm_prot_init()` |
| 27 | low globals mapped |
| 28 | dynamic range mapped |
| 29 | cpu counters, before the physmap |
| 30 | TTBR0 replaced, KVA live (first cyan bar) |
| 31 | `pmap_bootstrap()` |
| 32 | `arm_vm_init()` done |
| 33 | `arm_init()` after `arm_vm_init()` |
| 34 | `PE_consistent_debug_inherit()` |
| 35 | `serial_init()` |
| 36 | `kernel_startup_initialize_upto(STARTUP_SUB_KPRINTF)` |
| 37 | first `kprintf` |
| 38 | `PE_create_console()` |
| 39 | about to call `machine_startup()` |
| 40 | screen acquired, kernel log takes over |

Three green squares at y=240, x=64/144/224, are a pre-flight check of the
bootstrap tables, taken just before the MMU comes on: `AT S1E1R` translates an
address with TTBR0/TCR even while `SCTLR_EL1.M` is zero, and `PAR_EL1.F` then
says whether the first access after the switch would fault. Left to right the
addresses are the code, the framebuffer and `boot_args`; red instead of green
identifies the one that is not mapped. This exists because a red framebuffer
probe is what stopped the ladder at stage 2 in the first place.

The first version of this painter took its x offsets as bytes while thinking of
them as pixels, so every square was drawn at a quarter of its intended x and
sixteen pixels wide; a bar that should have been 64 pixels wide was 16, and the
squares looked like a barcode in the corner of the screen. The offsets are
multiplied by four now.

The loader's log gives the numbers the painter has to live with:

```
ARM64 va=0xFFFFFFF007004000 pa=0x44C04000 args=0x45E64000 owned=0x10000000
video base=0x9d400000 display=1 row=4320 width=1080 height=1920 depth=32
```

The framebuffer is outside the 256 MB window (the window is
`0x44C04000..0x54C04000`), so the painter has three paths. With the MMU off it
is the physical address itself. After the MMU comes on, `start.s` adds L2 block
entries for it to the V=P bootstrap map, which is why the assembler checkpoints
can keep using the physical address. Once `arm_vm_init()` replaces TTBR0 with
the kernel pmap, that map is gone, but the kernel physmap covers all of RAM --
the framebuffer included -- so `phystokv()` of the physical address works and is
what the C half uses (`sagit_kva_live` marks the switch, not `gVirtBase`, which
is set long before the tables actually change). `ml_io_map_unmappable()` is only
the fallback for a framebuffer outside reported memory. Bars are cleaned to DRAM
because the display controller scans memory directly.

The framebuffer and the window are in *different* 1 GB regions (L1 index 2 vs
1 on 4 KB granule with `T0SZ_BOOT` 26), so the framebuffer's L1 slot is empty
when the bootstrap map is built. The code therefore cannot simply fill L2
entries: it takes the last page of the reserved bootstrap pool
(`BOOTSTRAP_TABLE_BYTES - PGBYTES`), zeroes it, points the framebuffer's L1
entry at it and only then writes the block entries. The allocator starts at the
fourth page and needs one L2 page per gigabyte of window, so that page stays
free for the 256 MB window. Skipping this step is invisible before the MMU and
fatal right after it: the store of stage 3 takes a translation fault, the
exception vector is at a valid KVA, and the board simply stops with the cyan
square on screen.

The patch has to be the very last one applied, and it must start from a tree
where it is *not* applied: the format-1 build decides whether a patch still
needs applying by reverse-checking it, and the ladder's insertion points sit in
the context that `native-4k-bootstrap-capacity.patch` and
`zzz-boot-progress.patch` use for their own reverse-check. `tools/build-xnu-sagit.sh`
therefore reverses the ladder before each build and exports `LC_ALL=C`, because
in a UTF-8 locale the glob sorts `zzyb-` before `zzy-` and breaks the dependency
order of the observation hooks.

A real terminal over USB is ADR-0006.

## Open questions

- **RAM size.** The owner's unit has 6 GB. The device tree declares the
  3.75 GiB window that the firmware itself advertises, which is safe but leaves
  the rest unused; the real map has to come from the UEFI memory map at
  handoff.
- **Storage.** The Mi 6 uses eMMC (SDHCI), not the UFS controller that the
  other MSM8998 ports drive. Irrelevant for a ramdisk root, blocking for
  anything persistent.
