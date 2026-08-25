"""Safe directory-only mutations for raw CP/M disk images."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from .cpm import parse_directory_entry
from .filesystem import FilesystemError, cpm_filename
from .layout import from_filesystem_order, to_filesystem_order
from .profiles import DiskProfile, get_profile


def validate_cpm_name(value: str) -> str:
    """Return an uppercase CP/M 8.3 name, rejecting lossy conversions."""
    candidate = value.strip().upper()
    if not candidate:
        raise FilesystemError("CP/M filename cannot be empty")
    converted = cpm_filename(candidate)
    if converted != candidate:
        raise FilesystemError(
            "Use a valid CP/M 8.3 filename (up to 8 characters, optional 3-character extension)"
        )
    return candidate


def rename_file_in_raw_image(
    image_path: str | Path,
    profile_id: str,
    *,
    user: int,
    old_name: str,
    new_name: str,
) -> int:
    """Rename every extent of one CP/M logical file."""
    path, profile, image = _load_raw_image(image_path, profile_id)
    old_name = validate_cpm_name(old_name)
    new_name = validate_cpm_name(new_name)
    if old_name == new_name:
        return 0

    slots = _matching_slots(image, profile, user, old_name)
    if not slots:
        raise FilesystemError(f"User {user} does not contain {old_name}")
    if _matching_slots(image, profile, user, new_name):
        raise FilesystemError(f"User {user} already contains {new_name}")

    base, dot, extension = new_name.partition(".")
    extension = extension if dot else ""
    replacement = base.encode("ascii").ljust(8, b" ") + extension.encode("ascii").ljust(3, b" ")
    for offset in slots:
        for index, value in enumerate(replacement, start=1):
            image[offset + index] = (image[offset + index] & 0x80) | value

    _save_raw_image(path, profile, image)
    return len(slots)


def delete_file_from_raw_image(
    image_path: str | Path,
    profile_id: str,
    *,
    user: int,
    name: str,
) -> int:
    """Mark every extent of one CP/M logical file as deleted (E5h)."""
    path, profile, image = _load_raw_image(image_path, profile_id)
    name = validate_cpm_name(name)
    slots = _matching_slots(image, profile, user, name)
    if not slots:
        raise FilesystemError(f"User {user} does not contain {name}")
    for offset in slots:
        image[offset] = 0xE5
    _save_raw_image(path, profile, image)
    return len(slots)


def set_file_attributes_in_raw_image(
    image_path: str | Path,
    profile_id: str,
    *,
    user: int,
    name: str,
    read_only: bool,
    system: bool,
    archive: bool,
) -> int:
    """Set the standard CP/M R/O, SYS, and Archive flags on every extent."""
    path, profile, image = _load_raw_image(image_path, profile_id)
    name = validate_cpm_name(name)
    slots = _matching_slots(image, profile, user, name)
    if not slots:
        raise FilesystemError(f"User {user} does not contain {name}")
    for offset in slots:
        _set_high_bit(image, offset + 9, read_only)
        _set_high_bit(image, offset + 10, system)
        _set_high_bit(image, offset + 11, archive)
    _save_raw_image(path, profile, image)
    return len(slots)


def _set_high_bit(image: bytearray, offset: int, enabled: bool) -> None:
    image[offset] = (image[offset] & 0x7F) | (0x80 if enabled else 0)


def _matching_slots(
    image: bytearray, profile: DiskProfile, user: int, name: str
) -> list[int]:
    if not 0 <= user <= 31:
        raise FilesystemError("CP/M user area must be between 0 and 31")
    slots: list[int] = []
    entry_count = int(profile.filesystem["directory_entries"])
    for index in range(entry_count):
        offset = profile.directory_offset + index * 32
        raw = bytes(image[offset : offset + 32])
        if len(raw) != 32 or not 0 <= raw[0] <= 31:
            continue
        parsed = parse_directory_entry(raw, profile)
        if parsed is None:
            continue
        entry, _bad_allocations = parsed
        if entry.user == user and entry.name == name:
            slots.append(offset)
    return slots


def _load_raw_image(
    image_path: str | Path, profile_id: str
) -> tuple[Path, DiskProfile, bytearray]:
    path = Path(image_path)
    profile = get_profile(profile_id)
    physical = path.read_bytes()
    if physical.startswith(b"IMD "):
        raise FilesystemError("writing ImageDisk containers is not supported yet")

    minimum_size = profile.directory_offset + int(profile.filesystem["directory_entries"]) * 32
    variable = bool(profile.filesystem.get("variable_image_size", False))
    if variable:
        if len(physical) < minimum_size:
            raise FilesystemError(
                f"image is too short to contain the {profile.name} directory"
            )
    elif len(physical) != profile.image_size:
        raise FilesystemError(
            f"image size is {len(physical):,} bytes; {profile.name} requires "
            f"{profile.image_size:,} bytes"
        )
    return path, profile, bytearray(to_filesystem_order(physical, profile))


def _save_raw_image(path: Path, profile: DiskProfile, image: bytearray) -> None:
    physical = from_filesystem_order(image, profile)
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(physical)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
