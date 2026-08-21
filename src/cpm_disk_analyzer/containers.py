"""Read disk-image containers into a normalized logical sector stream."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ImageFormatError(ValueError):
    """Raised when a recognized container is malformed or unsupported."""


@dataclass
class ContainerImage:
    kind: str
    logical_data: bytes
    metadata: dict[str, Any] = field(default_factory=dict)
    observations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImdSector:
    cylinder: int
    head: int
    sector_id: int
    size: int
    data: bytes
    status: int


def read_image(path: str | Path) -> ContainerImage:
    image_path = Path(path)
    data = image_path.read_bytes()
    if data.startswith(b"IMD "):
        return _read_imd(data)
    return ContainerImage(
        kind="raw",
        logical_data=data,
        metadata={"bytes": len(data)},
        observations=[
            "Raw sector image inferred because no recognized container signature was present."
        ],
    )


def _read_imd(data: bytes) -> ContainerImage:
    header_end = data.find(b"\x1a")
    if header_end < 0:
        raise ImageFormatError("IMD header terminator (1Ah) was not found")

    header = data[:header_end].decode("ascii", errors="replace").strip()
    position = header_end + 1
    sectors: list[ImdSector] = []
    modes: set[int] = set()
    track_keys: set[tuple[int, int]] = set()
    unavailable = 0

    while position < len(data):
        if len(data) - position < 5:
            raise ImageFormatError("truncated IMD track header")
        mode, cylinder, head_flags, sector_count, size_code = data[position : position + 5]
        position += 5
        if mode > 5:
            raise ImageFormatError(f"unsupported IMD mode {mode}")
        modes.add(mode)
        head = head_flags & 0x01
        track_keys.add((cylinder, head))

        sector_ids, position = _take(data, position, sector_count, "sector numbering map")
        cylinder_map = [cylinder] * sector_count
        head_map = [head] * sector_count
        if head_flags & 0x80:
            raw, position = _take(data, position, sector_count, "cylinder map")
            cylinder_map = list(raw)
        if head_flags & 0x40:
            raw, position = _take(data, position, sector_count, "head map")
            head_map = [item & 0x01 for item in raw]

        if size_code == 0xFF:
            raw_sizes, position = _take(
                data, position, sector_count * 2, "sector size map"
            )
            sizes = [
                int.from_bytes(raw_sizes[index : index + 2], "little")
                for index in range(0, len(raw_sizes), 2)
            ]
        elif size_code <= 6:
            sizes = [128 << size_code] * sector_count
        else:
            raise ImageFormatError(f"unsupported IMD sector size code {size_code}")

        for index in range(sector_count):
            status_raw, position = _take(data, position, 1, "sector status")
            status = status_raw[0]
            sector_size = sizes[index]
            if status == 0:
                payload = bytes(sector_size)
                unavailable += 1
            elif status in (1, 3, 5, 7):
                payload, position = _take(data, position, sector_size, "sector data")
            elif status in (2, 4, 6, 8):
                fill, position = _take(data, position, 1, "compressed sector byte")
                payload = fill * sector_size
            else:
                raise ImageFormatError(f"unsupported IMD sector status {status}")
            sectors.append(
                ImdSector(
                    cylinder=cylinder_map[index],
                    head=head_map[index],
                    sector_id=sector_ids[index],
                    size=sector_size,
                    data=bytes(payload),
                    status=status,
                )
            )

    # IMD stores sectors in physical order. Sorting by their logical IDs removes
    # interleave/skew so the filesystem detector receives a linear image.
    sectors.sort(key=lambda sector: (sector.cylinder, sector.head, sector.sector_id))
    logical_data = b"".join(sector.data for sector in sectors)
    sizes = sorted({sector.size for sector in sectors})
    ids_by_track: dict[tuple[int, int], list[int]] = {}
    for sector in sectors:
        ids_by_track.setdefault((sector.cylinder, sector.head), []).append(sector.sector_id)

    warnings: list[str] = []
    if unavailable:
        warnings.append(
            f"{unavailable} unavailable sector(s) were represented as zero-filled data."
        )
    metadata = {
        "header": header,
        "tracks": len(track_keys),
        "cylinders": max((key[0] for key in track_keys), default=-1) + 1,
        "heads": max((key[1] for key in track_keys), default=-1) + 1,
        "sectors": len(sectors),
        "sector_sizes": sizes,
        "modes": sorted(modes),
        "mode_descriptions": [_imd_mode_name(mode) for mode in sorted(modes)],
        "unavailable_sectors": unavailable,
        "sectors_per_track": sorted({len(value) for value in ids_by_track.values()}),
    }
    return ContainerImage(
        kind="imd",
        logical_data=logical_data,
        metadata=metadata,
        observations=[
            "ImageDisk container signature and track records were observed.",
            "Geometry and recording modes were read from the IMD container.",
            "Sectors were normalized into cylinder/head/logical-sector order.",
        ],
        warnings=warnings,
    )


def _take(data: bytes, position: int, length: int, label: str) -> tuple[bytes, int]:
    end = position + length
    if end > len(data):
        raise ImageFormatError(f"truncated IMD {label}")
    return data[position:end], end


def _imd_mode_name(mode: int) -> str:
    return {
        0: "500 kbps FM",
        1: "300 kbps FM",
        2: "250 kbps FM",
        3: "500 kbps MFM",
        4: "300 kbps MFM",
        5: "250 kbps MFM",
    }[mode]

