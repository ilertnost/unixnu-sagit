#!/usr/bin/env python3
#
# Copyright (c) 2026 ilertnost
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Tests for the LZMA payload container in tools/mksagit-payload.py.

The firmware's decoder reads a five-byte LZMA property block, then one
eight-byte uncompressed size, then the range-coded data. Python's alone
format writes an unknown size (ff..ff) when the size is not known up front,
so the packer replaces it. Keeping the original field after the replacement
shifts the range coder by eight bytes and the decoder rejects the stream.
"""

import importlib.util
import lzma
import pathlib
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "mksagit_payload", ROOT / "tools" / "mksagit-payload.py")
payload = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = payload
SPEC.loader.exec_module(payload)

LZMA_PAYLOAD_HEADER_SIZE = 72


class PayloadStreamTest(unittest.TestCase):

    def test_stream_has_exactly_one_size_field(self):
        raw = bytes(range(256)) * 4096
        with tempfile.TemporaryDirectory() as scratch:
            source = pathlib.Path(scratch) / "volume.img"
            output = pathlib.Path(scratch) / "payload.img"
            source.write_bytes(raw)

            payload.compress_payload(source, output)
            blob = output.read_bytes()

            magic, version, header_size, stored, expanded, stream_crc, raw_crc = \
                struct.unpack_from("<QIIQQII", blob, 0)
            self.assertEqual(magic, int.from_bytes(b"UNXLZMA1", "little"))
            self.assertEqual(version, 1)
            self.assertEqual(header_size, LZMA_PAYLOAD_HEADER_SIZE)
            self.assertEqual(expanded, len(raw))
            self.assertEqual(len(blob) % 2048, 0)

            stream = blob[header_size:header_size + stored]
            self.assertEqual(len(stream), stored)
            self.assertEqual(zlib.crc32(stream) & 0xffffffff, stream_crc)
            self.assertEqual(zlib.crc32(raw) & 0xffffffff, raw_crc)

            # The range coder starts immediately after props + one size field
            # and always starts with a zero byte.
            self.assertEqual(stream[13], 0)
            decoded = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(stream)
            self.assertEqual(decoded, raw)


if __name__ == "__main__":
    unittest.main()
