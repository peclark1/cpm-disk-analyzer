"""Top-level disk-image analysis orchestration."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .containers import read_image
from .cpm import score_profile
from .models import ImageResult
from .profiles import get_profile, load_profiles


def analyze_image(path: str | Path, profile_id: str | None = None) -> ImageResult:
    image_path = Path(path)
    source_data = image_path.read_bytes()
    container = read_image(image_path)
    profiles = [get_profile(profile_id)] if profile_id else load_profiles()
    candidates = [score_profile(container.logical_data, profile) for profile in profiles]
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.profile_name))

    # Automatic results should remain useful without implying that a weak size
    # coincidence is a positive CP/M identification.
    if profile_id is None:
        candidates = [candidate for candidate in candidates if candidate.score >= 25]

    observations = list(container.observations)
    observations.append(f"SHA-256 was calculated over the original {len(source_data):,}-byte file.")
    if container.kind == "imd":
        observations.append(
            f"The normalized logical sector stream is {len(container.logical_data):,} bytes."
        )

    return ImageResult(
        path=image_path,
        sha256=hashlib.sha256(source_data).hexdigest(),
        size=len(source_data),
        container=container.kind,
        container_metadata=container.metadata,
        observations=observations,
        candidates=candidates,
        warnings=container.warnings,
    )

