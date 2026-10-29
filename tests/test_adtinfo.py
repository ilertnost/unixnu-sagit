#!/usr/bin/env python3
#
# Copyright (c) 2026 ilertnost
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Tests for tools/adtinfo.py.

Apple's shipped ADT is not committed here, so the fixtures are synthetic: they
build the exact wire format documented in adtinfo.py (and in
docs/ADT-SAGIT.md) by hand. One optional test reads the real d20ap.adt when a
caller has extracted it.
"""

import importlib.util
import json
import pathlib
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("adtinfo", ROOT / "tools" / "adtinfo.py")
adtinfo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adtinfo
SPEC.loader.exec_module(adtinfo)


# --------------------------------------------------------------------------
# Fixture builders

def adt_property(name: str, value: bytes, reference: bool = False) -> bytes:
    encoded = name.encode("ascii")
    if len(encoded) >= 32:
        raise ValueError("a property name is at most 31 bytes")
    length = len(value) | (adtinfo.ADT_REFERENCE if reference else 0)
    return (encoded.ljust(32, b"\0")
            + struct.pack("<I", length)
            + value
            + b"\0" * (-len(value) % 4))


def adt_node(name: str | None, properties=(), children=()) -> bytes:
    """An unnamed root is allowed; every other node carries its name property."""
    own = ([] if name is None else [("name", name.encode("ascii") + b"\0")]) + list(properties)
    out = struct.pack("<II", len(own), len(children))
    for item in own:
        out += adt_property(item[0], item[1], item[2] if len(item) > 2 else False)
    for child in children:
        out += child
    return out


def fdt_blob(root_name: str, properties, children=()) -> bytes:
    """A version 17 FDT with one root node and optional child nodes.

    properties is a list of (name, value); children a list of the same shape
    built by fdt_node().
    """
    strings = b""
    offsets = {}

    def intern(name: str) -> int:
        nonlocal strings
        if name not in offsets:
            offsets[name] = len(strings)
            strings += name.encode("ascii") + b"\0"
        return offsets[name]

    body = b""

    def node(name: str, props) -> bytes:
        out = struct.pack(">I", 1) + name.encode("ascii") + b"\0"
        out += b"\0" * (-(len(name) + 1) % 4)
        for prop_name, value in props:
            out += struct.pack(">III", 3, len(value), intern(prop_name))
            out += value + b"\0" * (-len(value) % 4)
        return out

    body += node(root_name, properties)
    for name, props in children:
        body += node(name, props) + struct.pack(">I", 2)
    body += struct.pack(">I", 2) + struct.pack(">I", 9)

    header_size = 40
    rsvmap = struct.pack(">QQ", 0, 0)
    off_rsvmap = header_size
    off_struct = off_rsvmap + len(rsvmap)
    off_strings = off_struct + len(body)
    total = off_strings + len(strings)
    header = struct.pack(">10I", adtinfo.FDT_MAGIC, total, off_struct, off_strings,
                         off_rsvmap, 17, 16, 0, len(strings), len(body))
    return header + rsvmap + body + strings


# --------------------------------------------------------------------------
# Apple device tree

class AdtTests(unittest.TestCase):
    def test_reads_nested_nodes_and_their_names(self):
        root = adtinfo.read_adt(adt_node(
            "device-tree",
            [("model", b"iPhone10,1\0")],
            [adt_node("chosen", [("chip-id", struct.pack("<I", 0x8015))]),
             adt_node("cpus", children=[
                 adt_node("cpu0", [("state", b"running\0")]),
                 adt_node("cpu1", [("state", b"waiting\0")]),
             ])],
        ))

        self.assertEqual(root.name, "device-tree")
        self.assertEqual([child.name for child in root.children],
                         ["chosen", "cpus"])
        self.assertEqual(root.children[0].get("chip-id").value,
                         struct.pack("<I", 0x8015))
        self.assertEqual([child.name for child in root.children[1].children],
                         ["cpu0", "cpu1"])
        self.assertEqual(len(list(root.walk())), 5)

    def test_reads_a_root_without_a_name_property(self):
        root = adtinfo.read_adt(adt_node(None, [("model", b"x\0")]))
        self.assertEqual(root.name, "")
        self.assertEqual(root.get("model").value, b"x\0")

    def test_keeps_the_syscfg_reference_flag(self):
        blob = adt_node("device-tree", [("serial-number", b"syscfg/SrNm/0x20", True)])
        prop = adtinfo.read_adt(blob).get("serial-number")
        self.assertTrue(prop.reference)
        self.assertEqual(prop.value, b"syscfg/SrNm/0x20")

    def test_skips_four_byte_value_padding(self):
        # A three-byte value occupies four bytes on disk, then the next
        # property starts. Reading the wrong offset would eat the name.
        blob = adt_node("device-tree", [("a", b"abc"), ("b", b"z")])
        root = adtinfo.read_adt(blob)
        self.assertEqual(root.get("a").value, b"abc")
        self.assertEqual(root.get("b").value, b"z")

    def test_rejects_trailing_bytes(self):
        with self.assertRaises(adtinfo.AdtError):
            adtinfo.read_adt(adt_node("device-tree") + b"\0\0\0\0")

    def test_rejects_a_truncated_property_name(self):
        blob = adt_node("device-tree")[:-8]
        with self.assertRaises(adtinfo.AdtError):
            adtinfo.read_adt(blob)

    def test_rejects_an_unterminated_property_name(self):
        blob = struct.pack("<II", 1, 0) + b"x" * 32 + struct.pack("<I", 0)
        with self.assertRaises(adtinfo.AdtError):
            adtinfo.read_adt(blob)

    def test_rejects_an_empty_property_name(self):
        blob = struct.pack("<II", 1, 0) + bytes(32) + struct.pack("<I", 0)
        with self.assertRaises(adtinfo.AdtError):
            adtinfo.read_adt(blob)

    def test_rejects_a_value_longer_than_the_input(self):
        blob = struct.pack("<II", 1, 0) + b"name".ljust(32, b"\0") + struct.pack("<I", 0x100)
        with self.assertRaises(adtinfo.AdtError):
            adtinfo.read_adt(blob)

    def test_rejects_a_header_that_is_too_short(self):
        with self.assertRaises(adtinfo.AdtError):
            adtinfo.read_adt(b"\0" * 7)


# --------------------------------------------------------------------------
# Flat device tree

class FdtTests(unittest.TestCase):
    def test_reads_a_flat_tree(self):
        blob = fdt_blob("", [("compatible", b"qcom,msm8998\0"),
                             ("#address-cells", struct.pack(">I", 2))],
                        [("memory@40000000", [
                            ("device_type", b"memory\0"),
                            ("reg", struct.pack(">QQ", 0x40000000, 0xF0000000)),
                        ])])
        root = adtinfo.read_fdt(blob)

        self.assertEqual(root.name, "")
        self.assertEqual(root.get("compatible").value, b"qcom,msm8998\0")
        memory = root.children[0]
        self.assertEqual(memory.name, "memory@40000000")
        self.assertEqual(adtinfo.decode(memory.get("reg"), big_endian=True),
                         ("bytes", struct.pack(">QQ", 0x40000000, 0xF0000000).hex()))

    def test_decodes_big_endian_integers(self):
        blob = fdt_blob("", [("one", struct.pack(">I", 0x8015)),
                             ("two", struct.pack(">Q", 0x0123456789abcdef))])
        root = adtinfo.read_fdt(blob)
        self.assertEqual(adtinfo.decode(root.get("one"), True), ("u32", 0x8015))
        self.assertEqual(adtinfo.decode(root.get("two"), True),
                         ("u64", 0x0123456789abcdef))

    def test_rejects_a_bad_magic(self):
        with self.assertRaises(adtinfo.FdtError):
            adtinfo.read_fdt(b"\0" * 64)

    def test_rejects_a_total_size_past_the_blob(self):
        blob = bytearray(fdt_blob("", [("a", b"b")]))
        struct.pack_into(">I", blob, 4, len(blob) + 1)
        with self.assertRaises(adtinfo.FdtError):
            adtinfo.read_fdt(bytes(blob))

    def test_rejects_a_missing_end_token(self):
        blob = bytearray(fdt_blob("", [("a", b"b")]))
        struct.pack_into(">I", blob, len(blob) - 4, 0)
        with self.assertRaises(adtinfo.FdtError):
            adtinfo.read_fdt(bytes(blob))


# --------------------------------------------------------------------------
# Value decoding and rendering

class DecodeTests(unittest.TestCase):
    def prop(self, value, reference=False):
        return adtinfo.Property("p", value, reference)

    def test_classifies_empty(self):
        self.assertEqual(adtinfo.decode(self.prop(b"")), ("empty", None))

    def test_classifies_strings(self):
        self.assertEqual(adtinfo.decode(self.prop(b"local\0")),
                         ("strings", ["local"]))

    def test_allows_zero_padding_after_a_string(self):
        self.assertEqual(adtinfo.decode(self.prop(b"local\0\0\0")),
                         ("strings", ["local"]))

    def test_classifies_multiple_strings(self):
        self.assertEqual(adtinfo.decode(self.prop(b"one\0two\0")),
                         ("strings", ["one", "two"]))

    def test_classifies_four_byte_integers(self):
        self.assertEqual(adtinfo.decode(self.prop(struct.pack("<I", 0x8015))),
                         ("u32", 0x8015))

    def test_classifies_eight_byte_integers(self):
        self.assertEqual(
            adtinfo.decode(self.prop(struct.pack("<Q", 0x0123456789abcdef))),
            ("u64", 0x0123456789abcdef))

    def test_falls_back_to_bytes(self):
        self.assertEqual(adtinfo.decode(self.prop(b"\x01\x02")), ("bytes", "0102"))

    def test_marks_a_reference_in_the_rendered_value(self):
        rendered = adtinfo.render_value(self.prop(b"syscfg/x", True))
        self.assertIn("[syscfg reference]", rendered)

    def test_json_keeps_the_raw_bytes_and_the_type(self):
        root = adtinfo.Node("device-tree", [
            adtinfo.Property("d", b"d\0\0\0"),
            adtinfo.Property("ref", b"syscfg/x", True),
        ])
        payload = adtinfo.to_dict(root)
        self.assertEqual(payload["properties"][0]["type"], "strings")
        self.assertEqual(payload["properties"][0]["hex"], "64000000")
        self.assertEqual(payload["properties"][1]["hex"], "7379736366672f78")
        self.assertTrue(payload["properties"][1]["reference"])


# --------------------------------------------------------------------------
# File loading and the command line

class CliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            prefix="adtinfo-test-", dir=__import__("os").environ.get("TMPDIR"))

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name, blob):
        path = pathlib.Path(self.directory.name) / name
        path.write_bytes(blob)
        return path

    def test_auto_detects_an_adt(self):
        path = self.write("tree.adt", adt_node("device-tree", [("model", b"x\0")]))
        root, kind, big_endian = adtinfo.load(path)
        self.assertEqual((kind, big_endian, root.name), ("adt", False, "device-tree"))

    def test_auto_detects_an_fdt(self):
        path = self.write("tree.dtb", fdt_blob("", [("model", b"x\0")]))
        _root, kind, big_endian = adtinfo.load(path)
        self.assertEqual((kind, big_endian), ("fdt", True))

    def test_json_output_is_one_document(self):
        path = self.write("tree.adt", adt_node(
            "device-tree", [("chip-id", struct.pack("<I", 0x8015))]))
        out = StringIO()
        with redirect_stdout(out):
            status = adtinfo.main(["--json", str(path)])
        payload = json.loads(out.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["format"], "adt")
        properties = {prop["name"]: prop for prop in payload["root"]["properties"]}
        self.assertEqual(properties["chip-id"]["value"], 0x8015)
        self.assertEqual(properties["chip-id"]["hex"], "15800000")

    def test_a_missing_file_is_an_error_status(self):
        with redirect_stdout(StringIO()):
            status = adtinfo.main([str(pathlib.Path(self.directory.name) / "nope")])
        self.assertEqual(status, 2)

    def test_a_corrupt_tree_is_an_error_status(self):
        path = self.write("bad.adt", b"\xff" * 8)
        with redirect_stdout(StringIO()):
            status = adtinfo.main([str(path)])
        self.assertEqual(status, 2)


class RealFileTests(unittest.TestCase):
    """Runs only when the caller has extracted Apple's own ADT."""

    def test_the_shipped_iphone10_1_tree_parses_to_eof(self):
        candidate = pathlib.Path("/data/opencode/ipsw/d20ap.adt")
        if not candidate.exists():
            self.skipTest("no extracted d20ap.adt")
        root = adtinfo.load(candidate)[0]
        self.assertEqual(root.name, "device-tree")
        self.assertEqual(root.get("model").value, b"iPhone10,1\0")
        chosen = next(child for child in root.children if child.name == "chosen")
        self.assertEqual(chosen.get("boot-type").value.rstrip(b"\0"), b"local")
        cpus = next(child for child in root.children if child.name == "cpus")
        self.assertEqual(len(cpus.children), 6)


if __name__ == "__main__":
    unittest.main()
