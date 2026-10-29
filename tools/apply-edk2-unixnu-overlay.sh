#!/usr/bin/env bash
#
# apply-edk2-unixnu-overlay.sh
#
# Minimal backport of the MdePkg FDT support (and the one ArmPkg assembly
# macro header) that the UniXNU modules need, from Microsoft's Mu basecore
# (release/202608) into the pinned January-2023 EDK2 core used by the
# edk2-msm8998 sagit port.
#
# The overlay is deliberately small and auditable:
#
#   Common/edk2/MdePkg/Include/Guid/Fdt.h              (new)
#   Common/edk2/MdePkg/Include/Library/FdtLib.h         (new)
#   Common/edk2/MdePkg/Library/BaseFdtLib/**            (new, incl. libfdt)
#   Common/edk2/ArmPkg/Include/AsmMacroLib.h            (new, see note)
#   Common/edk2/MdePkg/MdePkg.dec                       (patched: gFdtTableGuid,
#                                                        FdtLib library class)
#   Platforms/Msm8998Pkg/Device/xiaomi/sagit/sagit.dsc  (patched: ArmPkg include
#                                                        visibility + per-module
#                                                        FdtLib overrides)
#
# Nothing else in the pinned core is touched.  The vendored UniXNU/DarwinBoot
# packages are read-only inputs and are never modified.
#
# Note on AsmMacroLib.h: upstream Mu keeps the file at
# MdePkg/Include/AArch64/AsmMacroLib.h.  It is installed here under
# ArmPkg/Include (as required by the platform wiring) so that DarwinBoot's
# Entry.S can include <AsmMacroLib.h> once ArmPkg.dec is visible to it.
# Mu's header relies on _ASM_TYPE/AARCH64_BTI, which Mu defines in its
# ProcessorBind.h but the pinned 2023 core does not have, so the header gets
# a clearly marked fallback block at the end (no-op on a newer core).
#
# Usage:
#   ROOT=/data/opencode/edk2-msm8998 \
#   MU_BASECORE=/data/opencode/unixnu-src/out/feeds/mu_basecore-89034cbb \
#     tools/apply-edk2-unixnu-overlay.sh
#
# The script is idempotent: running it twice produces no further changes.

set -euo pipefail

ROOT="${ROOT:-/data/opencode/edk2-msm8998}"
MU_BASECORE="${MU_BASECORE:-/data/opencode/unixnu-src/out/feeds/mu_basecore-89034cbb}"

EDK2="$ROOT/Common/edk2"
DSC="$ROOT/Platforms/Msm8998Pkg/Device/xiaomi/sagit/sagit.dsc"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ -d "$EDK2/MdePkg" ] || die "no MdePkg under $EDK2 (ROOT=$ROOT)"
[ -d "$EDK2/ArmPkg" ] || die "no ArmPkg under $EDK2 (ROOT=$ROOT)"
[ -f "$DSC" ] || die "device DSC not found: $DSC"
[ -d "$MU_BASECORE/MdePkg" ] || die "no MdePkg under $MU_BASECORE"

# Every source path is checked up front so a missing item fails before any
# file has been written.
for src in \
  MdePkg/Include/Guid/Fdt.h \
  MdePkg/Include/Library/FdtLib.h \
  MdePkg/Library/BaseFdtLib/BaseFdtLib.inf \
  MdePkg/Library/BaseFdtLib/libfdt/libfdt/libfdt.h \
  MdePkg/Include/AArch64/AsmMacroLib.h
do
  [ -e "$MU_BASECORE/$src" ] || die "missing in Mu checkout: $src"
done

ADDED=0
UPDATED=0
UNCHANGED=0

copy_one() {
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    UNCHANGED=$((UNCHANGED + 1))
  elif [ -e "$dst" ]; then
    cp -p "$src" "$dst"
    UPDATED=$((UPDATED + 1))
    printf 'updated  %s\n' "${dst#"$ROOT"/}"
  else
    cp -p "$src" "$dst"
    ADDED=$((ADDED + 1))
    printf 'added    %s\n' "${dst#"$ROOT"/}"
  fi
}

copy_tree() {
  local src="$1" dst="$2" f rel
  while IFS= read -r -d '' f; do
    rel="${f#"$src"/}"
    copy_one "$f" "$dst/$rel"
  done < <(find "$src" -type f ! -path '*/.git/*' ! -name '.git' -print0)
}

printf '== overlay: %s\n' "$ROOT"
printf '== source : %s\n' "$MU_BASECORE"

# ---------------------------------------------------------------------------
# 1. MdePkg FDT headers and BaseFdtLib (including the libfdt submodule)
# ---------------------------------------------------------------------------
copy_one "$MU_BASECORE/MdePkg/Include/Guid/Fdt.h"       "$EDK2/MdePkg/Include/Guid/Fdt.h"
copy_one "$MU_BASECORE/MdePkg/Include/Library/FdtLib.h" "$EDK2/MdePkg/Include/Library/FdtLib.h"
copy_tree "$MU_BASECORE/MdePkg/Library/BaseFdtLib"      "$EDK2/MdePkg/Library/BaseFdtLib"

# ---------------------------------------------------------------------------
# 2. ArmPkg assembly macros (DarwinBoot/Entry.S: #include <AsmMacroLib.h>)
# ---------------------------------------------------------------------------
ASM_MACRO="$EDK2/ArmPkg/Include/AsmMacroLib.h"
TMPDIR="${TMPDIR:-/data/opencode/tmp}"
mkdir -p "$TMPDIR"
ASM_TMP="$(mktemp "$TMPDIR/asm-macro.XXXXXX")"
trap 'rm -f "$ASM_TMP"' EXIT
cp -p "$MU_BASECORE/MdePkg/Include/AArch64/AsmMacroLib.h" "$ASM_TMP"
cat >> "$ASM_TMP" <<'EOF'

//
// EDK2-JAN2023-COMPAT
//
// Mu defines _ASM_TYPE and AARCH64_BTI in MdePkg/Include/AArch64/
// ProcessorBind.h.  The pinned January-2023 EDK2 core predates both macros,
// so provide the same no-BTI fallbacks here.  On a core that already defines
// them (or a toolchain with branch protection) these guards are no-ops.
//
#ifndef _ASM_TYPE
  #ifdef __ELF__
#define _ASM_TYPE(Name)  .type Name, %function
  #else
#define _ASM_TYPE(Name)
  #endif
#endif

#ifndef AARCH64_BTI
#define AARCH64_BTI_NOTE()
#define AARCH64_BTI(__type)
#endif
EOF
copy_one "$ASM_TMP" "$ASM_MACRO"

# ---------------------------------------------------------------------------
# 3. MdePkg.dec: gFdtTableGuid in [Guids], FdtLib in [LibraryClasses]
# ---------------------------------------------------------------------------
python3 - "$EDK2/MdePkg/MdePkg.dec" <<'PY'
import re
import sys

path = sys.argv[1]
with open(path, encoding='utf-8', newline='') as f:
    lines = f.readlines()

eol = '\r\n' if any(l.endswith('\r\n') for l in lines) else '\n'


def section(name):
    for i, line in enumerate(lines):
        if line.strip() == name:
            return i
    return -1


def section_end(start):
    for j in range(start + 1, len(lines)):
        s = lines[j].strip()
        if s.startswith('[') and s.endswith(']'):
            return j
    return len(lines)


changed = []

# ---- [LibraryClasses] -----------------------------------------------------
start = section('[LibraryClasses]')
if start < 0:
    sys.exit('%s: no [LibraryClasses] section' % path)
end = section_end(start)
if not any(re.match(r'^\s*FdtLib\s*\|', l) for l in lines[start:end]):
    lines[end:end] = [
        eol,
        '  ##  @libraryclass  Provides APIs for third-party library libfdt.' + eol,
        '  #' + eol,
        '  FdtLib|Include/Library/FdtLib.h' + eol,
        eol,
    ]
    changed.append('FdtLib library class')

# ---- [Guids] --------------------------------------------------------------
start = section('[Guids]')
if start < 0:
    sys.exit('%s: no [Guids] section' % path)
end = section_end(start)
if not any(re.match(r'^\s*gFdtTableGuid\s*=', l) for l in lines[start:end]):
    lines[end:end] = [
        eol,
        '  ## FDT Configuration Table' + eol,
        '  # Include/Guid/Fdt.h' + eol,
        '  gFdtTableGuid = { 0xb1b621d5, 0xf19c, 0x41a5, { 0x83, 0x0b, 0xd9, 0x15, 0x2c, 0x69, 0xaa, 0xe0 } }' + eol,
        eol,
    ]
    changed.append('gFdtTableGuid')

if changed:
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.writelines(lines)
    print('patched  MdePkg/MdePkg.dec (%s)' % ', '.join(changed))
else:
    print('unchanged MdePkg/MdePkg.dec')
PY

# ---------------------------------------------------------------------------
# 4. sagit.dsc wiring
#    - ArmPkg.dec in [Packages] so DarwinBoot sees ArmPkg/Include without
#      editing the vendored DarwinBoot.inf.
#    - per-module FdtLib override for the two build units that use the new
#      MdePkg API: UniXNURamDiskDxe and the UniXNU application (which is the
#      module that compiles/links UniXNULib).  The platform-wide mapping keeps
#      EmbeddedPkg's lowercase FdtLib for every other module.
# ---------------------------------------------------------------------------
python3 - "$DSC" <<'PY'
import re
import sys

path = sys.argv[1]
with open(path, encoding='utf-8', newline='') as f:
    text = f.read()

changed = []

if not re.search(r'^\s*ArmPkg/ArmPkg\.dec\s*$', text, re.M):
    anchor = '!include Platforms/Msm8998Pkg/Msm8998.dsc\n'
    if anchor not in text:
        sys.exit('%s: include anchor not found' % path)
    block = (
        '\n'
        '#\n'
        '# DarwinBoot.inf is vendored read-only and its Entry.S includes\n'
        '# <AsmMacroLib.h> from ArmPkg/Include.  Give the platform ArmPkg\n'
        '# include visibility here instead of editing the package.\n'
        '#\n'
        '[Packages]\n'
        '  ArmPkg/ArmPkg.dec\n'
    )
    text = text.replace(anchor, anchor + block, 1)
    changed.append('[Packages] ArmPkg')

MODULES = '''
#
# UniXNU boot manager + DarwinBoot XNU loader.
#
# Both packages live outside this tree (unixnu-src) and are resolved through
# PACKAGES_PATH, see tools/build-sagit-edk2.sh in unixnu-sagit.
#
[LibraryClasses.common]
  UniXNULib|UniXNUPkg/Library/UniXNULib/UniXNULib.inf

[Components.common]
  UniXNUPkg/Driver/UniXNUDxe/UniXNUDxe.inf
  # UniXNURamDiskDxe is not used: it reads the initrd bounds from a Mu-Silicium
  # SEC handoff HOB that this EDK2 port does not create. SagitRamDiskDxe
  # publishes the same ramdisk from the device tree instead.
  SagitPkg/Drivers/RamDiskDxe/RamDiskDxe.inf
  UniXNUPkg/Application/UniXNU/UniXNU.inf {
    <LibraryClasses>
      #
      # UniXNULib is linked into this application and uses the MdePkg API.
      # The platform default FdtLib is EmbeddedPkg's lowercase libfdt;
      # this build unit uses the MdePkg/BaseFdtLib API.
      #
      FdtLib|MdePkg/Library/BaseFdtLib/BaseFdtLib.inf
  }
  DarwinBootPkg/Driver/DarwinBoot.inf
'''

if 'SagitPkg/Drivers/ReportDxe' not in text:
    anchor2 = '  SagitPkg/Drivers/RamDiskDxe/RamDiskDxe.inf\n'
    if anchor2 not in text:
        sys.exit('%s: RamDiskDxe anchor not found' % path)
    text = text.replace(
        anchor2,
        anchor2 + '  SagitPkg/Drivers/ReportDxe/ReportDxe.inf\n',
        1,
    )
    changed.append('SagitReportDxe')

if 'UniXNUPkg/Driver/UniXNUDxe' not in text:
    if not text.endswith('\n'):
        text += '\n'
    text += MODULES
    changed.append('UniXNULib + the four UniXNU/DarwinBoot modules')

if changed:
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    print('patched  Platforms/Msm8998Pkg/Device/xiaomi/sagit/sagit.dsc (%s)'
          % ', '.join(changed))
else:
    print('unchanged Platforms/Msm8998Pkg/Device/xiaomi/sagit/sagit.dsc')
PY

# ---------------------------------------------------------------------------
# 5. sagit.fdf: put the four modules into the main firmware volume
# ---------------------------------------------------------------------------
python3 - "$ROOT/Platforms/Msm8998Pkg/Device/xiaomi/sagit/sagit.fdf" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding='utf-8', newline='') as f:
    text = f.read()

if 'UniXNUPkg/Driver/UniXNUDxe' in text:
    print('unchanged sagit.fdf')
    sys.exit(0)

anchor = '  # INF src/kernelfdt/KernelFdtDxe.inf\n'
if anchor not in text:
    sys.exit('%s: FDF anchor not found' % path)

block = (
    '\n'
    '  #\n'
    '  # UniXNU boot manager and DarwinBoot XNU loader. UniXNUDxe registers the\n'
    '  # UniXNU application (same FV) as the first Boot#### option; DarwinBoot\n'
    '  # produces gUniXNULoaderProtocolGuid; UniXNURamDiskDxe publishes a ramdisk\n'
    '  # handed over in the FDT.\n'
    '  #\n'
    '  INF UniXNUPkg/Driver/UniXNUDxe/UniXNUDxe.inf\n'
    '  INF SagitPkg/Drivers/RamDiskDxe/RamDiskDxe.inf\n'
    '  INF SagitPkg/Drivers/ReportDxe/ReportDxe.inf\n'
    '  INF UniXNUPkg/Application/UniXNU/UniXNU.inf\n'
    '  INF DarwinBootPkg/Driver/DarwinBoot.inf\n'
)

text = text.replace(anchor, anchor + block, 1)
with open(path, 'w', encoding='utf-8', newline='') as f:
    f.write(text)
print('patched  Platforms/Msm8998Pkg/Device/xiaomi/sagit/sagit.fdf')
PY

printf '== done: %d added, %d updated, %d unchanged\n' "$ADDED" "$UPDATED" "$UNCHANGED"
