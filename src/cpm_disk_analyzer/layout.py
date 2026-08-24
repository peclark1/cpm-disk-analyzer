"""Translate between physical sector order and CP/M filesystem order."""

from __future__ import annotations

from .profiles import DiskProfile


def to_filesystem_order(data: bytes | bytearray, profile: DiskProfile) -> bytes:
    """Return sectors in the logical order used by CP/M block allocation."""
    return _translate(data, profile, to_logical=True)


def from_filesystem_order(data: bytes | bytearray, profile: DiskProfile) -> bytes:
    """Return a logical CP/M stream in physical sector-number order."""
    return _translate(data, profile, to_logical=False)


def _translate(
    data: bytes | bytearray, profile: DiskProfile, *, to_logical: bool
) -> bytes:
    translation = profile.filesystem.get("sector_translation")
    if translation is None:
        return bytes(data)

    sectors_per_track = int(profile.geometry["sectors_per_track"])
    sector_size = int(profile.geometry["sector_size"])
    heads = int(profile.geometry["heads"])
    physical_ids = tuple(int(value) for value in translation)
    expected_ids = tuple(range(1, sectors_per_track + 1))
    if len(physical_ids) != sectors_per_track or tuple(sorted(physical_ids)) != expected_ids:
        raise ValueError(f"{profile.name} has an invalid sector translation table")

    track_size = heads * sectors_per_track * sector_size
    if len(data) % track_size:
        raise ValueError(
            f"image length {len(data):,} is not a whole number of {profile.name} tracks"
        )

    translated = bytearray(len(data))
    track_count = len(data) // track_size
    for track in range(track_count):
        track_offset = track * track_size
        for head in range(heads):
            head_offset = track_offset + head * sectors_per_track * sector_size
            for logical_index, physical_id in enumerate(physical_ids):
                logical_offset = head_offset + logical_index * sector_size
                physical_offset = head_offset + (physical_id - 1) * sector_size
                if to_logical:
                    source, destination = physical_offset, logical_offset
                else:
                    source, destination = logical_offset, physical_offset
                translated[destination : destination + sector_size] = data[
                    source : source + sector_size
                ]
    return bytes(translated)
