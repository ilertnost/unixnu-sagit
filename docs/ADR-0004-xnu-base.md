# ADR-0004 — Build and boot XNU on top of UniXNU

- Status: accepted
- Date: 2026-10-29

## Context

XNU as Apple publishes it does not build for, or boot on, a non-Apple SoC. It
wants Apple's clang (for `__builtin_xnu_type_*` in `kalloc.h`), Apple's `ld64`
(lld rejects `-kernel`), `mig`, `iig`, and a prelinked Apple kernelcache to
supply the kexts we do not build from source.

That work is done in the open. UniXNU carries it: a Linux toolchain installer,
a build driver, and per-component drivers. Under BSD-2-Clause-Patent, so it can
be used from a GPLv3 project.

More importantly, **two of its ports are MSM8998**, our SoC:

- `chiron`, Xiaomi Mi MIX 2 — minimal 4 KiB XNU with its own PID 1, heartbeats
  observed on hardware.
- `cheeseburger`, OnePlus 5 — main branch tracks iOS 16.7.16 (20H392), the same
  build this project targets. XNU boots, root mounts, no GPU.

Doing this from zero would mean re-deriving the toolchain, the 4 KiB page work
and the prelink against Apple's kernelcache before a single line of ours ran.
That is months, and it is not where the project's value is.

## Decision

We take UniXNU as the **base for the kernel side**, not as a source of device
code:

| taken | not taken |
|---|---|
| the Linux toolchain (patched clang, cctools-port, mig, iig, lzfse) | their device repositories (`chiron`, `cheeseburger`, ...) |
| the build driver (`./unixnu`) and format-1 device profiles | their board configurations, device trees, DTS |
| their generic components — `ArmDTPlatform`, `ArmCpu`, `RamBlock`, `DarwinBoot` | any port-specific patch written for another phone |

Everything under `xnu/sagit/` is ours and is GPLv3. Vendor code keeps its own
header and licence; the licence table in `docs/LEGAL.md` is extended
accordingly.

## Consequences

- The first bootable kernel is a **minimal RAM-rooted XNU**, not a full iOS. No
  storage driver: the Mi 6 uses eMMC through SDHCI, while the other MSM8998
  ports drive UFS. Irrelevant for a ramdisk root, blocking for anything that
  has to persist.
- Their components are BSD-2-Clause-Patent, which carries a patent grant and is
  compatible with GPLv3 in the direction we use it (our project includes their
  code, not the reverse).
- We depend on their build driver's file format. If it changes, `xnu/sagit/`
  needs the same change; the format is small enough to maintain by hand.
- The GPU work — the reason this project exists — stays entirely ours. Their
  ports explicitly have no Adreno 540 and no Metal.
