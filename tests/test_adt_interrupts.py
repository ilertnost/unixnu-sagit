#!/usr/bin/env python3
#
# Copyright (c) 2026 ilertnost
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""The generated ADT must let XNU resolve every interrupt parent.

XNU 8796 registers IODT interrupt providers by "AAPL,phandle" and then looks
up "interrupt-parent" with FindPHandle().  The hardware DTS uses the standard
"phandle" spelling, so make-sagit-adt.py adds the Apple spelling as well.
Without it, FindPHandle() returns NULL and IODTGetICellCounts() dereferences
it: the boot panic at pc 0xfffffff0078d3114 (IODTGetICellCounts + 0x2c).

The GIC node also has to carry the Apple value
`interrupt-controller = "master"`; XNU's pe_arm_map_interrupt_controller()
searches for that value, not the Linux empty flag.

The test builds the real tree; it skips when dtc or the UniXNU converter is
not available, because those are build-host inputs rather than repository
files.
"""

import importlib.util
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("adtinfo", ROOT / "tools" / "adtinfo.py")
adtinfo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adtinfo
SPEC.loader.exec_module(adtinfo)

UNIXNU_SRC = pathlib.Path(os.environ.get("UNIXNU_SRC", "/data/opencode/unixnu-src"))


class AdtInterruptContractTest(unittest.TestCase):

    @unittest.skipUnless(shutil.which("dtc"), "dtc is not installed")
    @unittest.skipUnless(
        (UNIXNU_SRC / "scripts" / "unixnu" / "devicetree.py").is_file(),
        "the UniXNU converter is not available",
    )
    def test_every_interrupt_parent_has_an_aapl_phandle(self):
        with tempfile.TemporaryDirectory() as scratch:
            output = pathlib.Path(scratch) / "kernel.macho.adt"
            env = dict(os.environ, TMPDIR=scratch, UNIXNU_SRC=str(UNIXNU_SRC))
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "make-sagit-adt.py"),
                 "--output", str(output),
                 "--dram-size", "0xf0000000",
                 "--chip-id", "0x8015",
                 "--unique-chip-id", "0x0123456789abcdef"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            root = adtinfo.read_adt(output.read_bytes())

        intc = next((node for node in root.walk() if node.name == "intc"), None)
        self.assertIsNotNone(intc, "the ADT has no intc node")
        controller = intc.get("interrupt-controller")
        self.assertIsNotNone(controller, "intc has no interrupt-controller")
        self.assertEqual(controller.value, b"master\0",
                         "XNU looks up the value 'master', not an empty flag")

        by_aapl_handle = {}
        for node in root.walk():
            handle = node.get("AAPL,phandle")
            if handle is not None:
                self.assertEqual(len(handle.value), 4)
                by_aapl_handle[struct.unpack("<I", handle.value)[0]] = node

        checked = 0
        for node in root.walk():
            parent = node.get("interrupt-parent")
            if parent is None:
                continue
            self.assertEqual(len(parent.value), 4, node.name)
            handle = struct.unpack("<I", parent.value)[0]
            self.assertIn(handle, by_aapl_handle,
                          f"{node.name} points at unresolved interrupt parent "
                          f"0x{handle:x}")
            provider = by_aapl_handle[handle]
            self.assertIsNotNone(provider.get("#interrupt-cells"),
                                 f"interrupt parent {provider.name} has no "
                                 "#interrupt-cells")
            checked += 1
        self.assertGreater(checked, 0, "no interrupt-parent property in the ADT")


if __name__ == "__main__":
    unittest.main()
