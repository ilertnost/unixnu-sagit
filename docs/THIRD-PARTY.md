# Third-party provenance

This repository is GPL-3.0-or-later, but it does not exist in a vacuum: it is
read next to other device ports, XNU itself and Linux. What may be reused is
decided **per file**, by the licence that file declares, never by the licence
of the repository it came from. This page records the rule, the tool that
enforces it, and the audit of every tree we have looked at.

## The rule

| Category | Examples | What we may do |
|---|---|---|
| permissive | `BSD-2-Clause`, `BSD-2-Clause-Patent`, `BSD-3-Clause`, `MIT`, `ISC`, `Apache-2.0`, `Zlib` | Vendor into `third_party/` as is. The copyright header and the SPDX line stay in the file; a `MANIFEST.tsv` records the upstream commit and the sha256 of every file |
| GPL-3.0-compatible | `GPL-2.0-or-later`, `GPL-2.0+`, `LGPL-2.1-or-later`, `MPL-2.0` | May be combined with this project, subject to its terms |
| GPL-2.0-only | `GPL-2.0-only`, `GPL-2.0` | **Never copied and never paraphrased.** Only the facts are used |
| APSL-2.0 | XNU, Apple headers | Same as above: a dependency of the port, not a component of this repository |
| no licence line | generated headers, `Info.plist`, vendored blobs | Not vendored; listed as `unresolved` until someone checks them by hand |

Rewriting a file "so that none of the original code remains" is not a way
around the third and fourth rows. A paraphrase that keeps the structure,
sequences and names of a work is a derivative work; the licence of the
original still applies. The only lawful way to use `GPL-2.0-only` source in a
GPLv3 project is to take the facts and write the code independently, which is
what the last column of the table says.

A **fact** is an address, a register value, a bit field, a timing, a
device-tree property, an ordering constraint: something a datasheet or an
oscilloscope would tell us. Facts are written into `docs/` with a pointer to
where they came from, and the implementation is written from that note. This
is how `docs/SAGIT-GPIO-MAP.md` and the sagit device tree were produced.

An SPDX expression is evaluated as written. `GPL-2.0-only OR BSD-2-Clause` is
consumed through the BSD branch; `BSD-2-Clause AND GPL-2.0-only` still needs
both, so it is `GPL-2.0-only` for us. `GPL-2.0 WITH Linux-syscall-note` stays
`GPL-2.0-only`: the exception widens the permitted uses, it does not remove
the copyleft.

## The tool

`tools/licence-audit.py` implements the table:

```sh
# classify a tree
python3 tools/licence-audit.py /path/to/tree --dirs

# only what could be vendored, file by file
python3 tools/licence-audit.py /path/to/tree --per-file --only vendor

# vendor it into third_party/<name>/
python3 tools/licence-audit.py /path/to/tree --copy-to third_party/<name> \
    --upstream https://github.com/owner/repo
```

`--copy-to` copies only files classified `permissive`, keeps their headers,
and writes `MANIFEST.tsv`, `EXCLUDED.tsv` and `NOTICE`. `--require-clean`
returns non-zero if anything in the tree is not permissive; that is the check
to run in CI before a vendored directory is regenerated.

The vendored directory is generated, not edited: fix the upstream, re-run the
tool, commit the diff.

## Audit: `MC-XiaoXiao/ios_sagit`

Commit `898998cf1b74ea103e0c16d6dc0016822d8d644b` (2026-10-23), 6002 files
with a source suffix, scanned by the tool above.

| category | files |
|---|---|
| permissive | 460 |
| GPL-3.0-compatible | 4 |
| GPL-2.0-only | 387 |
| APSL-2.0 | 3271 |
| no licence line | 1880 |
| **total** | **6002** |

What the third and fourth rows are matters more than the totals: 3188 of the
3271 APSL files are the XNU tree under `kernel/`, and the `GPL-2.0-only` files
are the Linux-derived drivers — which is most of the code that touches power,
clocks, touch and baseband hardware.

By directory:

| directory | permissive | GPL-2.0-only | APSL-2.0 | no licence |
|---|---|---|---|---|
| `boot/` | 28 | 14 | 0 | 0 |
| `drivers/` | 279 | 166 | 6 | 108 |
| `kernel/` | 5 | 0 | 3188 | 1147 |
| `tools/` | 49 | 1 | 61 | 135 |
| `tests/` | 73 | 53 | 0 | 73 |
| `docs/` | 21 | 152 | 16 | 354 |
| `scripts/` | 0 | 1 | 0 | 62 |
| `device_trees/` | 5 | 0 | 0 | 0 |

Inside `drivers/`, per subsystem (`permissive` / `GPL-2.0-only`):

| subsystem | permissive | GPL-2.0-only | verdict |
|---|---|---|---|
| `usb` | 62 | 2 | usable |
| `include` | 65 | 3 | usable, but 6 Apple headers are unresolved |
| `wifi` | 27 | 0 | usable |
| `bluetooth` | 24 | 0 | usable |
| `gpu` | 39 | 7 | usable in part |
| `display` | 17 | 5 | usable in part |
| `storage` | 5 | 1 | usable in part; the PHY register sequence is `GPL-2.0-only` |
| `platform` | 6 | 2 | usable in part |
| `clock` | 8 | 19 | mostly facts |
| `power` | 2 | 22 | facts only |
| `input` (touch) | 4 | 23 | facts only |
| `bus` | 13 | 24 | mostly facts |
| `baseband` | 0 | 48 | facts only |

The `no licence line` column is not a rounding error: it is where Apple
headers copied without their licence block, generated tables and `Info.plist`
files live. They are `unresolved` and are not vendored until someone checks
them individually.

## Audit: `sing0626/UniXNU-marble`

Commit `a632603`, reviewed for the arm64 KPC backend that the public
`xnu-8796.141.3` tree omits. The repository is MIT (its `LICENSING.md` covers
original additions; the file in question, `legacy-xnu/overlay/osfmk/arm64/kpc.c`,
carries no separate notice, so it is checked by hand and kept under MIT).

Our copy, `patches/0020-sagit-xnu8796-kpc-backend.patch`, adds the SPDX line,
adds the `kpc_pmi_handler` stub that `monotonic_arm64.c` expects, and keeps the
copyright and the full MIT permission text in the file.

`patches/0021-sagit-xnu8796-pmap-preemption.patch` follows the same tree's
approach for `pmap_pending_preemption()` (define the hygiene out-of-line
wrapper on top of the public inline in `pmap_misc.c`); the four-line function
is written here in our own words. No other file is taken from that tree; the
AMCC, tunables and PPL overlays next to it are not used here (we keep those
objects gated off instead).

## What this means in practice

- The parts we can carry today without any further work are the ones that
  matter for the next milestones: USB, Wi-Fi, Bluetooth, most of the GPU and
  display glue, and the permissive half of the storage driver.
- The power, clock, touch and baseband paths stay a clean-room task. That is
  not a choice of style: those files are Linux-derived and `GPL-2.0-only`,
  and no amount of rewriting makes them usable here.
- Nothing from `kernel/` is vendored. It is Apple's XNU under APSL-2.0 with
  unmarked local changes, and this project already builds XNU from UniXNU and
  the upstream sources.
