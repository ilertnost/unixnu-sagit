#!/usr/bin/env python3
#
# Build the UniXNU payload volume: a small FAT filesystem with the boot
# configuration, the kernel and its ADT. The firmware ships it inside the
# Android boot image as the ramdisk, ABL copies it to RAM, and
# SagitRamDiskDxe publishes it as a block device.
#
# Copyright (c) 2026 ilertnost
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Create the payload FAT volume that UniXNU reads at boot.

The layout is fixed by UniXNU (docs/concepts/payload.md in unixnu-src):

    /EFI/UniXNU/unixnu.toml
    /EFI/UniXNU/Payloads/kernel.macho
    /EFI/UniXNU/Payloads/kernel.macho.adt

Timestamps are pinned so the same inputs produce the same bytes. The ADT is
expected to carry its own random seed, so a rebuild after regenerating it does
differ -- that is the ADT's business, not the volume's.
"""

import argparse
import hashlib
import json
import lzma
import struct
import zlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EPOCH = 315532800            # 1980-01-01T00:00:00Z, the FAT epoch
VOLUME_LABEL = 'ADRENOLINE'


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit('%s failed' % command[0])
    return result.stdout


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


LZMA_PAYLOAD_MAGIC = b'UNXLZMA1'
# Mirrors the firmware's LZMA_PAYLOAD_HEADER: magic inside, packed to 72 bytes.
LZMA_PAYLOAD_HEADER = struct.Struct('<QIIQQII32x')


def compress_payload(volume_path, output_path):
    """Wrap the FAT volume in a self-describing LZMA-alone stream.

    The Mi 6 bootloader refuses boot images whose ramdisk section exceeds
    roughly 36 MiB, so the volume travels compressed and UniXNURamDiskDxe
    expands it before publishing the block device. The LZMA-alone container
    carries the exact decoded size, which is what EDK2's decoder requires.
    """
    raw = volume_path.read_bytes()
    encoded = bytearray(lzma.compress(raw, format=lzma.FORMAT_ALONE,
                                      filters=[{'id': lzma.FILTER_LZMA1,
                                                'dict_size': 8 << 20,
                                                'lc': 3, 'lp': 0, 'pb': 2,
                                                'mode': lzma.MODE_NORMAL,
                                                'nice_len': 64, 'mf': lzma.MF_BT4}]))
    # Normalize the stream for EDK2's decoder: some Python builds prefix the
    # alone header with padding and write an unknown-size field (ff..ff),
    # while LzmaUefiDecompressGetInfo requires the exact decoded size.
    # The alone size field is eight bytes right after the five-byte props;
    # keep the props, replace that field, and drop the original one. Leaving
    # it in shifts the range-coded data by eight bytes and LzmaDecode rejects
    # the stream as corrupt.
    props = bytes(encoded).find(b'\x5d\x00\x00\x80\x00')
    if props < 0:
        raise SystemExit('LZMA-alone header not found in the encoded stream')
    stream = (bytes(encoded[props:props + 5]) + struct.pack('<Q', len(raw)) +
              bytes(encoded[props + 13:]))
    header = LZMA_PAYLOAD_HEADER.pack(
        int.from_bytes(LZMA_PAYLOAD_MAGIC, 'little'),
        1, LZMA_PAYLOAD_HEADER.size, len(stream), len(raw),
        zlib.crc32(stream) & 0xffffffff, zlib.crc32(raw) & 0xffffffff)
    blob = header + stream
    # The bootloader hands the ramdisk over by the header-declared size and
    # the firmware sanity-checks nothing about it, but a whole-file sector
    # multiple keeps every consumer honest.
    blob += bytes(-len(blob) % 2048)
    output_path.write_bytes(blob)
    return output_path


def configuration(title, kernel_args, has_adt, timeout=15):
    lines = [
        'version = 1',
        '',
        '[boot]',
        'timeout = %u' % timeout,
        'default = "xnu"',
        '',
        '[kits]',
        'autoload = false',
        '',
        '[[entry]]',
        'id = "xnu"',
        'title = "%s"' % title,
        'type = "xnu"',
        'path = "Payloads/kernel.macho"',
        'args = "%s"' % kernel_args,
    ]
    if not has_adt:
        raise SystemExit('an xnu entry without kernel.macho.adt cannot boot')
    # Restart and power off, as the built-in menu carries them.
    lines += [
        '',
        '[[entry]]',
        'id = "reset"',
        'title = "Restart"',
        'type = "action"',
        'action = "reset"',
        '',
        '[[entry]]',
        'id = "shutdown"',
        'title = "Power off"',
        'type = "action"',
        'action = "shutdown"',
    ]
    return '\n'.join(lines) + '\n'


def build(kernel, adt, output, size_mib, title, kernel_args, timeout=15):
    for path, what in ((kernel, 'kernel'), (adt, 'ADT')):
        if not path.is_file():
            raise SystemExit('%s not found: %s' % (what, path))

    with tempfile.TemporaryDirectory(prefix='sagit-payload-', dir=os.environ.get('TMPDIR')) as scratch_name:
        scratch = Path(scratch_name)
        stage = scratch / 'stage'
        payloads = stage / 'EFI/UniXNU/Payloads'
        payloads.mkdir(parents=True)

        shutil.copyfile(kernel, payloads / 'kernel.macho')
        shutil.copyfile(adt, payloads / 'kernel.macho.adt')
        (stage / 'EFI/UniXNU/unixnu.toml').write_text(configuration(title, kernel_args, True, timeout))

        for path in [stage, *sorted(stage.rglob('*'))]:
            os.utime(path, (EPOCH, EPOCH))

        content = sum(path.stat().st_size for path in stage.rglob('*') if path.is_file())
        limit = size_mib * 1024 * 1024
        if content > limit * 0.9:
            raise SystemExit(
                'the payload needs %d MiB plus FAT metadata; --size-mib is %d'
                % (content >> 20, size_mib)
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()

        # mtools' mformat in this generation has no size switch; dosfstools
        # creates the file and the filesystem in one step.
        run(['mkfs.fat', '-C', '-F', '16', '-n', VOLUME_LABEL, '-i', '0xad123456',
             str(output), str(size_mib * 1024)])
        env = dict(os.environ, MTOOLS_SKIP_CHECK='1', TZ='UTC')
        run(['mcopy', '-i', str(output), '-m', '-s', str(stage / 'EFI'), '::'], env=env)

        listing = run(['mdir', '-i', str(output), '-/b', '::'], env=env)

    report = {
        'payload_bytes': output.stat().st_size,
        'payload_sha256': sha256(output),
        'kernel_bytes': kernel.stat().st_size,
        'kernel_sha256': sha256(kernel),
        'adt_bytes': adt.stat().st_size,
        'adt_sha256': sha256(adt),
        'kernel_args': kernel_args,
        'files': sorted(line for line in listing.splitlines() if line.strip()),
    }
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description='Build the UniXNU payload volume for sagit.')
    parser.add_argument('--kernel', type=Path, required=True, help='XNU Mach-O to boot')
    parser.add_argument('--adt', type=Path, required=True, help='ADT for that kernel')
    parser.add_argument('--output', type=Path, required=True, help='FAT volume to write')
    parser.add_argument('--size-mib', type=int, default=24, help='volume size in MiB (default 24)')
    parser.add_argument('--title', default='Adrenoline', help='menu entry title')
    parser.add_argument('--args', default='-v', help='XNU boot arguments')
    parser.add_argument('--timeout', type=int, default=15, help='menu timeout in seconds (default 15)')
    parser.add_argument('--lzma', action='store_true',
                        help='wrap the volume in an LZMA-alone stream for the firmware to expand')
    arguments = parser.parse_args()

    report = build(arguments.kernel, arguments.adt, arguments.output,
                   arguments.size_mib, arguments.title, arguments.args,
                   arguments.timeout)
    if arguments.lzma:
        compressed = arguments.output.with_suffix('.img.lzma')
        compress_payload(arguments.output, compressed)
        report['lzma_bytes'] = compressed.stat().st_size
        report['lzma_sha256'] = sha256(compressed)
        print('lzma payload: %s (%d bytes, %d%% of the volume)'
              % (compressed, report['lzma_bytes'],
                 report['lzma_bytes'] * 100 // max(report['payload_bytes'], 1)))
    print('payload: %s (%d bytes, %d files)'
          % (arguments.output, report['payload_bytes'], len(report['files'])))
    for line in report['files']:
        print('  %s' % line)


if __name__ == '__main__':
    main()
