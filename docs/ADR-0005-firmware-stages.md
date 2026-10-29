# ADR-0005 — EDK2 for the first boot, Mu-Silicium as the real firmware

- Status: accepted
- Date: 2026-10-29

## Context

The boot chain we build today is `edk2-porting/edk2-msm8998`: a pinned EDK2 core
(January 2023) with an upstream MSM8998 platform layer, and it boots on the
phone. What it does not have is any driver for the power side of the SoC.

On MSM8998 the CPU voltage and frequency are set by XBL before anything else
runs. XBL picks a conservative point so that *any* payload can execute, and
nothing in our chain moves it afterwards:

- no SPMI bus, so no PM8998/PMI8998 regulator access,
- no RPM/RPMh clock and power-domain control,
- no CPR (Core Power Reduction) closed loop, which is how this SoC picks a
  voltage per frequency,
- no thermal, charger or battery management.

The consequence is that the AP runs at whatever the bootloader left it at. That
is survivable for a kernel that prints to UART, and it is a trap for everything
after that: shader compilation, APFS, and any rendering are CPU-bound, and a
core pinned near its lowest operating point turns a fast machine into a
misleadingly slow one. The first symptom would look like a graphics stack that
"works but is unusable", with the real cause two layers down.

Project Mu with the Silicium Qualcomm driver set is the firmware that does have
this: SPMI, PMIC, regulator, clock, RPM, USB, storage, display, thermal.

## Decision

Two stages, and the second must not block the first.

1. **First boot: EDK2, as it is.** The image that already boots is the one that
   carries UniXNU and DarwinBoot, and it is the fastest route to a kernel
   running in RAM. No power work happens here.
2. **Target firmware: Mu + Silicium.** Moving is the plan, not a fallback, as
   soon as the kernel boots. That is where CPU frequency, regulators, storage
   and display live.

## Consequences

- Anything measured on the EDK2 image is a lower bound on performance. A slow
  result there is not evidence about the graphics stack; it is evidence about
  the firmware.
- The Mu move replaces the pinned EDK2 core, so the FdtLib/Guid backport forced
  by the UniXNU modules disappears with it — a 2023+ core has that API. The
  DSC/FDF wiring is redone against Silicium's MSM8998 platform.
- The Mu move is also the point where a real driver surface starts to exist, and
  therefore where our own GPLv3 code can link against a firmware that actually
  manages the SoC.
- Cost of the delay: none if it stays second. Cost of doing it first: the kernel
  would still be unverified while the firmware work grows.
