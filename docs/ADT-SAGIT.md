# The ADT for sagit

XNU does not read a flattened device tree. On Apple hardware the bootloader
hands it an **Apple device tree** (ADT), a different, headerless format that
describes the platform *and* the device identity: serial placeholders, display
geometry, product strings, security state. This document describes the ADT this
project generates for the Xiaomi Mi 6, how to regenerate it, what was taken
from Apple's iPhone10,1 tree, and what is still guesswork.

Two tools live in `tools/`:

| tool | purpose |
|---|---|
| `adtinfo.py` | read an ADT or an FDT, print a tree (`--json` for machines) |
| `make-sagit-adt.py` | compile `xnu/sagit/dts/sagit.dts`, join it with the iPhone10,1 identity, write the ADT |

Tests: `tests/test_adtinfo.py` (ctest name `adtinfo`).

## Wire format

The ADT has no magic and no header:

```
u32 property_count
u32 child_count
property_count × {
    char name[32]          NUL terminated, zero padded
    u32  length            bit 31 set = the value is a /syscfg reference
    u8   value[length]     padded to a multiple of four
}
child_count × { a complete node }
```

A node's own name is the string value of its `name` property, so the root node
and every child are self-describing. The reference flag is iBoot's: a value such
as `syscfg/SrNm/0x20` is a placeholder that the bootloader replaces with fused
data. `adtinfo` preserves the flag, prints the raw value, and marks it.

`adtinfo.py` also accepts an FDT (`d00dfeed`) so that `hardware.dtb` can be
inspected with the same command. `--json` emits every property with its decoded
`type`, `value`, the raw bytes as `hex`, and `reference`; the decoded value is a
heuristic, the hex is not.

The heuristic has one unavoidable ambiguity: a four-byte integer whose bytes
happen to be printable ASCII is shown as a one-character string (the
`cluster-type` property is `'E'` or `'P'` for exactly this reason). The `hex`
field in `--json` is authoritative; use it when the distinction matters.

## Identity: what is Apple's and what is ours

`make-sagit-adt.py` mirrors the identity tables of the D20AP (iPhone 8 /
iPhone10,1) ADT extracted from iOS 16.7.16 (20H392). The rationale: XNU's
platform expert matches on names such as `D20AP` and reads `/chosen` keys it
expects to exist; presenting the SoC honestly as `qcom,msm8998` while keeping
the platform identity Apple's code recognises is the smallest lie that lets the
kernel reach its own drivers.

Values that are hardware facts come from the board, not from Apple:

| value | source |
|---|---|
| `chip-id = 0x8015` | `BuildManifest.plist`, `ApChipID` of the d20ap identity |
| `board-id = 0x02` | the same manifest, `ApBoardID` |
| `dram-base = 0x40000000` | the DTS memory node, which comes from the firmware's own map |
| `dram-size = 0xf0000000` | the same node (3.75 GiB, the window the firmware advertises) |
| `unique-chip-id` | the caller, mandatory, nonzero and different per build |
| CPU count, affinities, cluster kinds | MSM8998: 4× A53 at `0x0-0x3`, 4× A73 at `0x100-0x103` |
| `intc`, `timer` | Qualcomm GICv3 at `0x17a00000` and the ARMv8 generic timer |

Everything else in the identity tables is copied from Apple as data:

- root: `model`, `target-type`, `target-sub-type`, `manufacturer`,
  `device_type`, and the first three `compatible` entries
  (`D20AP`, `iPhone10,1`, `AppleARM`);
- `/chosen`: the security/AMFI/system-trusted integers and the placeholder
  blobs (`boot-manifest-hash`, `firmware-version`, `unique-device-id`, …);
- `/defaults`: the 17 scheduler/VM tunables;
- `/options`: empty, but present;
- `/product`: product strings and the 20-byte `product-id`;
- `/buttons`: descriptive properties only.

Deliberate divergences:

- `/chosen/osenvironment` is `"ramdisk"` (ours). This kernel boots from the FAT
  payload, not from an installed System volume.
- `/buttons` has no `function-*` properties. Those encode Apple PMIC/GPIO
  operations; no equivalent hardware exists on this board, and inventing a
  binding would be worse than omitting it.
- `/product` carries no camera/FaceTime/Maps capability subtrees. They describe
  iPhone 8 hardware, not the Mi 6.
- The root `compatible` appends the DTS strings after Apple's:
  `D20AP`, `iPhone10,1`, `AppleARM`, `device-tree`, `qcom,msm8998`.

## Generating

The ADT is a pure function of the arguments except for `random-seed`, which the
shared converter fills from 64 fresh random bytes. Two runs therefore have the
same tree and the same property values but different bytes and SHA-256. The
intermediate `hardware.dtb` is byte-for-byte reproducible.

```sh
export TMPDIR=/data/opencode/tmp
mkdir -p out/adt
python3 tools/make-sagit-adt.py \
    --output out/adt/kernel.macho.adt \
    --unique-chip-id 0x0123456789abcdef \
    --chip-id 0x8015 \
    --dram-size 0xf0000000 \
    --kernel-args=-v \
    --keep-dtb out/adt
```

- `--unique-chip-id HEX` is required and must be nonzero.
- `--dram-size` is a cross-check, not an override: it must equal the single
  `/memory` window in the DTS or the tool refuses to run.
- `--kernel-args` is validated (≤ 1023 printable characters, no NUL) but is
  **not written into the ADT**. XNU receives boot-args in `boot_args` from
  DarwinBoot, not here. Use `--kernel-args=-v` when the value starts with `-`.
- `--keep-dtb DIR` keeps `hardware.dtb`, which is otherwise built in `TMPDIR`
  and deleted.

`dtc` (≥ 1.4) must be on `PATH`. The converter is loaded from
`$UNIXNU_SRC/scripts` (default `/data/opencode/unixnu-src/scripts`) and is not
modified.

Output of the sample run:

```
ADT: out/adt/kernel.macho.adt
  bytes: 16576
  sha256: 58fb1182c19d11942c0fb8a14f790f33d01ef70a451132cf24057459a19720c6
  dram: 0x40000000 + 0xf0000000
  chip-id: 0x00008015
  unique-chip-id: 0x0123456789abcdef
  hardware dtb: out/adt/hardware.dtb
```

`hardware.dtb` is 2853 bytes, SHA-256
`dc3fbb1656fddbb94f41893b0b939002980aabea5882d824db74020b55b351bf`.

## What the generator actually emits

`generate()` in `unixnu-src/scripts/unixnu/devicetree.py` owns the shape. It
emits only these nodes:

```
device-tree
├── chosen          (boot identity, NVRAM proxy, memory-map, manifest-properties)
├── cpus            (one child per enabled CPU, all with cluster-type)
├── psci            (only because more than one CPU is enabled)
├── arm-io          (only nodes carrying unixnu,adt-name: intc, timer)
├── defaults
├── options
├── product
└── buttons
```

## Shape comparison with `d20ap.adt`

The real iPhone10,1 ADT is 185084 bytes and parses exactly to EOF; ours is
16576 bytes. The difference is not a bug: Apple's tree describes a whole
phone, ours only what the current kernel can use.

| item | `d20ap.adt` (20H392) | sagit ADT |
|---|---|---|
| root children | `chosen`, `options`, `cpus`, `aliases`, `memory`, `pram`, `vram`, `arm-io`, `buttons`, `port-lightning-1`, `port-inductive-1`, `backlight`, `baseband`, `sacm`, `defaults`, `product`, `filesystems`, `osenvironments` | `chosen`, `cpus`, `psci`, `arm-io`, `defaults`, `options`, `product`, `buttons` |
| CPU nodes | 6 (2 P + 4 E) | 8 (4 E A53 + 4 P A73) |
| `/chosen` properties | 72 | 50 |
| `/arm-io` children | 82 | 2 (`intc`, `timer`) |
| `AAPL,phandle` | root, `/chosen`, `/arm-io` | absent |
| root hardware nodes (`backlight`, `baseband`, `sacm`, ports) | present | absent |
| `/aliases`, `/memory`, `/pram`, `/vram`, `/filesystems`, `/osenvironments` | present | absent |
| `/psci` | absent | present (SMC) |

`/chosen` has 24 of Apple's properties missing and 2 extra. Missing:
`#address-cells`, `AAPL,phandle`, `backing-color`, `backing-glass-material`,
`bootp-response`, `cover-glass-material`, `coverglass-color`,
`display-boot-rotation`, `display-corner-radius`, `display-rotation`,
`display-scale`, `enclosure-material`, `ephemeral-storage`, `housing-color`,
`mac-address-bluetooth0`, `mac-address-ethernet0`, `mac-address-ethernet1`,
`mac-address-wifi0`, `marketing-hardware-behavior`,
`marketing-software-behavior`, `repair-status`, `root-matching`,
`software-behavior`, `software-bundle-version`. Most are iBoot syscfg
placeholders or display/network facts that belong to the iPhone. The extra
properties are `dram-base` and `dram-size`, which the converter adds for this
board. Note that `nvram-*` and `random-seed` are generated by the converter and
are not part of the mirrored table.

Exported `/arm-io` children deliberately carry no `#address-cells` or
`#size-cells`: the converter's `hardware_properties()` whitelist does not copy
them. They do carry `reg` translated into the `arm-io` `ranges` window, which
starts at the lowest exported register rounded down to 16 MiB — here
`0x17000000`, size `0x01000000`. The GIC entry therefore appears at offset
`0xA00000`, and its phandle is preserved for the timer's `interrupt-parent`.

## Validation performed

1. Generate the ADT with the command above (`--keep-dtb`).
2. `python3 tools/adtinfo.py out/adt/kernel.macho.adt` and `--json`.
3. Check the root identity, `/chosen/dram-base`, `dram-size`, `chip-id`,
   `unique-chip-id`, eight CPUs (`cpu0` `running`, the other seven `waiting`),
   and `/arm-io/intc` + `/arm-io/timer`.
4. Compare the shape against `/data/opencode/ipsw/d20ap.adt` with
   `adtinfo.py --json` (table above).
5. `ctest --output-on-failure` in `build/` — `smoke`, `home`, `img4tool`,
   `adtinfo` all pass.

Observed values in the sample tree:

```
compatible     "D20AP", "iPhone10,1", "AppleARM", "device-tree", "qcom,msm8998"
model          "iPhone10,1"
chosen/dram-base       0x0000000040000000
chosen/dram-size       0x00000000f0000000
chosen/chip-id         0x00008015
chosen/unique-chip-id  0x0123456789abcdef
cpus                   8 × {state running|waiting, cluster-type E|P, timebase 19200000}
arm-io/ranges          0 @ 0x17000000, size 0x01000000
arm-io/intc            arm,gic-v3 @ 0xa00000+0x10000 and 0xb00000+0x100000
arm-io/timer           arm,armv8-timer, 4 PPIs, interrupt-parent = intc
psci/method            "smc"
```

## Unverified and guesswork

This is the blunt list. None of it has been booted on the device yet.

- **`unique-chip-id = 0x0123456789abcdef`** in the sample command is a
  placeholder. Every build must pass a real, unique, nonzero value.
- **Identity values**: `chip-id 0x8015` and `board-id 0x02` are correct for
  the d20ap identity in the 20H392 manifest, but whether XNU 8796 accepts an
  MSM8998 running under that identity is untested.
- **`nvram-proxy-data` / `nvram-bank-*`** are inserted by the converter with
  its own defaults; the size and layout have not been exercised by XNU on this
  board.
- **`osenvironment = "ramdisk"`** is our choice; it has not been tested against
  this kernel's root-mount path.
- **The reserved-memory map is not in the ADT.** It stays in the hardware DTB
  for the loader; XNU does not receive it through `/chosen`. The map itself is
  taken from the Mi 6 Android DTS and has not been checked against the UEFI
  memory map at runtime.
- **`/chosen` zero placeholders** (`boot-manifest-hash`, `firmware-version`,
  `die-id`, …) are shape only. iBoot would fill them on an iPhone; here nothing
  does. Code that validates them will fail, correctly.
- **Interrupt numbering**: the timer's four PPIs are `<1 19 4>`, `<1 14 4>`,
  `<1 11 4>`, `<1 10 4>` (secure physical, non-secure physical, virtual,
  hypervisor). The secure-physical PPI (19) is the one Qualcomm's firmware
  actually uses; the others follow the ARM binding and have not been observed.
- **GIC layout**: distributor `0x17a00000+0x10000`, redistributors
  `0x17b00000+0x100000`, stride `0x20000` are from the MSM8998 memory map but
  the exact redistributor count and affinity routing have not been read back
  from hardware.
- **No `#address-cells`/`#size-cells` in exported children** follows the
  converter's whitelist. If XNU's platform code expects them on `intc`, that is
  a converter change, not a DTS change.
- **Timing**: `timebase-frequency = 19200000` is the documented MSM8998 value;
  it has not been measured against the architected timer.
- **`/memory` size** is the 3.75 GiB window the firmware advertises, not all
  6 GB. The remaining memory is deliberately unused until the loader can pass
  the real UEFI map.
- **AArch64 platform identity**: XNU's arm64 platform expert is written for
  Apple silicon; the ADT shape matching `d20ap` does not prove the kernel will
  accept Qualcomm's GICv3 or PSCI.
