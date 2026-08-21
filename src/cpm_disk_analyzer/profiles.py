"""Load declarative disk-format profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any


@dataclass(frozen=True)
class DiskProfile:
    id: str
    name: str
    description: str
    geometry: dict[str, Any]
    filesystem: dict[str, Any]
    signatures: tuple[str, ...] = ()

    @property
    def image_size(self) -> int:
        return (
            int(self.geometry["cylinders"])
            * int(self.geometry["heads"])
            * int(self.geometry["sectors_per_track"])
            * int(self.geometry["sector_size"])
        )

    @property
    def directory_offset(self) -> int:
        explicit = self.filesystem.get("directory_offset")
        if explicit is not None:
            return int(explicit)
        track_size = (
            int(self.geometry["heads"])
            * int(self.geometry["sectors_per_track"])
            * int(self.geometry["sector_size"])
        )
        return int(self.filesystem.get("reserved_tracks", 0)) * track_size


def load_profiles() -> list[DiskProfile]:
    resource = files("cpm_disk_analyzer").joinpath("data/profiles.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    return [
        DiskProfile(
            id=item["id"],
            name=item["name"],
            description=item.get("description", ""),
            geometry=item["geometry"],
            filesystem=item["filesystem"],
            signatures=tuple(item.get("signatures", [])),
        )
        for item in payload
    ]


def get_profile(profile_id: str) -> DiskProfile:
    for profile in load_profiles():
        if profile.id == profile_id:
            return profile
    raise KeyError(profile_id)

