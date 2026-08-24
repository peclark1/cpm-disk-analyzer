"""CP/M directory validation and profile scoring."""

from __future__ import annotations

from collections import Counter

from .models import CandidateResult, DirectoryEntry, Evidence
from .profiles import DiskProfile


def score_profile(data: bytes, profile: DiskProfile) -> CandidateResult:
    evidence: list[Evidence] = []
    warnings: list[str] = []
    score = 0

    if len(data) == profile.image_size:
        score += 25
        evidence.append(
            Evidence(
                "derived",
                f"Image length exactly matches {profile.image_size:,} bytes.",
                25,
            )
        )
    else:
        delta = len(data) - profile.image_size
        evidence.append(
            Evidence(
                "observed",
                f"Image length differs from this profile by {delta:+,} bytes.",
                -30,
            )
        )
        score -= 30

    offset = profile.directory_offset
    entry_count = int(profile.filesystem["directory_entries"])
    directory_length = entry_count * 32
    if offset + directory_length > len(data):
        evidence.append(Evidence("derived", "Directory region falls outside the image.", -60))
        return _candidate(profile, max(0, score - 60), evidence, [], warnings)

    directory = data[offset : offset + directory_length]
    files: list[DirectoryEntry] = []
    invalid_entries = 0
    unused_entries = 0
    deleted_entries = 0
    special_entries = 0
    allocation_errors = 0
    active_raw = 0

    for index in range(entry_count):
        raw = directory[index * 32 : (index + 1) * 32]
        status = raw[0]
        if status == 0xE5:
            unused_entries += 1
            continue
        if status in (0x20, 0x21):
            # CP/M Plus and compatible extensions use these directory slots
            # for disk labels and native timestamps, not file extents.
            special_entries += 1
            continue
        if not (0 <= status <= 31):
            invalid_entries += 1
            continue
        active_raw += 1
        parsed = parse_directory_entry(raw, profile)
        if parsed is None:
            invalid_entries += 1
            continue
        entry, bad_allocations = parsed
        files.append(entry)
        allocation_errors += bad_allocations
        if entry.deleted:
            deleted_entries += 1

    if files:
        valid_ratio = len(files) / max(1, active_raw)
        directory_points = min(45, 18 + len(files) * 2)
        if valid_ratio >= 0.9:
            directory_points += 10
        score += directory_points
        evidence.append(
            Evidence(
                "derived",
                f"Found {len(files)} plausible CP/M extent entr{'y' if len(files) == 1 else 'ies'} "
                f"({valid_ratio:.0%} of non-E5 candidate entries).",
                directory_points,
            )
        )
    elif unused_entries == entry_count:
        score += 25
        evidence.append(
            Evidence(
                "inferred",
                "Directory region is uniformly E5h, consistent with an empty formatted CP/M disk.",
                25,
            )
        )
    else:
        evidence.append(Evidence("derived", "No plausible CP/M directory entries were found.", -20))
        score -= 20

    if invalid_entries:
        penalty = min(25, invalid_entries * 2)
        score -= penalty
        evidence.append(
            Evidence(
                "derived",
                f"{invalid_entries} directory slot(s) were structurally implausible.",
                -penalty,
            )
        )
    if allocation_errors:
        penalty = min(20, allocation_errors * 2)
        score -= penalty
        evidence.append(
            Evidence(
                "derived",
                f"{allocation_errors} allocation pointer(s) exceeded the profile's data area.",
                -penalty,
            )
        )

    if files:
        grouped = Counter((entry.user, entry.name) for entry in files)
        logical_files = len(grouped)
        evidence.append(
            Evidence(
                "derived",
                f"The directory describes approximately {logical_files} logical file(s).",
                0,
            )
        )
    if deleted_entries:
        warnings.append(f"{deleted_entries} deleted directory entries were recognized.")
    if special_entries:
        evidence.append(
            Evidence(
                "observed",
                f"Preserved {special_entries} disk-label or timestamp directory "
                "entr{'y' if special_entries == 1 else 'ies'}.",
                0,
            )
        )

    for signature in profile.signatures:
        if signature.encode("ascii", errors="ignore").upper() in data.upper():
            score += 8
            evidence.append(
                Evidence("observed", f"Known text signature {signature!r} was present.", 8)
            )

    return _candidate(profile, max(0, min(100, score)), evidence, files, warnings)


def parse_directory_entry(
    raw: bytes, profile: DiskProfile
) -> tuple[DirectoryEntry, int] | None:
    cleaned = bytes(value & 0x7F for value in raw[1:12])
    name_raw, ext_raw = cleaned[:8], cleaned[8:11]
    if not _valid_component(name_raw, required=True) or not _valid_component(
        ext_raw, required=False
    ):
        return None
    name = name_raw.decode("ascii").rstrip()
    extension = ext_raw.decode("ascii").rstrip()
    full_name = f"{name}.{extension}" if extension else name
    records = raw[15]
    if records > 128:
        return None

    pointer_width = int(profile.filesystem.get("allocation_pointer_bytes", 1))
    allocation = raw[16:32]
    if pointer_width == 2:
        blocks = tuple(
            int.from_bytes(allocation[index : index + 2], "little")
            for index in range(0, 16, 2)
        )
    else:
        blocks = tuple(allocation)
    blocks = tuple(block for block in blocks if block)

    max_block = profile.filesystem.get("max_block")
    if max_block is None:
        usable = max(0, profile.image_size - profile.directory_offset)
        max_block = max(0, usable // int(profile.filesystem["block_size"]) - 1)
    bad_allocations = sum(block > int(max_block) for block in blocks)
    extent = raw[12] + ((raw[14] & 0x3F) << 5)
    return (
        DirectoryEntry(
            user=raw[0],
            name=full_name,
            extent=extent,
            records=records,
            allocation_blocks=blocks,
        ),
        bad_allocations,
    )


def _valid_component(value: bytes, *, required: bool) -> bool:
    if required and not value.strip(b" "):
        return False
    seen_space = False
    for byte in value:
        if byte == 0x20:
            seen_space = True
            continue
        if seen_space:
            return False
        if not (0x21 <= byte <= 0x7E) or byte in b"<>.,;:=?*[]":
            return False
    return True


def _candidate(
    profile: DiskProfile,
    score: int,
    evidence: list[Evidence],
    files: list[DirectoryEntry],
    warnings: list[str],
) -> CandidateResult:
    confidence = (
        "high" if score >= 80 else "moderate" if score >= 55 else "low" if score >= 30 else "unlikely"
    )
    return CandidateResult(
        profile_id=profile.id,
        profile_name=profile.name,
        score=score,
        confidence=confidence,
        geometry=dict(profile.geometry),
        filesystem=dict(profile.filesystem),
        evidence=evidence,
        files=files,
        warnings=warnings,
    )
