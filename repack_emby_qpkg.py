#!/usr/bin/env python3
"""Patch the supplied Emby 4.9.5.0 QNAP package and rebuild its QPKG."""

from __future__ import annotations

import argparse
import dataclasses
import io
import math
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tarfile
import tempfile
from typing import BinaryIO, Iterable

from qnap_signing import (
    SigningError,
    SigningMaterial,
    SigningOptions,
    add_signing_arguments,
    qpkg_encrypt_string,
    rewrite_qpkg_footer_checksum,
    run_checked,
    sha256_file,
    sign_qpkg_content,
    signing_options_from_args,
)


VALIDATE_DEVICE_OLD = (
    b'ajax({url:"https://mb3admin.com/admin/service/registration/'
    b'validateDevice?"+new URLSearchParams(params).toString(),type:"POST",'
    b'dataType:"json"})'
)
VALIDATE_DEVICE_NEW = (
    b"new Promise(function(resolve) {resolve('"
    b'{"cacheExpirationDays": 365,"message": "Device Valid",'
    b'"resultCode": "GOOD"}'
    b"');})"
)
PREMIERE_STATUS_OLD = (
    b'return fetch("https://mb3admin.com/admin/service/registration/getStatus",'
    b'{method:"POST",body:key,headers:{"Content-Type":'
    b'"application/x-www-form-urlencoded"}}).then(function(response)'
    b'{return response.json()})'
)
PREMIERE_STATUS_NEW = (
    b'return new Promise(function(resolve){resolve({"deviceStatus":"0",'
    b'"planType":"Lifetime","subscriptions":{}})})'
)

TARGET_WEB_DLL = "system/Emby.Web.dll"
TARGET_SERVER_DLL = "system/Emby.Server.Implementations.dll"
TARGET_MODEL_DLL = "system/MediaBrowser.Model.dll"
TARGET_CONNECTION_JS = (
    "system/dashboard-ui/modules/emby-apiclient/connectionmanager.js"
)
TARGET_PREMIERE_JS = "system/dashboard-ui/embypremiere/embypremiere.js"
TARGET_PATHS = {
    TARGET_WEB_DLL,
    TARGET_SERVER_DLL,
    TARGET_MODEL_DLL,
    TARGET_CONNECTION_JS,
    TARGET_PREMIERE_JS,
}


class PatchError(RuntimeError):
    """Raised when the package does not match the expected fixture."""


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def replace_once(
    data: bytes,
    old: bytes,
    new: bytes,
    *,
    label: str,
    pad_to_original: bool = False,
) -> bytes:
    count = data.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected source bytes exactly once, found {count}")
    if pad_to_original:
        if len(new) > len(old):
            raise PatchError(
                f"{label}: replacement is {len(new) - len(old)} bytes too large"
            )
        new = new.ljust(len(old), b" ")
    return data.replace(old, new, 1)


def constant_return_il(value: bool) -> bytes:
    return (b"\x17" if value else b"\x16") + b"\x2a"

@dataclasses.dataclass(frozen=True)
class QpkgLayout:
    script_size: int
    control_size: int
    payload_size: int
    footer_size: int

    @classmethod
    def from_header(cls, header: bytes, file_size: int) -> "QpkgLayout":
        script_match = re.search(rb"(?m)^script_len=(\d+)$", header)
        control_match = re.search(
            rb"(?m)^offset=.*\$script_len \+ (\d+)\)$", header
        )
        payload_matches = re.findall(rb"f\.seek\((\d+)\)", header)
        if not script_match or not control_match or len(payload_matches) != 1:
            raise PatchError("QPKG bootstrap layout declarations were not recognized")

        script_size = int(script_match.group(1))
        control_size = int(control_match.group(1))
        payload_size = int(payload_matches[0])
        footer_size = file_size - script_size - control_size - payload_size
        if script_size != len(header):
            raise PatchError(
                f"declared script length {script_size} differs from {len(header)}"
            )
        if footer_size < 0:
            raise PatchError("declared QPKG regions exceed the input file size")
        return cls(script_size, control_size, payload_size, footer_size)

    @classmethod
    def from_path(cls, path: Path) -> tuple["QpkgLayout", bytes]:
        file_size = path.stat().st_size
        with path.open("rb") as stream:
            probe = stream.read(min(file_size, 65536))
            script_match = re.search(rb"(?m)^script_len=(\d+)$", probe)
            if not script_match:
                raise PatchError("script_len was not found in the QPKG bootstrap")
            script_size = int(script_match.group(1))
            stream.seek(0)
            header = stream.read(script_size)
        return cls.from_header(header, file_size), header


def rewrite_header_payload_size(header: bytes, payload_size: int) -> bytes:
    old_match = re.search(rb"f\.seek\((\d+)\)", header)
    count_match = re.search(rb"(?m)(bs=1024 count=)(\d+)", header)
    if not old_match or not count_match:
        raise PatchError("payload size declarations were not found in the header")

    old_size = old_match.group(1)
    new_size = str(payload_size).encode("ascii")
    old_count = count_match.group(2)
    new_count = str(math.ceil(payload_size / 1024)).encode("ascii")
    if len(new_size) != len(old_size) or len(new_count) != len(old_count):
        raise PatchError(
            "new payload size changes bootstrap width; this package needs a rebuilt "
            "bootstrap instead of an in-place header rewrite"
        )
    if header.count(old_size) != 2:
        raise PatchError("expected the old payload size twice in the bootstrap")
    rewritten = header.replace(old_size, new_size)
    rewritten = rewritten[: count_match.start(2)] + new_count + rewritten[count_match.end(2) :]
    if len(rewritten) != len(header):
        raise PatchError("bootstrap length changed during payload size rewrite")
    return rewritten


@dataclasses.dataclass
class PeSection:
    name: str
    header_offset: int
    virtual_size: int
    virtual_address: int
    raw_size: int
    raw_offset: int


@dataclasses.dataclass(frozen=True)
class MethodDef:
    row_offset: int
    rva: int
    name: str


class DotNetImage:
    """Small PE/CLI metadata editor for the fixed MethodDef patches used here."""

    CODED_INDEXES = {
        "ResolutionScope": (2, (0, 26, 35, 1)),
        "TypeDefOrRef": (2, (2, 1, 27)),
        "MemberRefParent": (3, (2, 1, 26, 6, 27)),
    }

    def __init__(self, data: bytes, label: str) -> None:
        self.data = bytearray(data)
        self.label = label
        self._parse_pe()
        self._parse_metadata()

    def _u16(self, offset: int) -> int:
        return struct.unpack_from("<H", self.data, offset)[0]

    def _u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.data, offset)[0]

    def _u64(self, offset: int) -> int:
        return struct.unpack_from("<Q", self.data, offset)[0]

    def _parse_pe(self) -> None:
        if self.data[:2] != b"MZ":
            raise PatchError(f"{self.label}: missing DOS header")
        pe_offset = self._u32(0x3C)
        if self.data[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise PatchError(f"{self.label}: missing PE header")
        coff = pe_offset + 4
        section_count = self._u16(coff + 2)
        optional_size = self._u16(coff + 16)
        self.optional_offset = coff + 20
        magic = self._u16(self.optional_offset)
        if magic == 0x10B:
            directory_offset = self.optional_offset + 96
        elif magic == 0x20B:
            directory_offset = self.optional_offset + 112
        else:
            raise PatchError(f"{self.label}: unsupported PE optional header")

        cli_rva = self._u32(directory_offset + 14 * 8)
        cli_size = self._u32(directory_offset + 14 * 8 + 4)
        if not cli_rva or cli_size < 72:
            raise PatchError(f"{self.label}: missing CLI header")

        section_offset = self.optional_offset + optional_size
        self.sections: list[PeSection] = []
        for index in range(section_count):
            offset = section_offset + index * 40
            name = bytes(self.data[offset : offset + 8]).rstrip(b"\0").decode(
                "ascii", "replace"
            )
            self.sections.append(
                PeSection(
                    name=name,
                    header_offset=offset,
                    virtual_size=self._u32(offset + 8),
                    virtual_address=self._u32(offset + 12),
                    raw_size=self._u32(offset + 16),
                    raw_offset=self._u32(offset + 20),
                )
            )
        self.cli_offset = self.rva_to_offset(cli_rva)
        self.metadata_offset = self.rva_to_offset(self._u32(self.cli_offset + 8))

    def rva_to_offset(self, rva: int) -> int:
        for section in self.sections:
            extent = max(section.virtual_size, section.raw_size)
            if section.virtual_address <= rva < section.virtual_address + extent:
                return section.raw_offset + rva - section.virtual_address
        raise PatchError(f"{self.label}: RVA 0x{rva:x} is outside PE sections")

    def _parse_metadata(self) -> None:
        root = self.metadata_offset
        if bytes(self.data[root : root + 4]) != b"BSJB":
            raise PatchError(f"{self.label}: invalid CLI metadata signature")
        version_size = self._u32(root + 12)
        cursor = align_up(root + 16 + version_size, 4)
        stream_count = self._u16(cursor + 2)
        cursor += 4
        streams: dict[str, tuple[int, int]] = {}
        for _ in range(stream_count):
            relative_offset = self._u32(cursor)
            size = self._u32(cursor + 4)
            name_start = cursor + 8
            name_end = self.data.index(0, name_start)
            name = bytes(self.data[name_start:name_end]).decode("ascii")
            cursor = align_up(name_end + 1, 4)
            streams[name] = (root + relative_offset, size)

        table_stream = streams.get("#~") or streams.get("#-")
        strings_stream = streams.get("#Strings")
        blob_stream = streams.get("#Blob")
        if not table_stream or not strings_stream or not blob_stream:
            raise PatchError(f"{self.label}: required CLI metadata streams are missing")
        self.tables_offset = table_stream[0]
        self.strings_offset = strings_stream[0]
        self.strings_size = strings_stream[1]
        tables = self.tables_offset
        self.heap_sizes = self.data[tables + 6]
        valid = self._u64(tables + 8)
        cursor = tables + 24
        self.row_counts = [0] * 64
        for table_id in range(64):
            if valid & (1 << table_id):
                self.row_counts[table_id] = self._u32(cursor)
                cursor += 4
        self.table_offsets: dict[int, int] = {}
        for table_id in range(11):
            rows = self.row_counts[table_id]
            if rows:
                self.table_offsets[table_id] = cursor
                cursor += rows * self._row_size(table_id)

    def _heap_index_size(self, heap: str) -> int:
        bit = {"string": 0x01, "guid": 0x02, "blob": 0x04}[heap]
        return 4 if self.heap_sizes & bit else 2

    def _table_index_size(self, table_id: int) -> int:
        return 4 if self.row_counts[table_id] >= 65536 else 2

    def _coded_index_size(self, name: str) -> int:
        tag_bits, tables = self.CODED_INDEXES[name]
        largest = max(self.row_counts[table] for table in tables)
        return 4 if largest >= (1 << (16 - tag_bits)) else 2

    def _row_size(self, table_id: int) -> int:
        string = self._heap_index_size("string")
        guid = self._heap_index_size("guid")
        blob = self._heap_index_size("blob")
        sizes = {
            0: 2 + string + 3 * guid,
            1: self._coded_index_size("ResolutionScope") + 2 * string,
            2: 4
            + 2 * string
            + self._coded_index_size("TypeDefOrRef")
            + self._table_index_size(4)
            + self._table_index_size(6),
            3: self._table_index_size(4),
            4: 2 + string + blob,
            5: self._table_index_size(6),
            6: 8 + string + blob + self._table_index_size(8),
            7: self._table_index_size(8),
            8: 4 + string,
            9: self._table_index_size(2) + self._coded_index_size("TypeDefOrRef"),
            10: self._coded_index_size("MemberRefParent") + string + blob,
        }
        if table_id not in sizes:
            raise PatchError(
                f"{self.label}: metadata table {table_id} precedes a required table "
                "but its row format is not implemented"
            )
        return sizes[table_id]

    def _read_index(self, offset: int, size: int) -> int:
        if size == 2:
            return self._u16(offset)
        return self._u32(offset)

    def _string(self, index: int) -> str:
        if index >= self.strings_size:
            raise PatchError(f"{self.label}: invalid #Strings index {index}")
        start = self.strings_offset + index
        end = self.data.index(0, start)
        return bytes(self.data[start:end]).decode("utf-8", "replace")

    def _row_offset(self, table_id: int, rid: int) -> int:
        if rid < 1 or rid > self.row_counts[table_id]:
            raise PatchError(
                f"{self.label}: metadata token RID {rid} is outside table {table_id}"
            )
        return self.table_offsets[table_id] + (rid - 1) * self._row_size(table_id)

    def method_def(self, token: int) -> MethodDef:
        if token >> 24 != 0x06:
            raise PatchError(f"{self.label}: token 0x{token:08x} is not a MethodDef")
        rid = token & 0x00FFFFFF
        row = self._row_offset(6, rid)
        string_size = self._heap_index_size("string")
        name_index = self._read_index(row + 8, string_size)
        return MethodDef(row, self._u32(row), self._string(name_index))

    def _type_name(self, table_id: int, rid: int) -> str:
        if table_id not in (1, 2):
            return ""
        string_size = self._heap_index_size("string")
        row = self._row_offset(table_id, rid)
        if table_id == 1:
            cursor = row + self._coded_index_size("ResolutionScope")
        else:
            cursor = row + 4
        name = self._string(self._read_index(cursor, string_size))
        namespace = self._string(self._read_index(cursor + string_size, string_size))
        return f"{namespace}.{name}" if namespace else name

    def method_token_by_type(self, type_name: str, method_name: str) -> int:
        string_size = self._heap_index_size("string")
        method_index_size = self._table_index_size(6)
        type_rows = self.row_counts[2]
        for type_rid in range(1, type_rows + 1):
            row = self._row_offset(2, type_rid)
            if self._type_name(2, type_rid) != type_name:
                continue
            method_cursor = (
                row
                + 4
                + 2 * string_size
                + self._coded_index_size("TypeDefOrRef")
                + self._table_index_size(4)
            )
            first_method = self._read_index(method_cursor, method_index_size)
            if type_rid < type_rows:
                next_row = self._row_offset(2, type_rid + 1)
                next_cursor = (
                    next_row
                    + 4
                    + 2 * string_size
                    + self._coded_index_size("TypeDefOrRef")
                    + self._table_index_size(4)
                )
                final_method = self._read_index(next_cursor, method_index_size)
            else:
                final_method = self.row_counts[6] + 1
            matches = []
            for method_rid in range(first_method, final_method):
                token = 0x06000000 | method_rid
                if self.method_def(token).name == method_name:
                    matches.append(token)
            if len(matches) != 1:
                raise PatchError(
                    f"{self.label}: expected one {type_name}.{method_name}, "
                    f"found {len(matches)}"
                )
            return matches[0]
        raise PatchError(f"{self.label}: type {type_name} was not found")

    def member_ref_token(self, member_name: str, parent_name: str) -> int:
        parent_size = self._coded_index_size("MemberRefParent")
        string_size = self._heap_index_size("string")
        tag_bits, parent_tables = self.CODED_INDEXES["MemberRefParent"]
        matches: list[int] = []
        for rid in range(1, self.row_counts[10] + 1):
            row = self._row_offset(10, rid)
            coded_parent = self._read_index(row, parent_size)
            name_index = self._read_index(row + parent_size, string_size)
            if self._string(name_index) != member_name:
                continue
            tag = coded_parent & ((1 << tag_bits) - 1)
            parent_rid = coded_parent >> tag_bits
            if tag >= len(parent_tables):
                continue
            if self._type_name(parent_tables[tag], parent_rid) == parent_name:
                matches.append(0x0A000000 | rid)
        if len(matches) != 1:
            raise PatchError(
                f"{self.label}: expected one {parent_name}.{member_name} MemberRef, "
                f"found {len(matches)}"
            )
        return matches[0]

    def _method_code(self, method: MethodDef) -> tuple[int, int]:
        body = self.rva_to_offset(method.rva)
        first = self.data[body]
        if first & 0x03 == 0x02:
            return body + 1, first >> 2
        if first & 0x03 == 0x03:
            header_size = ((self._u16(body) >> 12) & 0xF) * 4
            return body + header_size, self._u32(body + 4)
        raise PatchError(f"{self.label}: unsupported method header for {method.name}")

    def patch_bool_getter(self, token: int, expected_name: str, value: bool) -> None:
        method = self.method_def(token)
        if method.name != expected_name:
            raise PatchError(
                f"{self.label}: token 0x{token:08x} is {method.name}, "
                f"expected {expected_name}"
            )
        code_offset, code_size = self._method_code(method)
        replacement = constant_return_il(value)
        if code_size < len(replacement):
            raise PatchError(f"{self.label}: {method.name} method body is too small")
        self.data[code_offset : code_offset + code_size] = replacement.ljust(
            code_size, b"\x00"
        )

    def relocate_expiration_getter(self, token: int, expected_name: str) -> None:
        method = self.method_def(token)
        if method.name != expected_name:
            raise PatchError(
                f"{self.label}: token 0x{token:08x} is {method.name}, "
                f"expected {expected_name}"
            )
        from_ticks = self.member_ref_token("FromTicks", "System.TimeSpan")
        constructor = self.member_ref_token(".ctor", "System.DateTimeOffset")
        il = (
            b"\x20"
            + struct.pack("<i", 2099)
            + b"\x1f\x0c"
            + b"\x1f\x1f"
            + b"\x16\x16\x16"
            + b"\x16\x6a\x28"
            + struct.pack("<I", from_ticks)
            + b"\x73"
            + struct.pack("<I", constructor)
            + b"\x2a"
        )
        if len(il) > 63:
            raise AssertionError("tiny CIL body limit exceeded")
        body = bytes([(len(il) << 2) | 0x02]) + il

        candidates = [section for section in self.sections if section.name == ".text"]
        if len(candidates) != 1:
            raise PatchError(f"{self.label}: expected one .text section")
        section = candidates[0]
        relative = align_up(section.virtual_size, 4)
        end = relative + len(body)
        if end > section.raw_size:
            raise PatchError(f"{self.label}: .text has no room for relocated method")
        file_offset = section.raw_offset + relative
        if any(self.data[file_offset : section.raw_offset + end]):
            raise PatchError(f"{self.label}: .text padding is not empty")
        self.data[file_offset : file_offset + len(body)] = body
        struct.pack_into("<I", self.data, section.header_offset + 8, end)
        new_rva = section.virtual_address + relative
        struct.pack_into("<I", self.data, method.row_offset, new_rva)
        section.virtual_size = end

    def strip_strong_name_signature(self) -> None:
        signature_rva = self._u32(self.cli_offset + 32)
        signature_size = self._u32(self.cli_offset + 36)
        if signature_rva and signature_size:
            signature_offset = self.rva_to_offset(signature_rva)
            self.data[signature_offset : signature_offset + signature_size] = (
                b"\0" * signature_size
            )
            flags = self._u32(self.cli_offset + 16) & ~0x00000008
            struct.pack_into("<I", self.data, self.cli_offset + 16, flags)
        struct.pack_into("<I", self.data, self.optional_offset + 64, 0)

    def bytes(self) -> bytes:
        return bytes(self.data)


def patch_web_dll(data: bytes) -> bytes:
    patched = replace_once(
        data,
        VALIDATE_DEVICE_OLD,
        VALIDATE_DEVICE_NEW,
        label=TARGET_WEB_DLL,
        pad_to_original=True,
    )
    image = DotNetImage(patched, TARGET_WEB_DLL)
    image.strip_strong_name_signature()
    return image.bytes()


def patch_server_dll(data: bytes) -> bytes:
    image = DotNetImage(data, TARGET_SERVER_DLL)
    type_name = "Emby.Server.Implementations.Security.RegRecord"
    registered = image.method_token_by_type(type_name, "get_registered")
    expiration = image.method_token_by_type(type_name, "get_expDate")
    trial = image.method_token_by_type(type_name, "get_isTrial")
    image.patch_bool_getter(registered, "get_registered", True)
    image.relocate_expiration_getter(expiration, "get_expDate")
    image.patch_bool_getter(trial, "get_isTrial", False)
    image.strip_strong_name_signature()
    return image.bytes()


def patch_model_dll(data: bytes) -> bytes:
    image = DotNetImage(data, TARGET_MODEL_DLL)
    token = image.method_token_by_type(
        "MediaBrowser.Model.Entities.PluginSecurityInfo", "get_IsMBSupporter"
    )
    image.patch_bool_getter(token, "get_IsMBSupporter", True)
    image.strip_strong_name_signature()
    return image.bytes()


def patch_target(path: str, data: bytes) -> bytes:
    if path == TARGET_WEB_DLL:
        return patch_web_dll(data)
    if path == TARGET_SERVER_DLL:
        return patch_server_dll(data)
    if path == TARGET_MODEL_DLL:
        return patch_model_dll(data)
    if path == TARGET_CONNECTION_JS:
        return replace_once(
            data,
            VALIDATE_DEVICE_OLD,
            VALIDATE_DEVICE_NEW,
            label=path,
        )
    if path == TARGET_PREMIERE_JS:
        return replace_once(
            data,
            PREMIERE_STATUS_OLD,
            PREMIERE_STATUS_NEW,
            label=path,
        )
    raise PatchError(f"unexpected patch target: {path}")


def normalized_tar_path(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name


def rewrite_data_tar(source: Path, destination: Path) -> dict[str, tuple[int, int]]:
    results: dict[str, tuple[int, int]] = {}
    with tarfile.open(source, "r:") as input_tar, tarfile.open(
        destination, "w:", format=tarfile.GNU_FORMAT
    ) as output_tar:
        for member in input_tar:
            normalized = normalized_tar_path(member.name)
            if not member.isfile():
                output_tar.addfile(member)
                continue
            extracted = input_tar.extractfile(member)
            if extracted is None:
                raise PatchError(f"could not read tar member {member.name}")
            if normalized in TARGET_PATHS:
                original = extracted.read()
                patched = patch_target(normalized, original)
                member.size = len(patched)
                output_tar.addfile(member, io.BytesIO(patched))
                results[normalized] = (len(original), len(patched))
            else:
                output_tar.addfile(member, extracted)
    missing = TARGET_PATHS.difference(results)
    if missing:
        raise PatchError(f"missing target files in data tar: {', '.join(sorted(missing))}")
    os.utime(destination, (source.stat().st_mtime, source.stat().st_mtime))
    return results


def copy_region(
    source: BinaryIO, destination: BinaryIO, offset: int, size: int
) -> None:
    source.seek(offset)
    remaining = size
    while remaining:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise PatchError("unexpected end of QPKG while copying a region")
        destination.write(chunk)
        remaining -= len(chunk)


def extract_qpkg_regions(
    source: Path, layout: QpkgLayout, work_dir: Path
) -> tuple[Path, bytes, bytes]:
    archive = work_dir / "data.tar.7z"
    with source.open("rb") as input_stream, archive.open("wb") as output_stream:
        copy_region(
            input_stream,
            output_stream,
            layout.script_size + layout.control_size,
            layout.payload_size,
        )
        input_stream.seek(layout.script_size)
        control = input_stream.read(layout.control_size)
        if layout.footer_size < 100:
            raise PatchError("QPKG trailing area is shorter than its footer")
        input_stream.seek(source.stat().st_size - 100)
        footer = input_stream.read(100)
    if not footer.endswith(b"QNAPQPKG  "):
        raise PatchError("QPKG footer marker was not recognized")
    return archive, control, footer


def rebuild_package(
    source: Path,
    output: Path,
    seven_zip: str,
    work_dir: Path,
    signing_options: SigningOptions | None,
) -> tuple[dict[str, tuple[int, int]], SigningMaterial | None]:
    layout, header = QpkgLayout.from_path(source)
    archive, control, footer = extract_qpkg_regions(source, layout, work_dir)
    run_checked([seven_zip, "t", str(archive)])
    run_checked([seven_zip, "x", "-y", f"-o{work_dir}", str(archive)])
    data_tar = work_dir / "data.tar"
    if not data_tar.is_file():
        raise PatchError("7z payload did not contain data.tar")

    patched_tar = work_dir / "patched" / "data.tar"
    patched_tar.parent.mkdir(parents=True, exist_ok=True)
    results = rewrite_data_tar(data_tar, patched_tar)
    patched_archive = work_dir / "patched-data.tar.7z"
    run_checked(
        [
            seven_zip,
            "a",
            "-t7z",
            "-m0=lzma2",
            "-mx=9",
            "-md=32m",
            "-ms=off",
            str(patched_archive),
            "data.tar",
        ],
        cwd=patched_tar.parent,
    )
    run_checked([seven_zip, "t", str(patched_archive)])

    payload_size = patched_archive.stat().st_size
    new_header = rewrite_header_payload_size(header, payload_size)
    content_path = work_dir / "qpkg-content.bin"
    with content_path.open("wb") as content:
        content.write(new_header)
        content.write(control)
        with patched_archive.open("rb") as payload:
            shutil.copyfileobj(payload, content, 1024 * 1024)
    if signing_options:
        qdk_area, signing_material = sign_qpkg_content(
            content_path, signing_options, work_dir / "signing"
        )
        print(f"signature")
    else:
        qdk_area, signing_material = b"", None
    total_size = content_path.stat().st_size + len(qdk_area) + len(footer)
    new_footer = rewrite_qpkg_footer_checksum(footer, total_size)
    temp_output = output.with_name(output.name + ".tmp")
    with temp_output.open("wb") as destination:
        with content_path.open("rb") as content:
            shutil.copyfileobj(content, destination, 1024 * 1024)
        destination.write(qdk_area)
        destination.write(new_footer)
    os.chmod(temp_output, source.stat().st_mode & 0o777)
    os.replace(temp_output, output)

    output_layout, _ = QpkgLayout.from_path(output)
    if output_layout.footer_size != len(qdk_area) + 100:
        raise PatchError("rebuilt QPKG signature/footer length is invalid")
    if new_footer[40:50] != qpkg_encrypt_string(output.stat().st_size):
        raise PatchError("rebuilt QPKG footer checksum is invalid")
    return results, signing_material


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Patch Emby 4.9.5.0 x86_64 for QNAP and rebuild the QPKG."
    )
    parser.add_argument("input", type=Path, help="source .qpkg path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output path (default: INPUT_patched.qpkg)",
    )
    parser.add_argument(
        "--seven-zip",
        default=shutil.which("7z") or "7z",
        help="7z executable path",
    )
    parser.add_argument("--work-dir", type=Path, help="persistent work directory")
    parser.add_argument("--force", action="store_true", help="overwrite output")
    add_signing_arguments(parser)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.input.expanduser().resolve()
    if not source.is_file():
        raise PatchError(f"input does not exist: {source}")
    output = (
        args.output.expanduser().resolve()
        if args.output
        else source.with_name(f"{source.stem}_patched{source.suffix}")
    )
    if output == source:
        raise PatchError("output must differ from input")
    if output.exists() and not args.force:
        raise PatchError(f"output already exists (use --force): {output}")
    if not shutil.which(args.seven_zip) and not Path(args.seven_zip).is_file():
        raise PatchError(f"7z executable not found: {args.seven_zip}")
    output.parent.mkdir(parents=True, exist_ok=True)
    signing_options = signing_options_from_args(args, output)

    print(f"Input:  {source}")
    print(f"SHA256: {sha256_file(source)}")
    if args.work_dir:
        work_dir = args.work_dir.expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        results, signing_material = rebuild_package(
            source, output, args.seven_zip, work_dir, signing_options
        )
    else:
        with tempfile.TemporaryDirectory(prefix="emby-qpkg-") as temporary:
            results, signing_material = rebuild_package(
                source, output, args.seven_zip, Path(temporary), signing_options
            )

    print("Patched targets:")
    for path, (old_size, new_size) in sorted(results.items()):
        print(f"  {path}: {old_size} -> {new_size} bytes")
    print(f"Output: {output}")
    if signing_material:
        print(f"Signer: {signing_material.certificate}")
    print(f"SHA256: {sha256_file(output)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PatchError, SigningError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
