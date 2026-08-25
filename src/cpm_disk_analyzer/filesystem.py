"""Read and safely update files in supported CP/M filesystem layouts."""

from __future__ import annotations

import math
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .cpm import parse_directory_entry
from .layout import from_filesystem_order, to_filesystem_order
from .models import DirectoryEntry, LogicalFile
from .profiles import DiskProfile, get_profile


class FilesystemError(ValueError):
    """Raised when a CP/M file operation cannot be completed safely."""


@dataclass(frozen=True)
class ImportPlan:
    source: Path
    cpm_name: str
    size: int
    records: int
    extents: int
    blocks: int


@dataclass(frozen=True)
class ImportedFile:
    source: Path
    cpm_name: str
    user: int
    size: int
    records: int


def group_directory_entries(entries: Iterable[DirectoryEntry]) -> list[LogicalFile]:
    grouped: dict[tuple[int, str], list[DirectoryEntry]] = {}
    for entry in entries:
        if entry.deleted:
            continue
        grouped.setdefault((entry.user, entry.name), []).append(entry)
    return [
        LogicalFile(user, name, tuple(sorted(extents, key=lambda item: item.extent)))
        for (user, name), extents in sorted(grouped.items())
    ]


def extract_logical_file(
    logical_data: bytes, profile: DiskProfile, logical_file: LogicalFile
) -> bytes:
    logical_data = to_filesystem_order(logical_data, profile)
    block_size = int(profile.filesystem["block_size"])
    output = bytearray()
    seen_extents: set[int] = set()

    for entry in sorted(logical_file.extents, key=lambda item: item.extent):
        if entry.extent in seen_extents:
            raise FilesystemError(
                f"{logical_file.name} has duplicate extent {entry.extent}"
            )
        seen_extents.add(entry.extent)
        extent_data = bytearray()
        for block in entry.allocation_blocks:
            start = profile.directory_offset + block * block_size
            end = start + block_size
            if start < 0 or end > len(logical_data):
                raise FilesystemError(
                    f"{logical_file.name} allocation block {block} is outside the image"
                )
            extent_data.extend(logical_data[start:end])
        required = entry.records * 128
        if len(extent_data) < required:
            raise FilesystemError(
                f"{logical_file.name} extent {entry.extent} does not contain "
                f"its declared {entry.records} records"
            )
        output.extend(extent_data[:required])
    return bytes(output)


def cpm_filename(host_name: str) -> str:
    """Convert a host filename to a deterministic CP/M 8.3 name."""
    name = Path(host_name).name
    base, separator, extension = name.rpartition(".")
    if not separator or not base:
        base, extension = name, ""

    def clean(component: str, limit: int) -> str:
        allowed = "!#$%&'()-@^_`{}~"
        converted = "".join(
            character if character.isascii() and (character.isalnum() or character in allowed)
            else "_"
            for character in component.upper()
        )
        return converted[:limit]

    clean_base = clean(base, 8) or "FILE"
    clean_extension = clean(extension, 3)
    return f"{clean_base}.{clean_extension}" if clean_extension else clean_base


def plan_imports(sources: Iterable[str | Path], profile: DiskProfile) -> list[ImportPlan]:
    pointer_width = int(profile.filesystem.get("allocation_pointer_bytes", 1))
    pointers_per_extent = 16 // pointer_width
    block_size = int(profile.filesystem["block_size"])
    extent_capacity = pointers_per_extent * block_size
    if extent_capacity < 128 * 128:
        raise FilesystemError("profile allocation layout cannot hold a CP/M extent")

    plans: list[ImportPlan] = []
    names: set[str] = set()
    for source_value in sources:
        source = Path(source_value)
        if not source.is_file():
            raise FilesystemError(f"not a regular file: {source}")
        name = cpm_filename(source.name)
        if name in names:
            raise FilesystemError(f"multiple dropped files map to CP/M name {name}")
        names.add(name)
        size = source.stat().st_size
        records = math.ceil(size / 128)
        extents = max(1, math.ceil(records / 128))
        blocks = math.ceil((records * 128) / block_size)
        plans.append(ImportPlan(source, name, size, records, extents, blocks))
    return plans


def insert_files_into_raw_image(
    image_path: str | Path,
    profile_id: str,
    sources: Iterable[str | Path],
    *,
    user: int = 0,
) -> list[ImportedFile]:
    """Add host files to a raw image and atomically replace the original."""
    if not 0 <= user <= 15:
        raise FilesystemError("CP/M user area must be between 0 and 15")

    path = Path(image_path)
    profile = get_profile(profile_id)
    plans = plan_imports(sources, profile)
    if not plans:
        return []

    physical_image = path.read_bytes()
    if physical_image.startswith(b"IMD "):
        raise FilesystemError("writing ImageDisk containers is not supported yet")

    entry_count = int(profile.filesystem["directory_entries"])
    directory_offset = profile.directory_offset
    directory_length = entry_count * 32
    directory_end = directory_offset + directory_length
    variable_image_size = bool(profile.filesystem.get("variable_image_size", False))
    if variable_image_size:
        if len(physical_image) < directory_end:
            raise FilesystemError(
                f"image is {len(physical_image):,} bytes; {profile.name} needs at least "
                f"{directory_end:,} bytes to contain its directory"
            )
    elif len(physical_image) != profile.image_size:
        raise FilesystemError(
            f"image size is {len(physical_image):,} bytes; {profile.name} requires "
            f"{profile.image_size:,} bytes"
        )
    image = bytearray(to_filesystem_order(physical_image, profile))

    if directory_end > len(image):
        raise FilesystemError("directory falls outside the image")
    max_block = int(profile.filesystem["max_block"])

    free_slots: list[int] = []
    existing_names: set[tuple[int, str]] = set()
    used_blocks: set[int] = set()
    recognized_entries = 0
    opaque_entries = 0
    for index in range(entry_count):
        start = directory_offset + index * 32
        raw = bytes(image[start : start + 32])
        if raw[0] == 0xE5:
            free_slots.append(index)
            continue
        if raw[0] in (0x20, 0x21):
            # Disk-label and timestamp entries contain metadata rather than
            # allocation pointers. Preserve them byte-for-byte.
            continue
        if not 0 <= raw[0] <= 31:
            opaque_entries += 1
            _reserve_opaque_entry_blocks(raw, profile, index, max_block, used_blocks)
            continue
        parsed = parse_directory_entry(raw, profile)
        if parsed is None:
            # The analyzer may encounter vendor-specific or mildly malformed
            # active entries that it cannot present as files. Preserve the
            # slot and conservatively reserve every plausible block pointer so
            # a new file can never overwrite data referenced by that entry.
            opaque_entries += 1
            _reserve_opaque_entry_blocks(raw, profile, index, max_block, used_blocks)
            continue
        entry, bad_allocations = parsed
        if bad_allocations:
            raise FilesystemError(
                f"directory slot {index} contains an out-of-range allocation block"
            )
        recognized_entries += 1
        existing_names.add((entry.user, entry.name))
        used_blocks.update(entry.allocation_blocks)

    opaque_limit = max(2, recognized_entries // 4)
    if opaque_entries > opaque_limit:
        raise FilesystemError(
            f"directory contains {opaque_entries} unrecognized entries; the selected "
            "format is not reliable enough for writing"
        )

    conflicts = [plan.cpm_name for plan in plans if (user, plan.cpm_name) in existing_names]
    if conflicts:
        raise FilesystemError(
            "file already exists in user " + str(user) + ": " + ", ".join(conflicts)
        )

    needed_slots = sum(plan.extents for plan in plans)
    if needed_slots > len(free_slots):
        raise FilesystemError(
            f"not enough directory entries: need {needed_slots}, have {len(free_slots)}"
        )

    block_size = int(profile.filesystem["block_size"])
    directory_blocks = math.ceil(directory_length / block_size)
    used_blocks.update(range(directory_blocks))
    free_blocks = [
        block for block in range(directory_blocks, max_block + 1) if block not in used_blocks
    ]
    needed_blocks = sum(plan.blocks for plan in plans)
    if needed_blocks > len(free_blocks):
        raise FilesystemError(
            f"not enough disk space: need {needed_blocks} allocation blocks, "
            f"have {len(free_blocks)}"
        )

    pointer_width = int(profile.filesystem.get("allocation_pointer_bytes", 1))
    pointers_per_extent = 16 // pointer_width
    imported: list[ImportedFile] = []
    slot_cursor = 0
    block_cursor = 0

    for plan in plans:
        source_data = plan.source.read_bytes()
        padded_data = source_data.ljust(plan.records * 128, b"\x1a")
        name, dot, extension = plan.cpm_name.partition(".")
        extension = extension if dot else ""

        for extent_number in range(plan.extents):
            extent_payload = padded_data[
                extent_number * 128 * 128 : (extent_number + 1) * 128 * 128
            ]
            extent_records = len(extent_payload) // 128
            extent_block_count = math.ceil(len(extent_payload) / block_size)
            extent_blocks = free_blocks[
                block_cursor : block_cursor + extent_block_count
            ]
            block_cursor += extent_block_count

            for payload_offset, block in enumerate(extent_blocks):
                start = directory_offset + block * block_size
                end = start + block_size
                if end > profile.image_size:
                    raise FilesystemError(f"allocation block {block} falls outside the CP/M volume")
                if end > len(image):
                    if not variable_image_size:
                        raise FilesystemError(
                            f"allocation block {block} falls outside the image"
                        )
                    image.extend(bytes(end - len(image)))
                image[start:end] = bytes(block_size)
                chunk = extent_payload[
                    payload_offset * block_size : (payload_offset + 1) * block_size
                ]
                image[start : start + len(chunk)] = chunk

            entry = bytearray(32)
            entry[0] = user
            entry[1:9] = name.encode("ascii").ljust(8, b" ")
            entry[9:12] = extension.encode("ascii").ljust(3, b" ")
            entry[12] = extent_number & 0x1F
            entry[13] = 0
            entry[14] = (extent_number >> 5) & 0x3F
            entry[15] = extent_records
            if len(extent_blocks) > pointers_per_extent:
                raise FilesystemError("internal error: too many blocks in one extent")
            for pointer_index, block in enumerate(extent_blocks):
                offset = 16 + pointer_index * pointer_width
                entry[offset : offset + pointer_width] = block.to_bytes(
                    pointer_width, "little"
                )

            directory_slot = free_slots[slot_cursor]
            slot_cursor += 1
            start = directory_offset + directory_slot * 32
            image[start : start + 32] = entry

        imported.append(ImportedFile(plan.source, plan.cpm_name, user, plan.size, plan.records))

    _atomic_replace(path, from_filesystem_order(image, profile))
    return imported


def _atomic_replace(path: Path, image: bytes | bytearray) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(image)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _allocation_blocks_from_raw(raw: bytes, profile: DiskProfile) -> tuple[int, ...]:
    pointer_width = int(profile.filesystem.get("allocation_pointer_bytes", 1))
    allocation = raw[16:32]
    if pointer_width == 2:
        blocks = (
            int.from_bytes(allocation[index : index + 2], "little")
            for index in range(0, 16, 2)
        )
    else:
        blocks = iter(allocation)
    return tuple(block for block in blocks if block)


def _reserve_opaque_entry_blocks(
    raw: bytes,
    profile: DiskProfile,
    index: int,
    max_block: int,
    used_blocks: set[int],
) -> None:
    blocks = _allocation_blocks_from_raw(raw, profile)
    bad_blocks = [block for block in blocks if block > max_block]
    if bad_blocks:
        raise FilesystemError(
            f"directory slot {index} contains out-of-range allocation block "
            f"{bad_blocks[0]}; refusing to write"
        )
    used_blocks.update(blocks)
