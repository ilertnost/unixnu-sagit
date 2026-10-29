# ADR-0003 — EDK2 as the boot chain

- Status: accepted
- Date: 2026-10-29

## Context

Nothing in this repository can reach the phone yet. The graphics stack needs an
XNU that is actually running, and XNU needs to be started by something. That
something is the boot chain, and it is now the largest single piece of work
left in the project.

The device facts that constrain the choice:

- The Mi 6 bootloader is **unlocked**. Confirmed by the owner.
- The partition layout is the **legacy** one: no A/B slots, no dynamic
  partitions. `fastboot getvar product` does not answer on this bootloader,
  which is normal for it and not a sign of a problem.
- The stock chain is Qualcomm XBL -> ABL (LittleKernel) -> `boot`.
- ABL loads the `boot` partition as an Android boot image and jumps to the
  kernel with the AArch64 Linux boot protocol: `x0` points at the device tree.

So whatever we put in `boot` has to present itself as a kernel to ABL, and from
that point on we own everything.

## Options

1. **Carry the kernel from Android/Linux.** Wrong shape: we are not booting
   Linux, and we would inherit a driver model that does not match XNU's.
2. **Write a bootloader from scratch.** It would be a UEFI implementation with
   one user. Months of work to arrive at a worse EDK2.
3. **EDK2.** BSD-2-Clause, real UEFI, AArch64, already has the exact entry path
   we need: `ArmVirtPkg` enters its PrePi module by PE entry with `x0` holding
   a device tree pointer. That is the handoff ABL already gives us.

## Decision

The boot chain is **EDK2 plus our own bridge**.

```
ABL (stock, unlocked)
  -> boot partition: Android boot image wrapping our payload
  -> EDK2 core (ArmVirtPkg PrePi + our platform DXE)
  -> bridge: patch the device tree, load XNU, jump
  -> XNU + our kexts
```

Responsibilities are split so that the licences stay clean:

- **EDK2** is used unmodified where possible and with notices where patched.
  It is BSD-2-Clause; it is not our code and we do not pretend otherwise.
- **The platform layer is ours**: Mi 6 memory map, TLMM, PM8998, UFS, display.
  Everything the reference port wrote is off limits; EDK2 and the public
  MSM8998 documentation are the sources.
- **The bridge is ours**: it is the piece that turns UEFI into an Apple
  platform, and it is where the device tree we have been decoding is used.

## Consequences

- `boot` holds only the chain. The OS lives on `userdata` as an APFS container
  with System and Data volumes. They are flashed separately and always will be.
- We get a real boot environment: EFI applications, a filesystem abstraction,
  and a place to run diagnostics before XNU exists.
- The first milestone is not "iOS boots". It is "our EDK2 reaches its own shell
  on the phone's display". That is testable and it validates the handoff, the
  memory map, and the display path at once.
- Debugging is constrained: there is no serial console by default. Bring-up
  output has to go to the framebuffer, and a failed payload has to fall back to
  Fastboot rather than hanging.
- The stock Android boot image is preserved before the first write, so the
  phone can always be returned to a working state with `fastboot flash boot`.

## Revision (same day) — the platform already exists

The first version of this ADR assumed the Mi 6 platform layer had to be written.
It does not. `edk2-porting/edk2-msm8998` is a public EDK2 firmware for Snapdragon
835, and it ships `Platforms/Msm8998Pkg/Device/xiaomi/sagit` — Mi 6 support is
**upstream there**, not something we have to invent.

What that changes:

- The platform description (memory map, TLMM, PM8998, UFS, panel) is **not ours
  to write**. We use it, unmodified where possible.
- The platform code carries BSD headers ("licensed and made available under the
  terms and conditions of the BSD License"), which is GPL-compatible.
- One component is not: `GPLDrivers/Library/simple-init` (BigfootACA/simple-init,
  GPL-2.0-only) is pulled into `sagit.fdf` via `INF src/main/SimpleInitMain.inf`.
  GPL-2.0-only is not compatible with GPL-3.0-or-later, so it must be **excluded**
  from our build. We do not need it: it exists to chain-load Linux, and we chain
  load XNU.

So the split becomes:

| layer | who writes it | licence |
|---|---|---|
| EDK2 core | upstream tianocore | BSD-2 |
| msm8998 + sagit platform | `edk2-porting/edk2-msm8998` | BSD |
| simple-init | **excluded** | GPL-2.0-only |
| XNU bridge | us | GPL-3.0-or-later |
| graphics stack | us | GPL-3.0-or-later |

The first milestone is unchanged, and it just got much cheaper: **upstream EDK2
reaching its shell on the phone's display**. No custom code at all.

The bridge remains entirely ours, and it is now the first thing we write rather
than the second.

## Open questions



- Whether ABL on this firmware actually passes a usable device tree in `x0`, or
  whether we have to synthesise the platform description ourselves.
- The exact `boot` partition size limit, which bounds how much firmware we can
  ship in the image.
- Whether the panel can be brought up early enough to show anything before the
  full display driver exists.
