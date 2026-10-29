# Home button on Mi 6

Goal: reach a state where the iOS UI is past the lock screen, so the graphics
work in this repository can actually be looked at.

## What the target identity expects

From the D20AP device tree in `20H392`:

```
buttons {
    compatible            = "buttons"
    home-button-type      = <1>
    button-delay_hold     = <75000>
    opposed-power-vol-buttons = <1>
    function-button_volup_pre_p2   = ...
    function-button_voldown_pre_p2 = ...
    function-button_ringerab       = ...
    interrupt-parent_pre_p2 = <0x24>
    supports-trm
}
```

`home-button-type = <1>` is the solid-state variant. The iPhone 8 has no
clicking Home button; a force sensor reports contact and the Taptic Engine
fakes the click. So the stack we are impersonating is built to consume
"sensor says finger is here", not "switch closed".

Mi 6 is the same shape of problem: the only Home-area sensor is the fingerprint
sensor under the glass, and it reports finger contact. The analogy is real, and
that is why this port is worth doing at all.

## What the system consumes

The Home action reaches SpringBoard as a HID event, Consumer page `0x0C`,
usage `0x40` (`Menu`). That is distinct from the keyboard `Home` key and from
`AC Home`, and it is what an accessibility switch or an external keyboard sends
when it presses Home.

A minimal report is one byte: bit 0 asserted while the finger is down, cleared
on release. Releasing matters: a Home that never releases leaves the system in
a press-and-hold state.

## The paths, cheapest first

### Path 0 — AssistiveTouch, no driver at all

Enable `AssistiveTouchEnabled` in the accessibility preferences on the data
volume before boot. The on-screen Home control appears over the lock screen and
is driven by the touchscreen, which already works.

Cost: a plist. Risk: whether SpringBoard honours a pre-seeded preference on a
fresh boot is unverified.

This is the fastest way to answer "does the UI actually come up", and it is
independent of everything below.

### Path 1 — describe an existing key as Home

Volume-down arrives on PM8998 GPIO6. If we declare it as the home button in the
device tree and Apple's native button driver binds, Home works with no new
driver code at all. The cost is losing volume-down.

Cost: a device-tree patch. Risk: depends on the native button driver existing
and matching on our platform, which is unknown.

### Path 2 — our own HID device

A small `IOHIDDevice` that publishes Consumer `0x0C`/`0x40`, fed from PM8998
GPIO6 (volume-down), or from a bottom-centre tap region on the touchscreen as a
second source. No secure world involved.

Cost: one driver, one event source. Risk: low, and it is a fallback that does
not depend on Apple's drivers.

### Path 3 — the real thing: the fingerprint sensor

`FP_INT_N` on GPIO121 only says "something happened". What happened is read
over SPI through QSEE, which needs the vendor trusted application loaded by
firmware that initialises the secure world first.

Cost: firmware extraction, TA loading, SMC transport, plus the sensor protocol.
This is the correct long-term answer and the one that gives a real Home button,
but it is weeks, not an evening, and it depends on the boot path already working.

## Order

The chosen order is Path 2 first, then Path 3 for Touch ID. Path 2 is the only
option that is both cheap and honest: it does not depend on Apple's drivers
binding to our platform, it does not need the secure world, and the logic can
be tested before the phone is ever flashed. Path 0 and Path 1 stay on the table
as one-hour experiments if Path 2 hits something unexpected once there is
hardware to run it on.

## Path 2 implementation

The decision is to build Path 2, then reuse the same sensor plumbing for Touch
ID later.

What exists today is the part that does not need hardware: the event contract
and the state machine, in `include/adrenoline/home.h` and `src/home/home.c`,
with host tests in `tests/test_home.c`.

The pieces:

- **Report.** One byte, bit 0 asserted while Home is down. The descriptor
  declares Consumer page `0x0C`, usage `0x40`, inside a Consumer Control
  application collection, plus seven constant padding bits so the report is a
  whole byte. `al_home_report_descriptor()` hands out exactly those bytes.
- **State machine.** `al_home_key()` takes a debounced key edge, `al_home_touch()`
  takes a panel coordinate, and both feed one click limiter. It enforces three
  things the hardware does not: a duplicate "down" is not a second click, a new
  press inside the repeat window is contact bounce and is dropped, and a press
  that never releases is force-released by `al_home_tick()`. `al_home_reset()`
  emits the neutral report, so a pause, a stop, or a source error can never
  leave the system believing the button is held.
- **Time is injected.** Nothing in the module reads a clock. The source loop
  passes monotonic milliseconds, which is why the behaviour is testable on the
  host and why a suspend cannot reorder events.

Two sources are wired to it, either one alone is enough to unlock:

1. PM8998 GPIO6, the volume-down line. It needs the PMIC interrupt path that
   the key driver already uses, so it arrives as a plain key edge.
2. A tap in the bottom band, centred, 120 px tall on a 1080x1920 panel. This is
   free: the touchscreen driver already delivers coordinates. A drag, a touch
   outside the band, and a hold longer than 400 ms are not taps.

### IOKit binding

The binding is deliberately thin, because the logic above is already tested:

```
start()
  -> IOHIDDevice subclass, set report descriptor from
     al_home_report_descriptor() before attach
  -> register the gadget, then open the source
stop()
  -> al_home_reset()   (neutral report, no held button)
  -> close the source
setPowerState(kIOPMDeviceUsbPowerStateOff ...)
  -> al_home_reset()
```

The emit callback is the only place that touches IOKit: it copies the single
byte into an `IOHIDReport` and calls `handleReport()`. Everything else stays
portable C, which is what lets `ctest` cover it without a phone.

### What is not done

Nothing here has run on the device. The module is tested on the host only, and
the binding above is a plan, not code. It also assumes the source driver is
already delivering edges, which depends on the PMIC path coming up during boot.

## Touch ID later

The same sensor serves both jobs, and the split is worth stating now so the
driver is not built twice:

- **Home** is a HID event. Any source can produce it, which is why Path 2 works
  at all.
- **Touch ID** is an identity check. It needs the sensor's own protocol over
  SPI, through the secure world, and it needs a match/no-match decision that
  only the vendor trusted application can make.

So Path 3 does not replace Path 2; it adds a second consumer on the same sensor.
Home will keep working when the fingerprint stack arrives, and the fingerprint
stack will not have to pretend to be a button.

