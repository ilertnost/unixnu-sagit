#!/usr/bin/env python3
"""Repack the prelinked kernel so it fits the Mi 6 boot image.

emit_prelink preserves the original kernelcache file layout: every copied
segment keeps its scattered file offset and the source kernel's symbol table
rides along in __LINKEDIT. The Mi 6 bootloader refuses boot images above
64 MiB, and the payload volume that carries this file is the bulk of one.

The kernel runtime and the DarwinBoot loader between them consume only
LC_SEGMENT_64 (vmaddr, vmsize, file offset, file size, init protection), so
this tool rewrites the file with the segments packed 4 KiB-aligned and drops
the symbol-table load commands (LC_SYMTAB, LC_DYSYMTAB, LC_FUNCTION_STARTS,
LC_DATA_IN_CODE, LC_LINKER_OPTIMIZATION_HINT, LC_CODE_SIGNATURE). __LINKEDIT
keeps its virtual extent with a zero file size, like the cache's own empty
__PLK_LINKEDIT. The POST HMAC of the source corecrypto provider covers the
loaded __text only and is unaffected by file layout.

Copyright (c) 2026 ilertnost
SPDX-License-Identifier: GPL-3.0-or-later
"""

import argparse
import struct
import sys
from pathlib import Path

ALIGN = 0x1000
# LC_SYMTAB, LC_DYSYMTAB, LC_FUNCTION_STARTS, LC_DATA_IN_CODE,
# LC_LINKER_OPTIMIZATION_HINT, LC_CODE_SIGNATURE
DROP_COMMANDS = {0x2, 0xB, 0x26, 0x29, 0x22, 0x25}
LC_SEGMENT_64 = 0x19


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='prelinked kernel Mach-O')
    parser.add_argument('output', type=Path, help='repacked kernel Mach-O')
    args = parser.parse_args()

    data = bytearray(args.input.read_bytes())
    magic, cputype, cpusub, filetype, ncmds, sizeofcmds, flags, reserved = \
        struct.unpack_from('<IiiIIIII', data, 0)
    if magic != 0xFEEDFACF:
        sys.exit('not a 64-bit Mach-O')

    offset, keep_commands, dropped = 32, [], 0
    end = 32 + sizeofcmds
    while offset < end:
        command, length = struct.unpack_from('<II', data, offset)
        if command in DROP_COMMANDS:
            dropped += 1
        else:
            keep_commands.append(bytearray(data[offset:offset + length]))
        offset += length

    new_sizeof = sum(len(c) for c in keep_commands)
    position = (32 + new_sizeof + ALIGN - 1) & ~(ALIGN - 1)
    moved = []
    for command in keep_commands:
        if struct.unpack_from('<I', command, 0)[0] != LC_SEGMENT_64:
            continue
        segname = bytes(command[8:24]).rstrip(b'\0').decode()
        old_fileoff, old_filesize = struct.unpack_from('<QQ', command, 40)
        if segname == '__LINKEDIT':
            struct.pack_into('<Q', command, 48, 0)   # FileSize
            struct.pack_into('<Q', command, 40, position)
            continue
        if old_filesize:
            struct.pack_into('<Q', command, 40, position)
            moved.append((old_fileoff, old_filesize, position))
            position = (position + old_filesize + ALIGN - 1) & ~(ALIGN - 1)
        else:
            struct.pack_into('<Q', command, 40, position)

    header = struct.pack('<IiiIIIII', 0xFEEDFACF, cputype, cpusub, filetype,
                         len(keep_commands), new_sizeof, flags, reserved)
    out = bytearray(header)
    for command in keep_commands:
        out += command
    for old_fileoff, old_filesize, new_off in moved:
        if new_off > len(out):
            out += bytes(new_off - len(out))
        out += data[old_fileoff:old_fileoff + old_filesize]

    args.output.write_bytes(bytes(out))
    print(f'{args.output}: {len(out):,} bytes '
          f'({args.input.stat().st_size:,} before, {dropped} commands dropped)')


if __name__ == '__main__':
    main()
