#!/usr/bin/env python3
#
# Copyright (c) 2026 ilertnost
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Tests for tools/licence-audit.py.

The cases are the ones that actually appear in neighbouring device trees:
BSD-2-Clause next to GPL-2.0-only, GPL-2.0-or-later, dual GPL-2.0+/MIT, and
"GPL-2.0 WITH Linux-syscall-note".  The point of the tool is that a permissive
branch of a dual expression is enough, while an AND has to satisfy every part.
"""

import importlib.util
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "licence_audit", ROOT / "tools" / "licence-audit.py")
licence_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = licence_audit
SPEC.loader.exec_module(licence_audit)


class ClassifyTest(unittest.TestCase):
    def test_permissive_families(self):
        for expression in ("BSD-2-Clause", "BSD-2-Clause-Patent", "BSD-3-Clause",
                           "MIT", "ISC", "Apache-2.0", "Zlib", "0BSD",
                           "Unlicense", "CC0-1.0"):
            with self.subTest(expression=expression):
                self.assertEqual(licence_audit.classify(expression), "permissive")

    def test_gpl2_only(self):
        for expression in ("GPL-2.0-only", "GPL-2.0", "gpl-2.0-only"):
            with self.subTest(expression=expression):
                self.assertEqual(licence_audit.classify(expression), "gpl2-only")

    def test_gpl3_compatible(self):
        for expression in ("GPL-2.0-or-later", "GPL-2.0+", "GPL-3.0-or-later",
                           "LGPL-2.1-or-later", "MPL-2.0"):
            with self.subTest(expression=expression):
                self.assertEqual(licence_audit.classify(expression),
                                 "gpl3-compatible")

    def test_syscall_note_keeps_the_copyleft(self):
        self.assertEqual(
            licence_audit.classify("GPL-2.0 WITH Linux-syscall-note"),
            "gpl2-only")

    def test_dual_is_read_through_the_permissive_branch(self):
        for expression in ("GPL-2.0+ OR MIT",
                           "(GPL-2.0-only OR BSD-2-Clause)",
                           "GPL-2.0 OR BSD-3-Clause",
                           "Apache-2.0 AND BSD-2-Clause"):
            with self.subTest(expression=expression):
                self.assertEqual(licence_audit.classify(expression),
                                 "permissive")

    def test_and_has_to_satisfy_every_part(self):
        self.assertEqual(licence_audit.classify("BSD-2-Clause AND GPL-2.0-only"),
                         "gpl2-only")

    def test_apple_and_unknown(self):
        self.assertEqual(licence_audit.classify("APSL-2.0"), "apple")
        self.assertEqual(licence_audit.classify("Proprietary"), "unknown")
        self.assertEqual(licence_audit.classify(None), "unknown")
        self.assertEqual(licence_audit.classify("   "), "unknown")


class ReadSpdxTest(unittest.TestCase):
    def _write(self, directory, name, text, binary=b""):
        path = pathlib.Path(directory) / name
        path.write_bytes(text.encode() if isinstance(text, str) else text)
        return path

    def test_slash_star_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "a.c", "/* SPDX-License-Identifier: BSD-2-Clause */\n")
            self.assertEqual(licence_audit.read_spdx(path), "BSD-2-Clause")

    def test_line_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "a.py", "#!/usr/bin/env python3\n"
                                           "# SPDX-License-Identifier: GPL-2.0-only\n")
            self.assertEqual(licence_audit.read_spdx(path), "GPL-2.0-only")

    def test_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "a.txt", "no licence here\n")
            self.assertIsNone(licence_audit.read_spdx(path))

    def test_binary_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "a.bin",
                               b"\x7fELF\x00\x00SPDX-License-Identifier: MIT\n")
            self.assertIsNone(licence_audit.read_spdx(path))


class ScanAndVendorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        (base / "tree" / "sub").mkdir(parents=True)
        (base / "tree" / "build").mkdir()
        (base / "tree" / "bsd.c").write_text("/* SPDX-License-Identifier: BSD-2-Clause */\nint a;\n")
        (base / "tree" / "sub" / "gpl.c").write_text("/* SPDX-License-Identifier: GPL-2.0-only */\nint b;\n")
        (base / "tree" / "sub" / "dual.c").write_text("/* SPDX-License-Identifier: GPL-2.0+ OR MIT */\n")
        (base / "tree" / "build" / "x.c").write_text("/* SPDX-License-Identifier: MIT */\n")
        (base / "tree" / "notes.rst").write_text("not a source suffix\n")
        self.base = base

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_skips_build_dirs_and_foreign_suffixes(self):
        records = licence_audit.scan(self.base / "tree")
        paths = sorted(record.path for record in records)
        self.assertEqual(paths, ["bsd.c", "sub/dual.c", "sub/gpl.c"])
        self.assertNotIn("notes.rst", paths)

    def test_group_by_top(self):
        records = licence_audit.scan(self.base / "tree")
        summary = licence_audit.group_by_top(records)
        self.assertEqual(summary["."]["permissive"], 1)
        self.assertEqual(summary["sub"]["gpl2-only"], 1)
        self.assertEqual(summary["sub"]["permissive"], 1)

    def test_copy_permissive_keeps_headers_and_writes_the_split(self):
        records = licence_audit.scan(self.base / "tree")
        destination = self.base / "vendor"
        count = licence_audit.copy_permissive(
            records, self.base / "tree", destination,
            "https://example.invalid/repo", "deadbeef")
        self.assertEqual(count, 2)
        copied = destination / "bsd.c"
        self.assertTrue(copied.is_file())
        self.assertIn("SPDX-License-Identifier: BSD-2-Clause",
                      copied.read_text())
        self.assertFalse((destination / "sub" / "gpl.c").exists())
        self.assertTrue((destination / "sub" / "dual.c").is_file())
        manifest = (destination / "MANIFEST.tsv").read_text()
        self.assertIn("bsd.c", manifest)
        self.assertIn("sub/dual.c", manifest)
        self.assertNotIn("gpl.c\t", manifest)
        excluded = (destination / "EXCLUDED.tsv").read_text()
        self.assertIn("sub/gpl.c", excluded)
        notice = (destination / "NOTICE").read_text()
        self.assertIn("https://example.invalid/repo", notice)
        self.assertIn("deadbeef", notice)


if __name__ == "__main__":
    unittest.main()
