# Building EDK2 for AARCH64

This is the bootstrap of the boot chain from ADR-0003. It is deliberately
about a *generic* AARCH64 firmware, not about Mi 6 yet: the point is to prove
that the toolchain, the tree, and the entry path work on this machine before
any Qualcomm-specific code exists.

## Versions used

| component | version |
|---|---|
| edk2 | `edk2-stable202608` |
| compiler | clang 23.1.4 + lld |
| toolchain tag | `CLANGDWARF` |
| iasl | 20251212 (`acpica`) |
| QEMU | 11.1.2 (`qemu-system-aarch64`) |

No AArch64 GCC is required. `CLANGDWARF` compiles with `--target=aarch64-*`
and links with `ld.lld`.

## Checkout

```sh
git clone --depth 1 --branch edk2-stable202608 \
  --recurse-submodules --shallow-submodules \
  https://github.com/tianocore/edk2.git
```

`--shallow-submodules` is **not reliable**: several submodules come back with
an empty worktree, or fail outright when the network drops. The ones that
actually matter for an AARCH64 build, and which had to be re-fetched by hand:

```
BaseTools/Source/C/BrotliCompress/brotli
MdeModulePkg/Library/BrotliCustomDecompressLib/brotli
MdePkg/Library/BaseFdtLib/libfdt
MdePkg/Library/MipiSysTLib/mipisyst
SecurityPkg/DeviceSecurity/SpdmLib/libspdm
MdeModulePkg/Universal/RegularExpressionDxe/oniguruma
CryptoPkg/Library/OpensslLib/openssl
CryptoPkg/Library/MbedTlsLib/mbedtls
```

If a submodule directory exists but is empty (only a `.git` file), `git
submodule status` still reports it as present. Check for the actual files, not
the status line.

Stale shallow locks are common after a failed fetch:

```sh
find .git/modules -name '*.lock' -delete
```

## Build

```sh
cd edk2
make -C BaseTools -j"$(nproc)"

export WORKSPACE=$PWD
export EDK_TOOLS_PATH=$PWD/BaseTools
export CONF_PATH=$PWD/Conf
export PATH=$PWD/BaseTools/Source/C/bin:$PATH
source edksetup.sh

build -a AARCH64 -t CLANGDWARF -b DEBUG \
      -p ArmVirtPkg/ArmVirtQemu.dsc -n "$(nproc)"
```

## Result

```
FV/QEMU_EFI.fd     3 145 728 bytes
286 EFI modules
FVMAIN_COMPACT     48% used (1.5 MB of 3.1 MB)
```

## Verification

Building is not proof. The image was booted under QEMU `virt` and reaches the
Boot Manager:

```sh
qemu-system-aarch64 -M virt -cpu cortex-a57 -m 1024 \
  -bios FV/QEMU_EFI.fd -nographic -no-reboot
```

Observed, in order: `Sec` -> `PeiCore` -> memory init over
`0x40000000-0x7FFFFFFF` -> `DXE CORE` -> driver dispatch (`FdtClientDxe`,
`ArmCpuDxe`, `SerialDxe`, ...) -> `BdsDxe` -> boot manager, with no assertion
and no exception.

The final `No bootable option or device was found` is correct: there is no
disk attached. It means the firmware finished its job.

## Why this shape

`ArmVirtQemu` uses `ArmVirtPrePiUniCoreRelocatable`: a PrePi module entered by
PE entry point with `x0` pointing at a device tree. That is exactly the handoff
ABL gives us on the phone (AArch64 Linux boot protocol, `x0` = DTB). Starting
from a platform that already consumes a DTB means the Mi 6 port replaces the
platform description and the drivers, not the architecture.

## What this does not do

- It does not run on the phone. `QEMU_EFI.fd` targets QEMU `virt`.
- It has no UFS, no PM8998, no TLMM, no display.
- It is not packaged as an Android boot image, which is what the `boot`
  partition on this device expects.

Those are the next steps, in that order.
