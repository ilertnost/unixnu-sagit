#!/usr/bin/env python3
#
# Copyright (c) 2026 ilertnost
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Read an Apple device tree (ADT) or a flat device tree (FDT).

An ADT is what DarwinBoot hands to XNU, and it is *not* an FDT. It is a
little-endian tree with no magic and no header:

    u32 property_count
    u32 child_count
    for each property:
        char name[32]          NUL-terminated, zero padded
        u32  length            bit 31 = the value is a /syscfg reference
        u8   value[length]     padded to a four-byte boundary
    for each child:
        a complete node

The value flag is iBoot's: Apple ships the ADT inside an IPSW with placeholders
such as "syscfg/SrNm/0x20,zeroes/0x20" instead of the serial number, and the
bootloader replaces them. Values without the flag are already final. This tool
only reads; it never rewrites a tree.

A flat FDT is accepted too, so a hardware DTB compiled with dtc can be inspected
with the same command and compared against the generated ADT.

The output is a tree. Value rendering is deliberately conservative: a value is
shown as a string only when it is NUL terminated and printable, as an integer
when it is exactly four or eight bytes, and as hex otherwise. `--json` carries
the full value of every property and the reference flag.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

ADT_REFERENCE = 0x80000000
FDT_MAGIC = 0xD00DFEED
MAX_DEPTH = 64
MAX_ITEMS = 1 << 20


class TreeError(ValueError):
    """The input is not a well-formed Apple device tree or flat device tree."""


class AdtError(TreeError):
    """The input is not a well-formed Apple device tree."""


class FdtError(TreeError):
    """The input is not a well-formed flattened device tree."""


@dataclass(frozen=True)
class Property:
    name: str
    value: bytes
    reference: bool = False


@dataclass
class Node:
    name: str
    properties: list[Property] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)

    def get(self, name: str) -> Property | None:
        for prop in self.properties:
            if prop.name == name:
                return prop
        return None

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


# --------------------------------------------------------------------------
# Apple device tree

def _adt_string(raw: bytes, where: str) -> str:
    end = raw.find(b"\0")
    if end < 0:
        raise AdtError(f"{where}: property name is not NUL terminated")
    if end == 0:
        raise AdtError(f"{where}: property name is empty")
    try:
        return raw[:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise AdtError(f"{where}: property name is not ASCII") from error


def _adt_node(blob: bytes, offset: int, depth: int) -> tuple[Node, int]:
    if depth > MAX_DEPTH:
        raise AdtError("ADT nesting is deeper than %d levels" % MAX_DEPTH)
    if offset + 8 > len(blob):
        raise AdtError(f"ADT node header at 0x{offset:x} is truncated")
    count, children = struct.unpack_from("<II", blob, offset)
    if count > MAX_ITEMS or children > MAX_ITEMS:
        raise AdtError(f"ADT node at 0x{offset:x} claims {count} properties "
                       f"and {children} children")
    offset += 8
    properties: list[Property] = []
    for index in range(count):
        if offset + 32 > len(blob):
            raise AdtError(f"ADT property name at 0x{offset:x} is truncated")
        name = _adt_string(blob[offset:offset + 32],
                           f"ADT property {index} in node at 0x{offset - 8:x}")
        offset += 32
        if offset + 4 > len(blob):
            raise AdtError(f"ADT property {name!r} length is truncated")
        raw_length = struct.unpack_from("<I", blob, offset)[0]
        offset += 4
        reference = bool(raw_length & ADT_REFERENCE)
        length = raw_length & ~ADT_REFERENCE
        if length > len(blob) - offset:
            raise AdtError(f"ADT property {name!r} claims {length} bytes, "
                           f"{len(blob) - offset} remain")
        value = blob[offset:offset + length]
        offset += length + (-length % 4)
        if offset > len(blob):
            raise AdtError(f"ADT property {name!r} padding runs past the end")
        properties.append(Property(name, value, reference))

    children_nodes: list[Node] = []
    for _ in range(children):
        child, offset = _adt_node(blob, offset, depth + 1)
        children_nodes.append(child)

    node = Node("", properties, children_nodes)
    marker = node.get("name")
    if marker is not None:
        raw = marker.value
        end = raw.find(b"\0")
        if end < 0:
            end = len(raw)
        try:
            node.name = raw[:end].decode("ascii")
        except UnicodeDecodeError:
            node.name = raw[:end].decode("latin-1")
    return node, offset


def read_adt(blob: bytes) -> Node:
    """Parse an Apple device tree. The whole input must belong to the tree."""
    if len(blob) < 8:
        raise AdtError("ADT is shorter than one node header")
    root, offset = _adt_node(blob, 0, 0)
    if offset != len(blob):
        raise AdtError(f"ADT has {len(blob) - offset} trailing bytes "
                       f"after the root node")
    return root


# --------------------------------------------------------------------------
# Flattened device tree

def read_fdt(blob: bytes) -> Node:
    """Parse a version 16+ flattened device tree (`d00dfeed`)."""
    if len(blob) < 40:
        raise FdtError("FDT header is truncated")
    (magic, total, off_struct, off_strings, _off_rsvmap, version,
     _last_comp, _boot_cpu, size_strings, size_struct) = struct.unpack_from(
        ">10I", blob)
    if magic != FDT_MAGIC:
        raise FdtError(f"bad FDT magic 0x{magic:08x}")
    if total > len(blob):
        raise FdtError(f"FDT claims {total} bytes, only {len(blob)} are present")
    if version < 16:
        raise FdtError(f"FDT version {version} is too old")
    if off_struct + size_struct > total or off_strings + size_strings > total:
        raise FdtError("FDT blocks run past the blob")
    strings = blob[off_strings:off_strings + size_strings]

    cursor = off_struct
    limit = off_struct + size_struct
    stack: list[Node] = []
    root: Node | None = None

    while cursor + 4 <= limit:
        token = struct.unpack_from(">I", blob, cursor)[0]
        cursor += 4
        if token == 1:                                   # FDT_BEGIN_NODE
            end = blob.find(b"\0", cursor, limit)
            if end < 0:
                raise FdtError("FDT node name is not terminated")
            try:
                name = blob[cursor:end].decode("ascii")
            except UnicodeDecodeError as error:
                raise FdtError("FDT node name is not ASCII") from error
            if len(stack) > MAX_DEPTH:
                raise FdtError("FDT nesting is too deep")
            node = Node(name)
            if stack:
                stack[-1].children.append(node)
            elif root is None:
                root = node
            else:
                raise FdtError("FDT has more than one root node")
            stack.append(node)
            cursor = (end + 4) & ~3
        elif token == 2:                                 # FDT_END_NODE
            if not stack:
                raise FdtError("FDT end-node without a node")
            stack.pop()
        elif token == 3:                                 # FDT_PROP
            if not stack or cursor + 8 > limit:
                raise FdtError("FDT property header is invalid")
            length, name_offset = struct.unpack_from(">II", blob, cursor)
            cursor += 8
            if name_offset >= size_strings:
                raise FdtError("FDT property name offset is out of range")
            end = strings.find(b"\0", name_offset)
            if end < 0:
                raise FdtError("FDT property name is not terminated")
            if cursor + length > limit:
                raise FdtError("FDT property value runs past the structure")
            try:
                name = strings[name_offset:end].decode("ascii")
            except UnicodeDecodeError as error:
                raise FdtError("FDT property name is not ASCII") from error
            stack[-1].properties.append(Property(name, blob[cursor:cursor + length]))
            cursor = (cursor + length + 3) & ~3
        elif token == 4:                                 # FDT_NOP
            continue
        elif token == 9:                                 # FDT_END
            if stack or root is None:
                raise FdtError("FDT end token before the tree is complete")
            return root
        else:
            raise FdtError(f"unknown FDT token {token} at 0x{cursor - 4:x}")
    raise FdtError("FDT end token is missing")


# --------------------------------------------------------------------------
# Value decoding

def _strings(raw: bytes) -> list[str] | None:
    """Return the NUL-separated ASCII strings, or None if the value is not one.

    Zero padding after the last terminator is allowed: Apple stores short
    strings in fixed-size properties (`boot-type` occupies twelve bytes), and
    the generator pads every value to four bytes.
    """
    if not raw or raw[-1] != 0:
        return None
    if not any(0x20 <= byte <= 0x7E for byte in raw):
        return None
    if any(byte not in (0,) and not 0x20 <= byte <= 0x7E for byte in raw):
        return None
    body = raw.rstrip(b"\0")
    if not body or b"\0\0" in body:
        return None
    try:
        return [part.decode("ascii") for part in body.split(b"\0")]
    except UnicodeDecodeError:
        return None


def decode(prop: Property, big_endian: bool = False):
    """Classify a property value as empty, strings, u32, u64 or bytes."""
    raw = prop.value
    if not raw:
        return "empty", None
    text = _strings(raw)
    if text is not None:
        return "strings", text
    if len(raw) == 4:
        return "u32", int.from_bytes(raw, "big" if big_endian else "little")
    if len(raw) == 8:
        return "u64", int.from_bytes(raw, "big" if big_endian else "little")
    return "bytes", raw.hex()


def render_value(prop: Property, big_endian: bool = False) -> str:
    kind, value = decode(prop, big_endian)
    if kind == "empty":
        rendered = "(empty)"
    elif kind == "strings":
        rendered = ", ".join(json.dumps(item) for item in value)
    elif kind == "u32":
        rendered = f"0x{value:08x}"
    elif kind == "u64":
        rendered = f"0x{value:016x}"
    else:
        hexed = value if len(value) <= 64 else value[:64] + "..."
        rendered = f"bytes[{len(value) // 2}] {hexed}"
    if prop.reference:
        rendered += "  [syscfg reference]"
    return rendered


# --------------------------------------------------------------------------
# Output

def to_dict(node: Node, big_endian: bool = False) -> dict:
    properties = []
    for prop in node.properties:
        kind, value = decode(prop, big_endian)
        properties.append({
            "name": prop.name,
            "type": kind,
            "length": len(prop.value),
            "reference": prop.reference,
            "value": value,
            # The decoded value is a guess; the raw bytes never are. Keep them
            # so a consumer can re-interpret an ASCII-looking integer.
            "hex": prop.value.hex(),
        })
    return {
        "name": node.name,
        "properties": properties,
        "children": [to_dict(child, big_endian) for child in node.children],
    }


def render_text(root: Node, big_endian: bool = False, out=None) -> None:
    out = out or sys.stdout
    out.write(f"{root.name or '<unnamed>'}\n")
    _render_node(root, big_endian, out, 1)


def _render_node(node: Node, big_endian: bool, out, depth: int) -> None:
    indent = "  " * depth
    for prop in node.properties:
        if prop.name == "name" and decode(prop, big_endian)[0] == "strings":
            continue                              # already on the node line
        out.write(f"{indent}{prop.name} = {render_value(prop, big_endian)}\n")
    for child in node.children:
        out.write(f"{indent}{child.name or '<unnamed>'}\n")
        _render_node(child, big_endian, out, depth + 1)


def load(path: str | Path, forced: str = "auto") -> tuple[Node, str, bool]:
    """Read a tree. Returns (root, format name, big_endian)."""
    blob = Path(path).read_bytes()
    if forced == "auto":
        forced = "fdt" if blob[:4] == struct.pack(">I", FDT_MAGIC) else "adt"
    if forced == "fdt":
        return read_fdt(blob), "fdt", True
    if forced == "adt":
        return read_adt(blob), "adt", False
    raise TreeError(f"unknown format {forced!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect an Apple device tree (ADT) or a flat device tree.")
    parser.add_argument("path", type=Path, help="tree to read")
    parser.add_argument("--json", action="store_true",
                        help="emit the full tree as JSON")
    parser.add_argument("--format", choices=("auto", "adt", "fdt"),
                        default="auto", help="input format (default: detect)")
    arguments = parser.parse_args(argv)

    try:
        root, kind, big_endian = load(arguments.path, arguments.format)
    except (OSError, TreeError) as error:
        print(f"adtinfo: {error}", file=sys.stderr)
        return 2

    try:
        if arguments.json:
            payload = {
                "format": kind,
                "big_endian": big_endian,
                "root": to_dict(root, big_endian),
            }
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(f"{arguments.path}: {kind.upper()}, "
                  f"{arguments.path.stat().st_size} bytes")
            render_text(root, big_endian)
        sys.stdout.flush()
    except BrokenPipeError:
        # A caller such as `head` closed the pipe; that is not an error.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
