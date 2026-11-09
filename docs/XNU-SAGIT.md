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

## Version: XNU 8796 for iOS 16.7.16 (20H392)

The board pins the OS profile `ios-16.7.16-20H392` (`profiles/os/`). It carries
XNU `8796.141.3` (Darwin `22.6.0`), the target triple `arm64-apple-ios16.0`,
SDK `16.0`, and the SHA-256 of the 20H392 thin kernelcache. The resolver builds:

- the upstream iOS 17 arch patches that still apply (44 of them,
  `os:patches/xnu/series-arch`),
- the SoC patches,
- our 8796 compatibility patches (`patches/0008`–`0024`),
- the CPU-cluster fix (`patches/0007`),
- the bring-up ladder (`patches/0006`), applied last.

The iOS 17 profile (`ipados-17.7.11-21H461`) stays in the driver for
comparison, but it is not the Mi 6 target. 20H392 carries XNU **8796**, and the
prelink step merges our kernel with the kexts of *that* kernelcache; a 10063
source build does not match its ABI. The 8796 build has its own issues, all
carried as device patches:

| Patch | Why 8796 needs it |
|---|---|
| `0008` | 8796 includes the Apple tunables and applies them unconditionally |
| `0009` | the Apple AMCC objects are not gated |
| `0010` | `pmap_sync_page_attributes_phys` shifts a 32-bit physical address |
| `0012` | an Apple-clang-only auto-var-init acknowledgement flag |
| `0013` | C++ `operator delete(void *)` reaches `__typeof__(*(ptr))` |
| `0014` | the newer DriverKit generator emits `IORPC.kernelContent` |
| `0015` | `uio_stackbuf_t` is a char array with 8-byte alignment |
| `0016` | 8796 has no `-MJ` compile records for the archive builders |
| `0017` | Darwin 23 libpthread calls `proc_get_jit_entitled`, absent on 8796 |
| `0019` | 8796's machine list lacks `nos_arm_asm nos_arm_pmap`, so it links no source pmap or start/cswitch/machine-routines |
| `0020` | the public 8796 tree omits `osfmk/arm64/kpc.c`; the empty backend keeps kperf linked |
| `0021` | `pmap.h` declares `pmap_pending_preemption()` out of line for hygiene builds, but its only definition ships in Apple's closed per-SoC pmap |
| `0023` | 8796's `MakeInc.def` predates the `CFLAGS_EXTRA` variable the baseline script carries `-DXNU_SOURCE_CORECRYPTO14` in |
| `0024` | Apple's 20H392 cache exports `vfs_getvfs_with_vfsops`, absent from the public 8796 tree; backported from 10063 |

`0011` is not a kernel patch: it makes the Linux driver read the target triple
and deployment version from the OS profile instead of hard-coding iOS 17.
`0018` is the same for the archive builders. `0022` is a driver patch too:
8796's MakeInc.kernel predates the `.noctf` kernel naming, so the linked
`.unstripped` (this build runs no CTF merge) is aliased to it for the seal
step and the prelink.


## Our board

`board.toml` at the repository root is a format-2 device profile: the board
description, the defconfig, the reserved-memory device tree and the OS profile
it pins. The old format-1 profile is preserved under `old/sagit/`.

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
# Installs profiles/os/ios-16.7.16-20H392 into the driver, applies 0003 and
# 0011 to the driver, and pins the 20H392 kernelcache for prelink.
cd /data/opencode/unixnu-src
./unixnu defconfig sagit_defconfig -D /data/opencode/unixnu-sagit
./unixnu build kernel
```

`defconfig` takes the config file name, not the board name. The first build
from scratch runs one pass that compiles everything and is expected to stop at
the link: the archive builders read the compile records that pass leaves
behind, so there is nothing to read on a fresh tree.

XNU 8796 predates the `-MJ $(@:o=o.json)` compile records that the archive
builders replay, so `patches/0016-sagit-xnu8796-compile-records.patch` adds
them to the assembly, C and C++ rules in `makedefs/MakeInc.rule`. Without it
the bootstrap pass leaves no records, the driver treats its expected link
failure as fatal, and the archives cannot be built.

### What has to be named explicitly

A format-2 board does not build drivers from their component entries. Two
switches decide what happens between the bootstrap pass and the final link:

- `[kernel] archives` in `board.toml` — the ordered list of builders.
- `CONFIG_XNU_BUILD_ARCHIVES=y` in the defconfig — without it the list is not
  read at all.

The order matters: the namespaced corecrypto pass consumes the plain one, and
`build-arm-driver.py` produces the platform expert, CPU provider and RAM block
that `ARM_DT_PLATFORM`, `ARMCPU` and `RAM_BLOCK` expect at link time. Names in
`archives` are scripts of the build tree plus their arguments.

### Result of the iOS 16 build

> Current build with the `0028`/`0029` fixes (2026-11-09):
> `.noctf` sha256 `dd233ff6bdb9a6bdccf4376e0f0e63287745cd6684c6724345eb30c84757e101`,
> prelinked stripped kernel sha256 `eab3f026eb7d0c462e3b103044a0e8ae9ca3b758cde9e8c0fc680dec54744aa7`.
> The block below is the original bring-up record.

```
mach.development.bcm2837.unstripped.noctf
  17 823 624 bytes
  sha256 4265f1adad11f03ac228e9849e192e8c11dc7fc115846b21d3010c6d2b3a527c
  Mach-O 64-bit arm64, NOUNDEFS|PIE, minos 16.0 (sdk 16.0)
  __TEXT at 0xfffffff007004000, 23 023 global symbols
  _corecrypto_kext_start and _mockfs_mountroot resolved
```

`NOUNDEFS` is the part that matters: the four symbols that the bootstrap pass
could not find are now supplied by the source crypto, the pthread archive and
the mockfs fixture.

### Packaging under the Mi 6 boot-image limit

The Mi 6 bootloader refuses boot images above 64 MiB, and the payload volume
carrying the kernel is the bulk of one. Two facts decide the layout:

- `emit_prelink` preserves the original cache file layout: the whole prelink
  area rides along (222 kexts, not just the 31 selected ones) and the source
  kernel's symbol table stays in `__LINKEDIT`. The prelinked kernel is
  therefore tens of megabytes even though the selected closure is 7.4 MiB —
  the dead passengers are never instantiated (the rebuilt `__PRELINK_INFO`
  lists only the selected components) but they occupy the file.
- The kernel is loaded from a FAT volume, and that volume is too large for
  the ramdisk section uncompressed. `tools/mksagit-payload.py --lzma` wraps it
  in a 72-byte `UNXLZMA1` header and an LZMA-alone stream;
  `UniXNURamDiskDxe` expands it before publishing the block device, as
  described in `docs/FIRMWARE-PAYLOAD.md`.

`tools/strip-prelinked-kernel.py` rewrites the file for the only consumers
that matter: DarwinBoot reads LC_SEGMENT_64 alone, and XNU boots from memory.
Segments are packed 4 KiB-aligned and the symbol-table commands are dropped;
`__LINKEDIT` keeps its virtual extent with a zero file size. The prelinked
file is 39 837 696 bytes (sha256
`168fac2bab402e0af5ffe41b2ac947c815d72cfadba9e1a17584d7fd28ed3f64`), the
44 MiB volume compresses to 5 023 744 bytes, and the POST HMAC is untouched
(it covers the loaded `__text`).

### Three patches to the driver

`build kernel` used to call `kernel.prelink` unconditionally, and a source-only
board has no `[kernel.prelink]`, so a successful link ended with a failed build
and a non-zero exit. `patches/0003-unixnu-prelink-when-specified.patch` makes
the prelink step conditional on the board asking for it.

The driver also hard-codes XNU 10063's `-target arm64-apple-ios17.0` and the
`17.0` deployment version. `patches/0011-unixnu-ios-target.patch` reads both
from the OS profile (`os.target`, `os.sdk`), so the 8796 build is compiled for
iOS 16 and the iOS 17 profile keeps its own triple.

The archive builders had the same iOS 17 assumption: they compile their own
sources with `arm64-apple-ios17.0` and refuse any object whose LC_BUILD_VERSION
is not `(2, 0x110000)`. `patches/0018-unixnu-archive-deployment.patch` takes the
triple and the expected deployment from `UNIXNU_DEPLOYMENT_TARGET`, which
`0011` exports from the same `os.sdk`, so libpthread, corecrypto, the platform
components and the mockfs fixture all stay on 16.0.

`tools/build-xnu-sagit.sh` applies all three idempotently.

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
(`patches/0006-sagit-boot-ladder.patch`, the last entry of the device series in
`patches/xnu/series`, so that it is applied after every shared patch).

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
| 91 | phase: `arm_set_user_tbi()` returned |
| 94 | phase: physmap low slid region mapped |
| 95 | phase: physmap window tail (DT, ramdisk, args) mapped |
| 96 | phase: physmap rest of physmem mapped |
| 97 | phase: physmap done, `ptov_table` sorted and copied |
| 92 | phase: `arm_vm_physmap_init()` returned |
| 93 | phase: `set_mmu_ttb_alternate()` returned, TTBR0 switch next |
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

**A decimal readout** is painted in two places, the top right corner at
(820, 120) and the bottom right one 90 pixels above the last row. It exists
because the bars can be *measured* right up to the moment the phone reboots:
the next boot's UniXNU menu is drawn over the same framebuffer, and its list
box happens to cover the middle of the second column — bars 27 to 30, which
is where `arm_vm_init()` hands the kernel over to the pmap. In the first
photograph after the cluster fix the left column was clearly full and the
right column disappeared under a blue menu highlight, so the stop could not be
told from a hang at checkpoint 26. The number is drawn with a 3x5 digit font
scaled to 8 pixels, cleared before each paint, and the bottom copy is
positioned from the framebuffer height reported in `boot_args`, not from a
hardcoded 1920.

Five bars is where the third photograph stopped, and the reason was in
`pexpert/pexpert/arm64/board_config.h`, not in the kernel: the BCM2837 board
config that this build rides on declares `MAX_CPU_CLUSTERS 1`, so
`MAX_CPU_CLUSTER_PHY_ID` is 0, and `ml_parse_cpu_topology()` computes
`phys_cluster_id = (cluster_type == CLUSTER_TYPE_P)` when `HAS_CLUSTER` is not
set. Our DT describes the four gold Kryo cores with `cluster-type = "P"`, so the
fifth CPU node trips `assert(phys_cluster_id <= MAX_CPU_CLUSTER_PHY_ID)`; an
assert without a console is a silent freeze. MSM8998 really has two clusters, so
`patches/0007-sagit-cpu-clusters.patch` sets `MAX_CPU_CLUSTERS 2` for the
BCM2837 config and the build installs it next to the ladder as
`patches/xnu/zzzz-sagit-cpu-clusters.patch`. It has to sit after the shared
`native-smp-secondary.patch`, which edits the same three lines of that header.

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
`0x44C04000..0x54C04000`), so the painter has three views of it. With the MMU
off it is the physical address itself. After the MMU comes on, `start.s` adds
L2 block entries for it to the V=P bootstrap map, which is why the assembler
checkpoints and everything up to checkpoint 29 can keep using the physical
address.

Once `arm_vm_init()` replaces TTBR0 with the kernel pmap, that map is gone, and
the physmap does **not** cover the framebuffer. DarwinBoot reports
`MemorySize = WindowSize`, the one contiguous conventional-memory run it
allocated for the kernel, and XNU builds `physmap_base .. physmap_base + memSize`
from exactly that: 256 MB here, ending at `0x54C04000`. `phystokv(0x9d400000)`
is then outside `physmap_end` and faults. `ml_io_map_unmappable()` is the right
answer for a framebuffer outside reported memory, but it needs `pmap_bootstrap()`
and the kernel map, so it cannot be called at the switch either: the board
reset there, which is what stopped the very first version of the ladder at 29.

The switch itself is bridged with the invalid TTBR0 root. `arm_vm_init()`
already builds `invalid_tte` for the window between `set_mmu_ttb()` and the
real user map; the patch adds 2 MB block entries covering every block of the
framebuffer at its physical address (`c11b503`). The ladder therefore keeps
painting at `0x9d400000` across the switch, with no alias and no second
mapping.

`ml_io_map_unmappable()` enters only after `initialize_screen()` has run.
`sagit_pmap_ready()` flips `sagit_pmap_live` right after that call, and the
next checkpoint selects a view: `phystokv()` when the physmap covers the
framebuffer, otherwise one lazy `ml_io_map_unmappable(..., VM_WIMG_WCOMB)`.
Here the physmap is exactly the 256 MB loader window
(`0x44C04000..0x54C04000`) and does not cover it, so the lazy mapping is the
path taken. Calling it earlier is a silent crash, which is why the physical
address remains in use until `sagit_pmap_ready()`.

The ladder's own stores are cleaned to DRAM, because the display controller
scans memory directly.

Since the invalid root keeps the framebuffer reachable, the ladder has no
blind window. A photo that stops at 93 says the kernel died in the TTBR0 write
or the TLB flush itself, one that stops at 30 says it died before
`pmap_bootstrap()`, and the bars after 30 are the checkpoints `arm_init()` and
`kernel_bootstrap()` go through. `sagit_step()` marks sub-steps 22..25 while it
paints; `sagit_mark_phase()` paints 91..97 for the physmap stages without
advancing the bar index, and the counter clamps at 99.

A sub-ladder of 18x18 red squares sits at y=208, x=64+26*step, right under the
stage row. `sagit_step()` uses 22..25 while painting a bar. The startup hook
loop clears the row and starts again at 9, so the rightmost square is the last
step that ran.

The same row is the sub-ladder of the startup hook loop. Once the invalid root
is in place the ladder can paint after the switch, so a stop at a checkpoint only says
which call died, not why: `kernel_startup_initialize_upto()` runs a whole batch
of hooks behind a single checkpoint. `sagit_dbg()` is therefore exported, and
`sagit_dbg_clear()` wipes the row at the top of every iteration of that loop so
only the marks of the last iteration survive. The loop marks are 9 before
`kernel_startup_log(cur->subsystem)`, 10 before `cur->func(cur->arg)`, 11 after
the hook returns, 12 after the loop and 13 once `kernel_startup_log(upto)` has
run. Since `kernel_startup_bootstrap()` consumes everything up to `LOCKS` and
`arm_init()` asks for `KPRINTF`, the batch behind checkpoint 36 is exactly
`_PE_init_kprintf`: 9 alone puts the fault in the subsystem log, 10 in the hook
and 11 in the phase log or the bookkeeping after the hook.

Which hook a checkpoint covers is not obvious from the source, because
`kernel_startup_bootstrap()` sorts `__BOOTDATA,__init_entry_set` at runtime by
(subsystem, rank) and the table in the file is unordered. `tools/startup-entries.py`
parses that section out of the kernel that was actually flashed, applies the same
sort and prints the entries with their ordinals, so `--subsystem 4` answers the
question directly; run it on the unstripped Mach-O, the one the payload carries.

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

The format-2 resolver applies the selected series in order on a fresh clone
(`patches/xnu/series`: arch, SoC, OS, then device) whenever the source revision
or any patch changes. The ladder is the last entry of the device series and
`0007-sagit-cpu-clusters.patch` precedes it; the series order is the dependency
order, so no reverse-check dance is involved.

A real terminal over USB is ADR-0006.

## Open questions

- **RAM size.** The owner's unit has 6 GB. The device tree declares the
  3.75 GiB window that the firmware itself advertises, which is safe but leaves
  the rest unused; the real map has to come from the UEFI memory map at
  handoff.
- **Storage.** The Mi 6 uses eMMC (SDHCI), not the UFS controller that the
  other MSM8998 ports drive. Irrelevant for a ramdisk root, blocking for
  anything persistent.
