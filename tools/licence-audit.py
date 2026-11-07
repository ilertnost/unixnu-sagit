#!/usr/bin/env python3
#
# Copyright (c) 2026 ilertnost
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Classify a source tree by the SPDX licence identifier in each file.

The boot chain lives next to other device ports and upstream trees, and they
are not licensed uniformly: a single repository can mix permissive code
(BSD-2-Clause, MIT, ISC) with Linux-derived drivers marked GPL-2.0-only.  This
tool answers one question per file: may that file be vendored into this
GPL-3.0-or-later repository, or may only the facts it describes be used?

Classification is about what *this* project can consume, not about the merits
of a licence:

  permissive        BSD-2-Clause, BSD-2-Clause-Patent, BSD-3-Clause, MIT,
                    ISC, Apache-2.0, Zlib, 0BSD, Unlicense, CC0-1.0.
                    Can be copied in, keeping the copyright notice.
  gpl3-compatible   GPL-2.0-or-later and later GPL/LGPL/MPL variants.  Can be
                    combined with GPL-3.0-or-later, subject to its terms.
  gpl2-only         GPL-2.0-only, and GPL-2.0 which SPDX deprecates to the
                    same thing.  Not compatible with GPL-3.0: neither copying
                    nor paraphrasing is allowed.  Only facts may be used.
  apple             APSL-2.0, the licence XNU itself carries.  Not ours to
                    relicense; the kernel is a dependency, not a component.
  unknown           No SPDX line, or an identifier this tool does not know.

Dual expressions follow their own rules: an ``OR`` of a permissive and a
GPL-2.0-only licence is consumed through the permissive branch, while an
``AND`` has to satisfy every part.

Two modes:

  licence-audit.py PATH                  report
  licence-audit.py PATH --copy-to DIR    vendor the permissive files

The second mode is how third_party/ trees are made: it copies only files
classified ``permissive``, preserves their relative path and, above all, their
copyright headers and SPDX line, and writes MANIFEST.tsv next to them with the
upstream URL, commit and the sha256 of every file.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SPDX_RE = re.compile(r"SPDX-License-Identifier:[ \t]*([^\r\n]+)", re.IGNORECASE)

# Only the head of a file is scanned: every real SPDX line is in the first
# few lines, and a 4 KiB prefix keeps the scan cheap on a 100 MB tree.
HEAD_BYTES = 4096

SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv",
             "build", "out", "dist", "target"}

SOURCE_SUFFIXES = {
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm",
    ".s", ".asm", ".S", ".py", ".sh", ".bash", ".pl", ".rb",
    ".mk", ".cmake", ".ld", ".lds", ".map", ".dts", ".dtsi",
    ".json", ".toml", ".yml", ".yaml", ".ini", ".plist",
    ".md", ".txt", ".patch", ".diff",
}

PERMISSIVE = {
    "0bsd", "apache-2.0", "bsd-2-clause", "bsd-2-clause-patent",
    "bsd-2-clause-views", "bsd-3-clause", "bsd-3-clause-clear",
    "cc0-1.0", "isc", "mit", "mit-0", "unlicense", "wtfpl", "zlib",
    "bsl-1.0", "postgresql", "ncsa",
}

GPL3_COMPATIBLE = {
    "gpl-2.0-or-later", "gpl-3.0-only", "gpl-3.0-or-later",
    "lgpl-2.1-or-later", "lgpl-3.0-only", "lgpl-3.0-or-later",
    "mpl-2.0", "epl-2.0", "cc-by-sa-4.0",
    # Deprecated SPDX spellings of the same grants.
    "gpl-2.0+", "gpl-3.0+", "lgpl-2.1+", "lgpl-3.0+",
}

GPL2_ONLY = {"gpl-2.0-only", "gpl-2.0"}

APPLE = {"apsl-2.0"}

CATEGORIES = ("permissive", "gpl3-compatible", "gpl2-only", "apple", "unknown")

# Higher wins in an OR, lower wins in an AND.  "unknown" must lose to
# everything, so that an unrecognised branch cannot make a file look safe.
RANK = {"permissive": 4, "gpl3-compatible": 3, "gpl2-only": 2, "apple": 1,
        "unknown": 0}

VERDICT = {
    "permissive": "vendor",
    "gpl3-compatible": "compatible",
    "gpl2-only": "facts-only",
    "apple": "facts-only",
    "unknown": "unresolved",
}


@dataclass
class Record:
    path: str
    licence: str | None
    category: str
    verdict: str
    size: int
    sha256: str = ""

    def as_row(self) -> str:
        return "\t".join((self.path, self.licence or "-", self.category,
                          self.verdict, str(self.size), self.sha256))


def _split_top(expr: str, op: str) -> list[str]:
    """Split on a top-level ``OR``/``AND``, leaving bracketed groups alone."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    n = len(op)
    while i < len(expr):
        char = expr[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif (depth == 0 and expr[i:i + n].upper() == op
              and (i == 0 or expr[i - 1] in " )")
              and (i + n == len(expr) or expr[i + n] in " (")):
            parts.append(expr[start:i])
            start = i + n
            i += n - 1
        i += 1
    parts.append(expr[start:])
    return [part.strip() for part in parts if part.strip()]


def _strip_parens(expr: str) -> str:
    expr = expr.strip()
    while len(expr) > 1 and expr[0] == "(" and expr[-1] == ")":
        depth = 0
        balanced = True
        for index, char in enumerate(expr):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(expr) - 1:
                    balanced = False
                    break
        if not balanced:
            break
        expr = expr[1:-1].strip()
    return expr


def _single(identifier: str) -> str:
    # "GPL-2.0 WITH Linux-syscall-note" stays GPL-2.0 for consumption purposes;
    # the exception widens the usages, it does not relax copyleft.
    identifier = identifier.split(" WITH ")[0].strip().lower()
    if identifier in PERMISSIVE:
        return "permissive"
    if identifier in GPL3_COMPATIBLE:
        return "gpl3-compatible"
    if identifier in GPL2_ONLY:
        return "gpl2-only"
    if identifier in APPLE:
        return "apple"
    return "unknown"


def classify(expression: str | None) -> str:
    """Return the category of an SPDX expression (see the module docstring)."""
    if not expression or not expression.strip():
        return "unknown"
    expr = _strip_parens(expression)
    alternatives = _split_top(expr, "OR")
    if len(alternatives) > 1:
        return max((classify(part) for part in alternatives), key=RANK.get)
    contexts = _split_top(expr, "AND")
    if len(contexts) > 1:
        return min((classify(part) for part in contexts), key=RANK.get)
    return _single(_strip_parens(expr))


def read_spdx(path: Path) -> str | None:
    """Return the SPDX expression of a file, or None when it carries none."""
    try:
        with path.open("rb") as handle:
            head = handle.read(HEAD_BYTES)
    except OSError:
        return None
    if b"\0" in head:
        return None
    text = head.decode("utf-8", "replace")
    match = SPDX_RE.search(text)
    if not match:
        # Apple's sources predate SPDX and say it in prose instead.  Treating
        # that as "unknown" would hide XNU-derived files behind a category
        # that looks like an oversight rather than a licence.
        if "Apple Public Source License" in text:
            return "APSL-2.0"
        return None
    expression = match.group(1)
    for cut in ("*/", '"', "'"):
        expression = expression.split(cut)[0]
    return expression.strip() or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def scan(root: Path, suffixes: set[str] | None = None) -> list[Record]:
    """Walk *root* and classify every source-like file under it."""
    records: list[Record] = []
    allowed = suffixes if suffixes is not None else SOURCE_SUFFIXES
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(directory) / name
            if allowed and path.suffix not in allowed:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            licence = read_spdx(path)
            category = classify(licence)
            records.append(Record(
                path=str(path.relative_to(root)),
                licence=licence,
                category=category,
                verdict=VERDICT[category],
                size=size,
            ))
    return records


def group_by_top(records: list[Record]) -> dict[str, dict[str, int]]:
    """Count categories per first path component."""
    summary: dict[str, dict[str, int]] = {}
    for record in records:
        top = record.path.split(os.sep, 1)[0] if os.sep in record.path else "."
        bucket = summary.setdefault(top, {name: 0 for name in CATEGORIES})
        bucket[record.category] += 1
    return summary


def print_summary(records: list[Record]) -> None:
    totals = {name: 0 for name in CATEGORIES}
    for record in records:
        totals[record.category] += 1
    width = max(len(name) for name in CATEGORIES)
    print(f"{'category':<{width}}  files")
    for name in CATEGORIES:
        print(f"{name:<{width}}  {totals[name]:>6}")
    print(f"{'total':<{width}}  {len(records):>6}")


def print_dirs(records: list[Record]) -> None:
    summary = group_by_top(records)
    header = "directory".ljust(18) + "".join(name.rjust(16) for name in CATEGORIES)
    print(header)
    for top in sorted(summary):
        row = top.ljust(18)
        row += "".join(str(summary[top][name]).rjust(16) for name in CATEGORIES)
        print(row)


def copy_permissive(records: list[Record], root: Path, destination: Path,
                    upstream: str, commit: str) -> int:
    """Copy the files that may be vendored, keeping their headers."""
    copied = 0
    manifest = ["# path\tlicence\tcategory\tverdict\tsize\tsha256"]
    destination.mkdir(parents=True, exist_ok=True)
    for record in records:
        if record.verdict != "vendor":
            continue
        source = root / record.path
        target = destination / record.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        record.sha256 = _sha256(source)
        manifest.append(record.as_row())
        copied += 1
    (destination / "MANIFEST.tsv").write_text("\n".join(manifest) + "\n")
    notice = [
        "Vendored third-party code.",
        "",
        f"upstream: {upstream}",
        f"commit:   {commit}",
        f"date:     {datetime.date.today().isoformat()}",
        "",
        "Only files whose SPDX expression classifies as permissive are copied.",
        "Every file keeps its own copyright header and SPDX line; those terms",
        "apply to that file.  Everything else in this repository is",
        "GPL-3.0-or-later.  Files that were not copied are listed in",
        "EXCLUDED.tsv so the split is auditable.",
        "",
        "Generated by tools/licence-audit.py --copy-to.",
    ]
    (destination / "NOTICE").write_text("\n".join(notice) + "\n")
    excluded = ["# path\tlicence\tverdict"]
    for record in records:
        if record.verdict != "vendor":
            excluded.append("\t".join((record.path, record.licence or "-",
                                       record.verdict)))
    (destination / "EXCLUDED.tsv").write_text("\n".join(excluded) + "\n")
    return copied


def upstream_commit(source: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="tree to audit")
    parser.add_argument("--json", type=Path, help="also write the full list as JSON")
    parser.add_argument("--dirs", action="store_true", help="per-directory counts")
    parser.add_argument("--per-file", action="store_true", help="print every file")
    parser.add_argument("--only", choices=("vendor", "facts-only", "unresolved"),
                        help="print only files with this verdict")
    parser.add_argument("--copy-to", type=Path,
                        help="copy permissive files under this directory")
    parser.add_argument("--upstream", default=None,
                        help="URL recorded in the vendored NOTICE")
    parser.add_argument("--commit", default=None,
                        help="upstream commit recorded in the NOTICE")
    parser.add_argument("--require-clean", action="store_true",
                        help="exit 1 if any file is not permissive")
    args = parser.parse_args(argv)

    root: Path = args.path
    if not root.is_dir():
        parser.error(f"{root}: not a directory")

    records = scan(root)
    print(f"{root}: {len(records)} files")
    print_summary(records)
    if args.dirs:
        print()
        print_dirs(records)

    selected = [record for record in records
                if args.only is None or record.verdict == args.only]
    if args.per_file:
        for record in selected:
            print(f"{record.verdict:<12} {record.licence or '-':<28} {record.path}")

    if args.json:
        args.json.write_text(json.dumps([record.__dict__ for record in selected],
                                        indent=1) + "\n")

    if args.copy_to:
        commit = args.commit or upstream_commit(root)
        upstream = args.upstream or str(root)
        count = copy_permissive(records, root, args.copy_to, upstream, commit)
        print(f"\ncopied {count} permissive files to {args.copy_to}")

    if args.require_clean:
        offending = [record for record in records if record.verdict != "vendor"]
        if offending:
            print(f"\n{len(offending)} files are not permissive", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
