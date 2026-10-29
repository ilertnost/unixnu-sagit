# ADR-0006 — A live kernel log channel: screen first, USB second

- Status: proposed
- Date: 2026-10-29

## Context

XNU is handed the machine and the screen stays black. There is no UART on this
board: the Mi 6 has no accessible debug pads, and the XNU ARM serial driver only
knows Apple's UARTs (`PI3_UART`, `VMAPPLE_UART`, `DOCKCHANNEL_UART`,
`APPLE_UART`), none of which exist on MSM8998. So the usual bring-up tool is
unavailable and every question about the kernel costs a flash cycle.

The request is a live kernel log over USB, so that the phone can be watched from
a host terminal while it boots.

Three facts shape the answer.

### 1. The kernel is silent by design, not dead

`pexpert/arm/pe_kprintf.c` initialises two independent switches:

```c
SECURITY_READ_ONLY_LATE(bool) disable_serial_output  = true;
SECURITY_READ_ONLY_LATE(bool) disable_kprintf_output = true;
```

`PE_init_kprintf` prints only when **both** are false:

- `disable_serial_output` is cleared by `debug=...` with the `DB_KPRT` bit
  (`0x8`), e.g. `debug=0x14e`;
- in a `DEVELOPMENT` build `disable_kprintf_output` is cleared only by the
  `-enable_kprintf_spam` boot argument (a `DEBUG` build clears it always).

`-v` alone does neither. The kernel we boot passes `-v` only, so it cannot draw
anything even when it runs.

When serial init fails, `PE_init_kprintf` falls back to
`console_write_unbuffered`, and `cons_ops_index` starts at `VC_CONS_OPS`, so the
fallback console is the video console. `DarwinBoot` already fills
`boot_args.Video` from GOP (`v_baseAddr`, `v_display = 1`, `v_rowBytes`,
`v_width`, `v_height`, `v_depth = 32`), and `PE_create_console` calls
`PE_initialize_console(..., kPEGraphicsMode)` because `v_display` is set.
`initialize_screen` then maps the framebuffer with `ml_io_map_unmappable` while
`kernel_map` is still null, which is the path XNU expects on a booter-provided
framebuffer.

So an on-screen kernel console is already implemented upstream and only needs
the two boot arguments. That is the cheapest live log that exists today: it is
live, it is the real kernel text, and it needs no new code.

### 2. XNU has no USB stack to attach a CDC ACM port to

A `cdc_acm` port on the device side needs, in this order: USB PHY, controller in
device mode, endpoint management, EP0 enumeration, a CDC ACM function, and a
character device. The reference port's `drivers/usb/QcomUSB` is 14.4k lines of
C++ and depends on IOKit and `IOUSBHost`-style infrastructure that this kernel
does not have. Nothing in the current image can speak USB after
`ExitBootServices`.

### 3. Mu-Silicium is a map, not a donor

`Silicon/Samsung/SamsungPkg/Drivers/UsbfnDwc3Dxe` is a working DWC3 *device*
driver for UEFI: core, endpoints, transfers, 2191 lines. It is exactly the layer
we would otherwise have to invent. But its `Source.txt` names the origin:

```
REPO: https://github.com/LineageOS/android_kernel_samsung_universal9830/
FILES: drivers/usb/dwc3/core.c, gadget.c, ep0.c ...
```

It is derived from the Linux `dwc3` driver, i.e. GPL-2.0-only. That is the hard
boundary in `docs/LEGAL.md`: it cannot be copied into a GPL-3.0/BSD firmware
tree, and it cannot be linked into XNU. It is readable as documentation of
register order and platform sequencing. The same applies to `PlatformUsbfnLib`,
which is the per-SoC half (PHY, clocks, PMIC) and has no MSM8998 instance in any
tree we have.

`FrameBufferSerialPortLib` and `Font.h` are worth knowing about: the UniXNU
panic-screen renderer uses that font. That is a UEFI-side console, not a kernel
log channel.

## What MSM8998 needs for USB 2.0 device mode

Taken from the Linux MSM8998 device tree (`qcom/msm8998.dtsi`), as hardware
facts only:

| Block | Address | Notes |
|---|---|---|
| DWC3 core | `0x0a800000` | length `0xcd00` |
| DWC3 wrapper | `0x0a8f8800` | length `0x400`, `qcom,msm8998-dwc3` |
| QUSB2 PHY | `0x0c012000` | length `0x2a8`, enough for high speed |
| QMP USB3 PHY | `0x0c010000` | only needed for SuperSpeed; skip |

Clocks: `GCC_CFG_NOC_USB3_AXI`, `GCC_USB30_MASTER` (120 MHz),
`GCC_AGGRE1_USB3_AXI`, `GCC_USB30_SLEEP`, `GCC_USB30_MOCK_UTMI` (19.2 MHz),
`GCC_USB_PHY_CFG_AHB2PHY`, `GCC_RX1_USB2_CLKREF`.
Power domain: `USB_30_GDSC`. Resets: `GCC_USB_30_BCR`,
`GCC_QUSB2PHY_PRIM_BCR`.

A console needs none of the SS PHY, none of the Type-C logic and no DMA
scatter-gather. High speed device mode with one interrupt IN endpoint is
sufficient for text.

## Options

**A. On-screen kernel console.** Pass `-v -enable_kprintf_spam debug=0x14e`.
XNU prints to the DarwinBoot framebuffer. No code. Live, but a photograph, not a
terminal, and it dies with the first panic that trashes the screen.

**B. USB CDC ACM in UEFI.** Implement DWC3 device mode plus a CDC ACM class
driver inside the firmware, using Mu-Silicium only as a map. Result: the host
sees `/dev/ttyACM0` *before* the kernel runs, so the `DARWINBOOT:` lines and
`video base=` become readable, and the whole hardware path (PMIC, clocks, PHY,
connector) is proven independently of XNU. Effort is dominated by the platform
layer, not by DWC3 itself.

**C. Handoff log channel (target).** B leaves the gadget enumerated and armed.
`DarwinBoot` writes the DWC3 base, the endpoint number and the transfer ring
into the ADT under `/chosen`. XNU then needs only a polled transmitter: a
`struct pe_serial_functions` with `init`/`transmit_ready`/`transmit_data` that
pushes bytes into the prepared endpoint and rings one doorbell, registered from
`serial_init()` the way `PI3_UART` and `APPLE_UART` are registered today. That is
a few hundred lines, no interrupts, no allocation, no IOKit, and it makes
`serial_init()` succeed so `kprintf` takes the normal serial path. The host side
already exists: UniXNU ships `scripts/debug_terminal.py` talking the giulia
protocol over CDC, so the device only has to answer `ping`, `status` and
`replay` to be usable.

**D. Full IOKit USB stack in XNU.** The reference implementation. Weeks of work,
and it buys host-mode USB that a log channel does not need. Rejected for now.

## Decision

1. Do **A** immediately: it costs two boot arguments and answers the open
   question "does the kernel run at all".
2. Then do **B**: a UEFI-side CDC ACM device. It is independently useful, it
   de-risks the hardware path, and it is the prerequisite for C.
3. Then do **C**: the handoff channel, which is the actual goal — a live kernel
   log over USB with our own driver.
4. Keep **D** out of scope until the graphics work needs host-mode USB.

Every line of B and C is ours. Mu-Silicium and the reference port are read for
register order, addresses and failure modes; no source is copied, and no
GPL-2.0-only code enters this tree.

## Consequences

- Stage A gives evidence this week; stages B and C are measured in weeks.
- B adds a CDC device to the firmware, so the boot image budget matters:
  `FVMAIN` is at 99% and `FVMAIN_COMPACT` has ~600 KB. The USB driver does not
  fit in `FVMAIN`; it has to go into the compressed volume or the payload.
- C makes `kprintf` use the serial path, so `console_write_unbuffered` and the
  video console stay as the fallback if the USB channel fails to initialise.
- The ADT gains a `/chosen` node owned by this channel. It travels with the
  payload, which is already versioned with the kernel.

## Open questions

- Which ABL path leaves the DWC3 controller: does it keep USB 2.0 PHY powered
  after a normal boot, or does the first register access hang? Needs the
  `probe` treatment that `SagitReportDxe` already applies to the ramdisk search.
- Does the Li-ion/charger path matter? USB device mode on this board may depend
  on VBUS detection through the PMIC, which the firmware does not touch yet.
- `GCC` clock and `GDSC` control require the RPM/RPMh or direct register access
  with the right vote. ADR-0005 defers that work to the Mu-Silicium stage; the
  USB channel may force part of it earlier.
