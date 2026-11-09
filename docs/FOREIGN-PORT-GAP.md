<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Foreign-port gap analysis: cheeseburger (OnePlus 5) vs sagit (Mi 6)

Status: 2026-11-09. Target: iOS 16.7.16 (20H392), XNU 8796, `arm64-apple-ios16.0`.
Scope: boot chain, XNU, firmware, storage/root, display, input, power, security,
diagnostics, userland, GPU.

Evidence tags used below:

| tag | meaning |
|---|---|
| **H** | observed on this device (sagit) or on the reference's own device |
| **B** | built and inspected offline, not observed at runtime |
| **R** | demonstrated by the reference port on its own hardware |
| **P** | planned; no implementation here yet |

The reference is `DinnerABC/UniXNU-cheeseburger` (OnePlus 5, MSM8998),
checkout `/data/opencode/ref-cheeseburger`. It is the closest MSM8998 port and
the furthest along. It is **not** a sagit port and it is **not** a proven
iOS 16 userland: its user-confirmed visible UI is iPadOS 17.7.11 (21H461)
(`P21-UP1`), while its iOS 16.7.16 (20H392) branch is a linked and transmitted
candidate whose runtime was never confirmed (`P23_STAGE_C_STATUS.md`).

---

## 0. Bottom line

### 0.1 What sagit does today

```
XBL -> ABL -> Mu-Silicium UEFI -> UniXNU menu -> DarwinBoot -> XNU 8796
                                                              |
                                                              +-- video console
                                                              +-- panic screen
                                                              +-- IOKit init
                                                              +-- mockfs source root
```

- The firmware chain works on hardware: the UniXNU menu is drawn, DarwinBoot
  loads the Mach-O and hands off to XNU. **H**
- The iOS 16 / XNU 8796 kernel builds, links with `NOUNDEFS`, has
  `minos 16.0` / `sdk 16.0`, and reaches IOKit. **H/B**
- The console works, and the panic screen is now readable and single-spaced.
  The first real panic was decoded and fixed (see §11). **H/B**
- The kernel has an in-memory `mockfs` root and its own PID 1 fixture. There is
  no APFS, no Apple userland, no SpringBoard, no storage, no display backend,
  no touch, no kernel button driver, no PMGR/power backend and no KeyStore. **B**

### 0.2 What the reference proves on MSM8998

The reference is a full bring-up, not a kernel-only demo:

| capability | reference state | evidence |
|---|---|---|
| XNU on MSM8998 | boots to IOKit/BSD | **R** |
| CPU | 8 CPUs configured, 1 GiB; R6 log says online=8 | **R/B** |
| storage | warm-link UFS, read-only, 4 KiB LUN, GPT/userdata windows | **R** |
| root | authenticated owner root, OS/SystemOS APFS read-only | **R** |
| Data | writable volatile APFS RAM Data | **R** |
| display | CPU copy to UEFI GOP through `IOMobileFramebufferLegacy`; UI visible on iPadOS 17.7.11 | **R** |
| touch | QUP5 / Synaptics RMI4 F12 producer, physical validation pending | **R/B** |
| buttons | SPMI PON/GPIO poll, HID usages, Home chord | **R** |
| security | AMFI / Image4 / CoreTrust / TrustCache, sandbox, AppleMatch | **R** |
| watchdog | `watchdogd` isolated, persistent log | **R** |
| iOS 16.7.16 | XNU 8796 + 20H392 components linked and sent; runtime unconfirmed | **B** |
| GPU | none | **R** |
| KeyStore / SEP | AKS state fixture only; no SEP, no real keybag | **R** |
| Wi-Fi / BT / audio / camera | none | **R** |
| USB log | persistent UFS log window, no USB gadget | **R** |

The reference is therefore the correct checklist, but its iOS 16 result is not
a known-good end-to-end recipe. Our target (iOS 16.7.16) is on the reference's
experimental branch, not its proven baseline. We cannot assume its 17.7.11
userland recipe carries over unchanged.

### 0.3 The three real blockers

1. **Storage and root.** Without a UFS block device there is no APFS, no
   `/System/Library`, no launchd, no SpringBoard. This is the largest gap and
   the prerequisite for every userland capability.
2. **Display backend.** The kernel has only the early text console. The
   prelinked closure has no `IOMobileGraphicsFamily`/`IOSurface`, and no
   `GopFramebuffer` archive is in this board. There is no first frame.
3. **Platform services.** AMFI/ApplePMGR need a PMGR nub; buttons/touch need
   kernel HID producers; KeyStore needs an AKS state backend. These are smaller
   than storage, but they gate a usable UI.

CPU count and memory (1 CPU / 256 MiB today vs 8 CPUs / 1 GiB in the reference)
are performance/stability work, not the first functional gate, but they should
be raised once the kernel survives past `bsd_init`.

---

## 1. Boot chain comparison

| stage | reference | sagit today | gap |
|---|---|---|---|
| bootloader | stock ABL, unlocked | stock ABL, unlocked | none |
| UEFI | Mu-Silicium-based, `sagit-uefi` branch | Mu-Silicium-based `Mu-sagit.img` | none |
| firmware payload | `UniXNURamDiskDxe` + LZMA | same, LZMA-capable | none |
| boot manager | UniXNU menu | UniXNU menu | none |
| OS loader | DarwinBoot | DarwinBoot | none |
| ADT | generated from captured FDT + Apple identity | generated from `dts/sagit.dts` + D20AP identity | identity is synthetic; hardware nodes are ours |
| kernel transport | ramdisk payload in boot image | ramdisk payload in boot image | none |
| kernel entry | XNU 8796 | XNU 8796 | none |

Both ports use the Android boot-image transport. The Mi 6 bootloader rejects a
ramdisk section above roughly 36 MiB, so the 44 MiB FAT payload volume travels
as an LZMA-alone stream (`UNXLZMA1`, 72-byte header). The LZMA-capable firmware
is the Mu-Silicium image (`Mu-sagit.img`); the EDK2 `boot-sagit.img` path uses
`SagitRamDiskDxe`, which only probes for a raw FAT boot sector and therefore
must **not** be handed the compressed payload. This is why the handoff image
below is `Mu-sagit.img`, not `boot-sagit.img`.

The reference's firmware is its own `edk2-msm8998` / Mu-Silicium variant; we do
not copy it. The payload handoff and menu are shared UniXNU behaviour. **R/H**

### 1.1 Firmware-specific gaps

| item | reference | ours |
|---|---|---|
| key input in firmware | SPMI PON/GPIO read in `SagitButtonsDxe` (ours) / reference board code | `SagitButtonsDxe` reads the SPMI arbiter directly, maps Vol+/Vol-/Power to UEFI menu keys, stops at `ExitBootServices` |
| display in firmware | GOP framebuffer left lit | same; the panel is already lit by the firmware |
| PMIC chain | vendor chain dead on Mu-Silicium | same; direct SPMI read is the workaround |
| fastboot USB | ABL leaves it running | same |
| serial console | none | none |

The firmware is not the blocker. The kernel-side backends are.

---

## 2. XNU build and patch comparison

### 2.1 Our build result

The new build (log `/data/opencode/tmp/build-kernel-fix2.log`) applied the full
arch/SoC/OS series plus the device series, including the two new patches, and
produced:

```
mach.development.bcm2837.unstripped.noctf
  17,823,632 bytes
  sha256 dd233ff6bdb9a6bdccf4376e0f0e63287745cd6684c6724345eb30c84757e101
  Mach-O 64-bit arm64, NOUNDEFS|PIE, minos 16.0 (sdk 16.0)
  __TEXT at 0xfffffff007004000
```

Prelinked and repacked for the boot image:

```
kernel.stripped.macho   39,837,696 bytes
sha256 eab3f026eb7d0c462e3b103044a0e8ae9ca3b758cde9e8c0fc680dec54744aa7
```

The prelink selects 18 components (not the 222 passengers carried in the
file layout):

```
AppleFSCompression.Zlib, AppleARMPlatform, AppleEffaceableStorage,
AppleMobileFileIntegrity, ApplePMGR, apfs, CoreAnalyticsFamily,
IOCryptoAcceleratorFamily, IOHIDFamily, IOReportFamily,
IOSlowAdaptiveClockingFamily, IOStorageFamily, Libm, corecrypto,
AppleMatch, CoreTrust, AppleImage4, sandbox
```

This is a security/storage/HID closure. It is **not** a display closure and it
does not include `pthread` (we build source pthread) or KeyStore.

### 2.2 Device patch delta

The reference's `patches/xnu/` contains eight device patches:

| reference patch | purpose | ours |
|---|---|---|
| `0001` platform integration | calls `cheeseburger_ufs_register`, `native_ram_block_register`, `cheeseburger_pmgr_startup` from `kernel_bootstrap`; BSD exec/exit hooks | not present; no board runtime |
| `0002` owner manifest publication | `XNU_OWNER_BOOT_MANIFEST` in `IOKitBSDInit` | not present; no root |
| `0003` visible early console | paints into the handoff framebuffer from `start.s` | our `0006` boot ladder does this, but it is disabled |
| `0004` console handoff | hands the early console to the kernel console | not present as a separate patch; our console works after `PE_init_kprintf` |
| `0005` console internal stages | console stage diagnostics | not present |
| `0006` complete diagnostics | fault/exit probes, panic log | not present; only panic screen |
| `0007` eight-core topology | enables 8 CPUs | our `0007` sets `MAX_CPU_CLUSTERS=2`, but `cpu_count=1` and no 8-core run |
| `0008` framebuffer normal-NC | framebuffer mapping attributes | not present; our lazy `ml_io_map_unmappable` path exists |

Our device series is mostly iOS 16 / 8796 portability work (tunables, AMCC,
pmap, pthread JIT callback, source machine layer, KPC backend, preemption,
CFLAGS, `vfs_getvfs`, etc.), plus the new `0028`/`0029`. It is not a superset of
the reference's runtime features. The missing runtime features are the board
runtime hooks, the early console/diagnostics, the 8-core topology and the
owner-manifest publication.

`0006-sagit-boot-ladder.patch` is still commented out in
`patches/xnu/series`. It is useful for silent early failures, but the kernel
now reaches IOKit, so it is not the first priority. Install it only when a
future change stops before the console.

---

## 3. CPU and memory

| item | reference | sagit today | gap / action |
|---|---|---|---|
| CPU count | 8 configured, R6 online=8 | `cpu_count = 1`; `0007` only fixes cluster count | raise to 8 after the kernel survives `bsd_init`; verify PSCI, GIC redistributors, IPI, timer |
| CPU topology | 4 gold + 4 silver, `cluster-type = "P"`/`"E"` | DTS has the clusters; only one CPU is started | start secondary CPUs; check `ml_parse_cpu_topology` |
| PSCI | SMC conduit; `CPU_ON` | `/psci` node present; secondary start unverified | verify `CPU_ON`, `SYSTEM_OFF`, `SYSTEM_RESET` |
| GIC | GICv3, redistributor stride 0x20000 | `gicd=0x17a00000`, `gicr=0x17b00000`, stride 0x20000 | matches; verify redistributor frames and SGIs |
| timer | 19.2 MHz, virtual INTID 19 | 19.2 MHz, INTID 19 | matches |
| memory window | 1 GiB requested | `owned_memory_size = 0x10000000` (256 MiB) | raise to 1 GiB after stability; reference needed 1 GiB for the UI |
| physmap / framebuffer | framebuffer outside the window; lazy I/O map | same; `ml_io_map_unmappable` path | works, but only one 2560×... mapping; check WCOMB attributes |
| page size | 4 KiB board config | 4 KiB | matches |

The loader log is:

```
ARM64 va=0xFFFFFFF007004000 pa=0x44C04000 args=0x45E64000 owned=0x10000000
video base=0x9d400000 display=1 row=4320 width=1080 height=1920 depth=32
```

The framebuffer is outside the 256 MiB window, which is why the kernel maps its
own alias. Raising the window to 1 GiB would put the framebuffer inside the
physmap and simplify the mapping, but it also changes the memory the loader
hands over; do it as a measured step, not together with other changes.

---

## 4. Storage, root and Data

### 4.1 Reference implementation

- `NativeUfs` is a warm-link MSM8998 UFS adapter: the bootloader has already
  initialized the host, so the kernel only reads. It uses the UFS host window
  at `0x1da4000` (size `0x2500`), the GCC block at `0x100000` (size `0xb0000`)
  and the UFS clock registers. It requires a `qcom,ufshc` node with a verified
  MSM8998 profile.
- The transport is UTP task management + READ(10), 4 KiB logical blocks,
  PRDT bounce buffer, bounded completion polling, no write path.
- `RootLayout`, `RootWindowRead`, `RootPublication`, `NativeUfsBlock` publish
  read-only windows to `IOStorageFamily`. The root policy verifies a SHA-256
  block table signed with RSA-3072 (`VerifiedRoot`).
- `NativeRamBlock` publishes a RAM-backed block device for the volatile APFS
  Data volume. The reference's P17 Data is a 1 GiB `DBRDISK1` seed, RAM-only;
  persistent Data is not enabled.
- The owner boot manifest is published after the real OS/SystemOS mounts and
  the root re-check; it hashes the real Manifest bytes and the role UUIDs.

### 4.2 Our state

- `CONFIG_XNU_NATIVE_UFS=n`; there is no UFS host driver, no GPT parser, no
  `IOBlockStorageDevice`, no root policy and no Data.
- `RamBlock` is in the board archive list but `CONFIG_XNU_NATIVE_RAM_BLOCK=n`;
  it is not registered.
- `IOStorageFamily` and APFS are prelinked, so the kernel has the Apple side
  of the stack, but nothing publishes a block device.
- `mockfs` provides the in-memory root for the current bring-up only.

### 4.3 What must be written

1. **UFS host driver**: map `0x1da4000`, read host capabilities, UIC power
   mode, UTP transfer request/response, PRDT, polled completion. Warm-link
   first; no PHY/clock re-init until the read path is proven. Facts needed from
   the sagit vendor DTS/schematic: UFS PHY base, regulators, clock branches,
   reset GPIO, LUN count and block size.
2. **GPT parser and userdata discovery**: the sagit GPT is not the OP5 GPT.
   Capture the real sagit partition table (LUN0 block 0, 4 KiB) before writing
   the root layout. The reference's `out/p12/geometry.json` is OP5-specific and
   must not be reused.
3. **`IOBlockStorageDevice`**: 512-byte window reads translated to 4 KiB UFS
   reads, bounds checks, serialized commands, read-only.
4. **Root auth**: choose the owner key and sign a SHA-256 block table over the
   OS/SystemOS windows. The `VerifiedRoot` kit is available; the reference's
   `RootAuth`/`RootDecision` is a design reference, not a binary to copy.
5. **Data**: a RAM-backed block device with an APFS container and a volatile
   Data volume. The reference's P17 seed is a starting point; the UUID and
   size must be sagit's own.
6. **Owner manifest**: after the real mounts, publish the manifest hash and
   role UUIDs so `mount` does not exit 66.

### 4.4 Licence note

The reference's `NativeUfs.cpp`, `NativeRamBlock.cpp` and the root layout
headers are `BSD-2-Clause-Patent`; they may be vendored with their headers and
hashes if we choose. The project policy is an independent implementation, so
the default is facts only. The touch file (`CheeseburgerTouch.cpp`) is
`GPL-2.0-only` and is facts-only regardless. `CheeseburgerPlatformPower.cpp`
is MIT and may be vendored with attribution.

---

## 5. Display

### 5.1 Reference implementation

- The display is a **CPU copy** path, not a hardware DSI/DPU driver:
  QuartzCore → original `IOSurface` / `IOMobileGraphicsFamily` →
  `NativeLegacyDisplay` (an `IOMobileFramebufferLegacy` subclass) → copy into
  the UEFI GOP framebuffer at `0x9d400000`.
- The original `IOMobileFramebufferLegacy` class and `IOSurface` getter are
  kept; unknown backend leaves remain fatal rather than being stubbed.
- The closure is 30 components: the original 19 plus 11 real dependencies,
  including `IOMobileGraphicsFamily`, `IOSurface`, `AppleM2ScalerCSCDriver`,
  `IODARTFamily`, the audio families and `Libm`.
- A software vsync sender drives the compositor; there is no physical
  `PP_DONE`/VBL event. The panel is already lit by the firmware GOP, so the
  reference does not re-init DSI or backlight.
- The UI was user-confirmed on iPadOS 17.7.11; the iOS 16 candidate's display
  binding is not confirmed.

### 5.2 Our state

- Only the early text console and the panic screen. The prelinked 18-component
  closure does **not** include `IOMobileGraphicsFamily`, `IOSurface`,
  `AppleM2ScalerCSCDriver` or `IODARTFamily`.
- `GopFramebuffer` is not in `[components]`/`archives` in `board.toml`.
- `CONFIG_XNU_NATIVE_LEGACY_DISPLAY=n`.
- The framebuffer mapping works for the console; there is no display kext.

### 5.3 What must be added

1. Prelink roots: `com.apple.iokit.IOMobileGraphicsFamily`,
   `com.apple.iokit.IOSurface`, `com.apple.driver.AppleM2ScalerCSCDriver`,
   `com.apple.driver.IODARTFamily`. Add the audio families only when audio is
   in scope.
2. `GopFramebuffer` component (or our own `NativeLegacyDisplay` equivalent)
   that subclasses the original `IOMobileFramebufferLegacy`, maps the GOP
   framebuffer, copies the visible `IOSurface`, and sends the software vsync.
3. `CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y`, and a framebuffer handoff from
   DarwinBoot that matches the 1080×1920, 32 bpp, row 4320 layout.
4. Console handoff: once the display kext owns the frame, the text console
   must stop overwriting it (the reference's `p20_scanout_state` design).
5. Acceptance: (a) text console still works; (b) a test pattern appears from
   the display kext; (c) SpringBoard's first frame appears after root.

The CPU path is the right first target. A hardware DSI/DPU driver is a much
larger project and is not needed to see the UI.

---

## 6. Touch

### 6.1 Reference implementation

- OP5: QUP5 v2 I2C PIO at `0xc179000`, pins SDA 87, SCL 88, RESET 89, INT 125,
  Synaptics RMI4 F12. The code is `GPL-2.0-only`, adapted from Linux
  `i2c-qup.c`, `pinctrl-msm8998.c`, `gcc-msm8998.c`; facts only for us.
- It reads RMI4 F01/F12, publishes frames to a `NativeHidInput` `IOHIDDevice`
  with the original HID report descriptor, and supports a polled fallback if
  the SPI/IRQ route is not exclusive.
- Physical validation was still pending in the reference at the time of P22.

### 6.2 Sagit hardware facts

From the vendor schematic (`docs/SAGIT-GPIO-MAP.md`):

| net | pin |
|---|---|
| `TS_I2C_SDA` | TLMM GPIO 87 |
| `TS_I2C_SCL` | TLMM GPIO 88 |
| `TS_RESET_N` | TLMM GPIO 89 |
| `TS_INT_N` | TLMM GPIO 125 |

The pins match the reference's QUP5 I2C pins. The controller model and the
power rails are **not yet confirmed** for sagit. The Mi 6 may use a Synaptics
RMI4 part or a different controller; do not assume OP5's F12 layout. The
reference's `P21_R5_SAGIT_INPUT_COMPARISON.md` explicitly says the sagit
target ABI in the `ios_sagit` project is iOS 18/22H374 and its hardware DPU is
different, so its private offsets are not ours.

### 6.3 What must be written

1. Identify the sagit touch controller (I2C address, reset sequence, firmware
   version, protocol). The vendor kernel/DTS or a bus dump is the source.
2. QUP5 I2C PIO driver with bounded transfers, clock/pin configuration, and
   no unbounded retries.
3. Controller protocol: RMI4 F01/F12 if it is Synaptics, otherwise the vendor
   protocol. Map to a stable multi-touch frame (id, x, y, pressure).
4. `NativeHidInput` `IOHIDDevice` with the original HID report descriptor;
   dispatch through IOHIDFamily; one release per press.
5. Calibration and edge handling; a bottom-centre tap region can feed Home.
6. Acceptance: touch coordinates visible in a kernel log/IORegistry, then
   SpringBoard can be tapped.

---

## 7. Buttons and Home

### 7.1 Reference implementation

`CheeseburgerButtons.cpp` maps three SPMI channels:

- PON PPID `0x008`, bit 0 = power.
- GPIO PPID `0x0c4`, bit 0 = volume down (OP5 GPIO5, active low).
- GPIO PPID `0x0c5`, bit 0 = volume up (GPIO6, active low).

It reads the SPMI arbiter at `0x0800f000`, discovers APIDs by PPID, maps the
observer channels at `0x09400000 + 0x8000*apid`, sends the observer READ
command, and polls every 10 ms with two stable samples. It does not configure
the PMIC, IRQ, clocks or reset. The HID usages are Volume Up/Down and Power;
a two-volume chord can synthesize Home.

### 7.2 Sagit wiring

From the schematic and the reference's same-SoC comparison:

| key | sagit path | note |
|---|---|---|
| power | PON PPID `0x008`, bit 0 | same semantic as OP5 |
| volume down | PON RESIN bit 1 | **not** OP5 GPIO5; do not reuse the OP5 mask |
| volume up | PM8998 GPIO 6, PPID `0x0c5`, active low | same GPIO as OP5, but verify the PPID/APID mapping |
| Home | no mechanical switch; fingerprint sensor via QSEE | Path 2 is our HID fallback |

The firmware `SagitButtonsDxe` already reads the same SPMI arbiter and maps the
keys to UEFI menu keys. The kernel has no button driver.

### 7.3 What must be written

1. A kernel SPMI poll thread with the sagit mask:
   `power = PON bit0`, `vol-down = PON bit1`, `vol-up = GPIO6 low`.
2. A `NativeHidInput` consumer that publishes Consumer/Keyboard usages; do not
   reuse the OP5 three-channel mask unchanged.
3. Home Path 2: publish Consumer `0x0C`/`0x40` from a source (volume-down edge
   or a bottom-centre touch tap), with neutral report on release and on stop.
4. Debounce (15 ms per key on sagit, or two stable 10 ms samples), boot-held
   release suppression, and a force-release timeout.
5. Acceptance: each key produces the expected HID event; Home unlocks the
   lock screen; no key sticks across a panic/reboot.

Path 3 (the real fingerprint sensor) is a separate secure-world project:
`FP_INT_N` GPIO 121 only says "something happened"; the sensor must be read
over BLSP12 SPI through QSEE, with the vendor TA loaded. It is not a shortcut
for Home and it is not Touch ID until the TA and match decision exist.

---

## 8. Power, PMIC, PMGR, watchdog and KeyStore

### 8.1 Reference implementation

- `CheeseburgerPlatformPower.cpp` (MIT) provides the device-scoped Apple PMGR
  interface compatibility. It is not a Qualcomm PMIC backend; it satisfies
  AMFI's `ApplePMGRNub` type check and the slot behaviour.
- AMFI's early initializer requires a usable `ApplePMGRNub`; without it the
  reference panicked `AMFI: No PMGR?` (`ConfigurationSettings.cpp:235`).
  The nub read is OR'd with `0x4`, then the write interface is called; the
  provider is at object offset `0x88`, dispatch slots 320/321.
- AKS (`AppleKeyStoreState` / `NativeKeyStoreEvents`) is a development-only
  state/selector service. There is no SEP, no real keybag, no hardware-backed
  unlock. The reference refuses to call a fake lock state "unlocked".
- `watchdogd` is isolated with development-state job rules; the reference does
  not disable other services or change code signing.

### 8.2 Our state

- `ApplePMGR` is prelinked, but there is no PMGR nub. `CONFIG_XNU_TRUST_CACHE_RUNTIME=n`
  keeps the AMFI runtime off, so the current storage-less kernel does not hit
  the `No PMGR?` panic. That will change when the security closure is enabled.
- No PMIC/SPMI kernel driver beyond the firmware button reader.
- No watchdog policy, no AKS state, no KeyStore.
- `PROPRIETARY`/SEP is absent; the target iPhone 8 has a SEP, but we cannot
  impersonate it.

### 8.3 What must be written

1. PMGR nub: match the sagit platform node, implement the read/write slots
   with a defined policy, and never write guessed hardware registers.
2. Enable `CONFIG_XNU_TRUST_CACHE_RUNTIME=y` only after the nub and the
   Image4/AMFI closure are ready; verify `AMFI registered: TrustCache
   interface=3`.
3. PMIC/SPMI: the button reader is read-only; a regulator/clock driver is
   needed for UFS, display and touch power. Keep it separate from the button
   path.
4. Watchdog: a policy that isolates `watchdogd` without disabling unrelated
   services; persistent log first.
5. KeyStore: an AKS state service is enough for some services to start, but it
   is **not** Touch ID. Do not claim unlock or keybag support without SEP.

---

## 9. USB, logging and diagnostics

### 9.1 Reference

- A 32 MiB persistent log window in userdata, read back from Recovery; no USB
  gadget. The reference explicitly lists USB as not implemented.
- Boot fault probe, exit probe, display observation, panic screen, a
  persistent kernel log ring.

### 9.2 Ours

- Early text console and panic screen; the doubled-character bug is fixed
  (§11). No persistent log because there is no storage. No USB.
- The boot ladder (`0006`) is disabled.

### 9.3 What must be written

1. After UFS: a persistent log window in a reserved userdata area, with a
   read-back tool. Do not write outside the reserved window.
2. USB: a DWC3 gadget or the `QcomXnuUsbDebug` component used by other ports.
   The reference's `NativeDwc3` note is a design reference; the component is
   not in our catalog.
3. Panic log: keep the screen renderer, add a persistent ring and a recovery
   reader.
4. Boot ladder: re-enable only if a future regression stops before the console;
   it costs framebuffer writes and is not needed now.

---

## 10. GPU, network, audio, camera

| subsystem | reference | sagit | action |
|---|---|---|---|
| GPU | none; CPU GOP only | none; Adrenoline is a separate a5xx project | keep separate; do not link GPU code into this repo |
| Wi-Fi | none | none | later; needs WCN3990/PCIe/MHI facts |
| Bluetooth | none | none | later; shares the WCN3990 transport |
| audio | original audio kexts in the display closure, no codec/route | none | later; needs WCD9341/clock/routing facts |
| camera | none | none | later; needs CSI/ISP/PMIC facts |

The display closure's audio families are not evidence of working audio. Do not
enable them just because they are in the reference's 30-component list.

---

## 11. The panic output fix (already in the build)

### 11.1 Symptom

Every panic and KDB character appeared twice, which made the panic screen hard
to read and could hide the first line.

### 11.2 Root cause

`PE_init_kprintf()` sets `PE_kputc = console_write_unbuffered` when
`serial_init()` fails. `consdebug_putc()` and `consdebug_putc_unbuffered()`
then wrote the same byte twice:

1. once through `console_write_char()` / `console_write_unbuffered()`;
2. once through `PE_kputc()`.

On a board with no enumerated serial port, both paths are the video console.

### 11.3 Fix

`patches/0028-sagit-panic-console-double.patch` adds
`consdebug_serial_mirror()` and only calls `PE_kputc()` when it is a real
serial backend:

```c
if (!console_is_serial() && !disable_serial_output &&
    PE_kputc != console_write_unbuffered) {
        PE_kputc(c);
}
```

The patch is in `patches/xnu/series` before `0007`. The build applies it; the
new kernel carries it. **B** The runtime check is the next flash: the panic
text should be single-spaced.

### 11.4 The first real panic, also fixed

The panic screenshot decoded to:

```
pc 0xfffffff0078d3114 = IODTGetICellCounts + 0x2c (ldr x8, [x0], x0 = NULL)
lr 0xfffffff0078d1434 = IODTMapInterruptsSharing + 0x290
```

`IODTMapInterruptsSharing` found the interrupt parent with `FindPHandle(1)`.
XNU registers interrupt parents by `AAPL,phandle` (`gIODTPHandleKey`), but our
ADT had only `phandle = 1`, so `FindPHandle(1)` returned NULL and
`IODTGetICellCounts(NULL, ...)` data-aborted.

Two fixes:

1. `soc/msm8998/dts/msm8998.dtsi` now emits both `phandle = <1>` and
   `AAPL,phandle = <1>` for the GIC; `tools/make-sagit-adt.py` adds
   `AAPL,phandle` to the converter's `U32` set. The regenerated ADT has
   `intc: phandle=1, AAPL,phandle=1`, and `timer: interrupt-parent=1`.
2. `patches/0029-sagit-iodt-interrupt-null-guard.patch` makes
   `IODTGetICellCounts(NULL, ...)` return one interrupt cell and zero address
   cells instead of dereferencing NULL. This is a safety net for any other
   node with an incomplete parent graph.

Regression test: `tests/test_adt_interrupts.py` (passes).

---

## 12. Prelink closure: 18 now, 30 in the reference

| component | ours | reference | needed for |
|---|---|---|---|
| `IOMobileGraphicsFamily` | no | yes | display |
| `IOSurface` | no | yes | display |
| `AppleM2ScalerCSCDriver` | no | yes | display/scale |
| `IODARTFamily` | no | yes | display/GPU memory |
| `IOHIDEventDriver` | no | likely in the 19 | touch/buttons event dispatch |
| `AppleKeyStoreState` | no | yes (state only) | KeyStore-dependent services |
| `AppleAudioClockLibs` | no | yes | audio |
| `AppleIISController` | no | yes | audio |
| `IOAVFamily` | no | yes | audio/HDMI |
| `IOAudio2Family` | no | yes | audio |
| `IOCECFamily` | no | yes | HDMI CEC |
| `IOHDCPFamily` | no | yes | HDMI HDCP |
| `Libm` | yes | yes | math |
| `apfs`, `IOStorageFamily`, `AppleFSCompression` | yes | yes | root/Data |
| `AMFI`, `Image4`, `CoreTrust`, `sandbox`, `AppleMatch` | yes | yes | security |
| `ApplePMGR`, `AppleARMPlatform`, `AppleEffaceableStorage` | yes | yes | platform |

Adding a root is not free: it changes the prelinked layout and the ABI
qualification. Add display components as one measured change, then touch/HID,
then KeyStore. Do not add all 11 at once.

---

## 13. Prioritized roadmap

### P0 — flash the current fix and learn the next wall

1. Flash `Mu-sagit.img` (sha256 `dbba476744dea098b59b12c255e8a02f5a6eda0a686d5abcb051743cfe18939e`).
2. Verify the panic screen is single-spaced and the `IODTGetICellCounts`
   panic is gone.
3. Capture the next panic/first failure. Likely candidates: PMGR/AMFI (if the
   security path is reached), CPU topology, or the mockfs/PID 1 transition.
4. Do not change the CPU count or memory window in the same flash.

Acceptance: a readable screen and a new first-failure line.

### P1 — make the kernel a full OS kernel

1. **8 CPUs / 1 GiB**: raise `cpu_count`, `owned_memory_size`, and verify PSCI,
   GIC redistributors, IPI and timer. Acceptance: all 8 CPUs online in the
   reference's `cpu` diagnostics, no cross-CPU framebuffer corruption.
2. **UFS + GPT + block device**: warm-link read-only driver, sagit GPT dump,
   `IOBlockStorageDevice` windows. Acceptance: `IOMedia` for userdata.
3. **Root auth + APFS**: sign the OS/SystemOS windows, mount read-only APFS.
   Acceptance: `mount` succeeds; no `Failed to mount root device`.
4. **Data**: volatile APFS RAM Data. Acceptance: writable `/private/var`.
5. **Display**: add the display closure and `GopFramebuffer`/`NativeLegacyDisplay`.
   Acceptance: first SpringBoard frame; console stops overwriting it.
6. **Touch + buttons**: QUP5 I2C, controller protocol, HID producer, sagit key
   mask, Home Path 2. Acceptance: touch and Home work on the lock screen.
7. **PMGR + AMFI/TrustCache**: PMGR nub, enable trust cache runtime, verify
   `AMFI registered: TrustCache interface=3`. Acceptance: userland reaches
   launchd.

### P2 — usability and diagnostics

1. Persistent log window and recovery reader.
2. USB debug channel.
3. KeyStore state service (not SEP).
4. Audio, Wi-Fi/BT, camera.
5. Performance: 8 CPUs, 1 GiB, scheduler/timer tuning.

### P3 — GPU

The Adreno a5xx stack stays in `ilertnost/Adrenoline`. This repository only
provides the software framebuffer path. Do not link the GPU driver here.

---

## 14. Handoff image and hashes

The current image is the Mu-Silicium path, because it contains the LZMA-capable
`UniXNURamDiskDxe`. The EDK2 `boot-sagit.img` path probes for a raw FAT boot
sector and is not compatible with the compressed payload.

```
/data/opencode/uefi-sagit/Mu-sagit.img
  11,155,456 bytes
  sha256 dbba476744dea098b59b12c255e8a02f5a6eda0a686d5abcb051743cfe18939e
  kernel part 6,129,247 bytes (the proven sagit-uefi firmware)
  ramdisk 5,023,744 bytes (the new LZMA payload)
```

Payload:

```
/data/opencode/unixnu-sagit/out/sagit-payload-volume.img.lzma
  5,023,744 bytes
  sha256 04129808fa6d62cb071896e906c3360cade617f2cb1a170988b3a98aafd9d883
  sha256 (uncompressed volume) 1108b7b4f6076d7d14cb17b4c138080dcb9ffd573ee14a72701a8ff75abb90be
```

Kernel and ADT inside the payload:

```
kernel.stripped.macho  39,837,696 bytes
  sha256 eab3f026eb7d0c462e3b103044a0e8ae9ca3b758cde9e8c0fc680dec54744aa7
kernel.macho.adt       16,616 bytes
  sha256 332e9f6bd00521a31e4c05c33724c5325e7f2d7c4a462bda1af04184a1e8ee91
```

Flash:

```sh
fastboot flash boot /data/opencode/uefi-sagit/Mu-sagit.img
fastboot reboot
```

The first boot after the flash should be observed for:

- UniXNU menu and DarwinBoot (unchanged);
- video console text, **single-spaced**;
- no `Kernel data abort` at `IODTGetICellCounts`;
- the next first-failure line, photographed or copied.

Keep the previous image until the new one reaches the same point. The userdata
partition is not touched by this image.

---

## 15. What not to claim

- The reference's iOS 16.7.16 userland is **not** proven. Its proven UI is
  iPadOS 17.7.11. Do not describe the reference's iOS 16 branch as a working
  end-to-end baseline.
- We do not have GPU, KeyStore, SEP, Touch ID, Wi-Fi, Bluetooth, audio or
  camera. The reference does not either (KeyStore is a state fixture only).
- The reference's `NativeUfs` and `RootAuth` are board code, not a generic
  MSM8998 driver. The sagit GPT, UFS PHY/clock/regulator facts and touch
  controller must be captured for this device.
- A boot-image transport success is not a runtime success. The reference's P23
  explicitly separates `fastboot` OKAY from kernel progress; do the same.

---

## 16. Reference evidence index

| topic | file |
|---|---|
| reference status and guarded profile | `README.md`, `board.toml`, `porting.toml` |
| iOS 16 candidate status | `docs/P23_STAGE_C_STATUS.md` |
| iOS 17 UI baseline | `docs/P21_UPSTREAM_BOOT_COMPARISON.md` |
| CPU and memory | `docs/P8_IMPLEMENTATION.md`, `docs/P21_OFFICIAL_ADAPTATION.md` |
| storage/root | `docs/P11_IMPLEMENTATION.md`, `docs/P12_ROOT_INTEGRATION.md`, `docs/P14_ROOT_AUTH.md` |
| Data | `docs/P17_DATA_IMPLEMENTATION.md` |
| display | `docs/P20_DISPLAY_IMPLEMENTATION.md` |
| touch | `docs/P22_TOUCH_IMPLEMENTATION.md`, `docs/P21_R5_SAGIT_INPUT_COMPARISON.md` |
| security/PMGR | `docs/P10_SECURITY_INTEGRATION.md` |
| diagnostics | `docs/P20_DISPLAY_IMPLEMENTATION.md`, `docs/P21_R17_COMPLETE_DIAGNOSTICS.md` |
| board runtime | `package/BoardRuntime/` |
| sagit hardware facts | `docs/SAGIT-GPIO-MAP.md`, `docs/HOME-BUTTON.md`, `docs/ADT-SAGIT.md` |

The reference files are read for facts under the per-file SPDX audit; no
reference code is copied into this repository by this document.
