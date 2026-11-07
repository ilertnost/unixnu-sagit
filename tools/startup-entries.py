#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""List the kernel startup hook table in execution order.

`kernel_startup_bootstrap()` sorts `__BOOTDATA,__init_entry_set` by
(subsystem, rank) and every later `kernel_startup_initialize_upto()` walks the
sorted table. The bring-up ladder paints the ordinal of the entry it is about
to call, restarted at zero for each call, so this tool turns that number back
into a symbol: run it on the same Mach-O that was flashed and pass the
subsystem the call was made for.

Entries with equal subsystem and rank are ties: the kernel sorts them with an
unstable qsort, so their relative order here is the file order and may not be
what the kernel picked.

SPDX-License-Identifier: MIT
"""

import argparse
import struct
import sys

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2

MH_MAGIC_64 = 0xFEEDFACF

SUBSYSTEMS = [
    "none",
    "tunables",
    "timeouts",
    "locks",
    "kprintf",
    "pmap_steal",
    "kmem",
    "zalloc",
    "ktrace",
    "percpu",
    "event",
    "codesigning",
    "oslog",
    "mach_ipc",
    "thread_call",
    "sysctl",
    "early_boot",
    "lockdown",
]


def macho_sections(blob):
    """Return (sections, symbols) of a thin 64-bit little-endian Mach-O."""
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, _ = \
        struct.unpack_from("<IiiIIIII", blob, 0)
    if magic != MH_MAGIC_64:
        raise SystemExit("not a thin 64-bit Mach-O (magic 0x%08x)" % magic)
    if filetype != 2:
        raise SystemExit("not MH_EXECUTE (filetype %d)" % filetype)

    sections = {}
    symbols = []
    offset = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", blob, offset)
        if cmd == LC_SEGMENT_64:
            nsects = struct.unpack_from("<I", blob, offset + 64)[0]
            sect_off = offset + 72
            for _ in range(nsects):
                (sectname, seg, addr, size, file_offset, align, reloff,
                 nreloc, sect_flags, r1, r2, r3) = struct.unpack_from(
                     "<16s16sQQIIIIIIII", blob, sect_off)
                name = (seg.rstrip(b"\0").decode() + "," +
                        sectname.rstrip(b"\0").decode())
                sections[name] = (addr, size, file_offset)
                sect_off += 80
        elif cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", blob,
                                                               offset + 8)
            for i in range(nsyms):
                n_strx, n_type, n_sect, n_desc, n_value = struct.unpack_from(
                    "<IBBHQ", blob, symoff + i * 16)
                if n_type & 0xE0 != 0 or n_value == 0:
                    continue
                end = blob.index(b"\0", stroff + n_strx)
                symbols.append((n_value,
                                blob[stroff + n_strx:end].decode("utf-8",
                                                                 "replace")))
        offset += cmdsize
    return sections, symbols


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kernel", help="Mach-O kernel (unstripped, so names resolve)")
    ap.add_argument("--subsystem", type=int, default=None, metavar="ID",
                    help="only entries of this subsystem, numbered from zero")
    ap.add_argument("--section", default="__BOOTDATA,__init_entry_set",
                    help="section holding the hooks (default %(default)s)")
    args = ap.parse_args()

    blob = open(args.kernel, "rb").read()
    sections, symbols = macho_sections(blob)
    if args.section not in sections:
        have = ", ".join(sorted(sections))
        raise SystemExit("no %s section; have: %s" % (args.section, have))

    addr, size, file_offset = sections[args.section]
    if size % 24:
        raise SystemExit("section size %d is not a multiple of 24" % size)

    by_addr = {}
    for value, name in symbols:
        by_addr.setdefault(value, name)

    entries = []
    for i in range(size // 24):
        subsystem, rank, func, arg = struct.unpack_from(
            "<IIQQ", blob, file_offset + i * 24)
        entries.append((subsystem, rank, func, arg))

    # Same key as startup_entry_cmp(); file order breaks ties.
    entries.sort(key=lambda e: (e[0], e[1]))

    ordinal = 0
    for subsystem, rank, func, arg in entries:
        if args.subsystem is not None and subsystem != args.subsystem:
            continue
        name = by_addr.get(func, "<%#x>" % func)
        sub = SUBSYSTEMS[subsystem] if subsystem < len(SUBSYSTEMS) else \
            str(subsystem)
        print("%3d  %-12s rank %-3d  %s" % (ordinal, sub, rank, name))
        ordinal += 1


if __name__ == "__main__":
    sys.exit(main())
