"""Human-readable and JSON reports."""

from __future__ import annotations

import json

from .models import CandidateResult, ImageResult


def as_json(result: ImageResult, *, indent: int = 2) -> str:
    return json.dumps(result.to_dict(), indent=indent, sort_keys=True)


def as_text(result: ImageResult, *, show_files: bool = False) -> str:
    lines = [
        "CP/M Disk Analyzer",
        f"Image:     {result.path}",
        f"SHA-256:   {result.sha256}",
        f"File size: {result.size:,} bytes",
        f"Container: {result.container.upper()}",
    ]
    if result.container_metadata:
        summary = ", ".join(
            f"{key}={value}" for key, value in result.container_metadata.items() if key != "header"
        )
        lines.append(f"Observed:  {summary}")
    lines.append("")

    if not result.candidates:
        lines.extend(
            [
                "No supported CP/M disk profile produced a credible match.",
                "This does not prove the image is not CP/M; its format may not be cataloged yet.",
            ]
        )
    else:
        lines.append("Candidate interpretations:")
        for index, candidate in enumerate(result.candidates, 1):
            lines.extend(_candidate_text(index, candidate, show_files=show_files))

    if result.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    return "\n".join(lines)


def _candidate_text(
    index: int, candidate: CandidateResult, *, show_files: bool
) -> list[str]:
    geometry = candidate.geometry
    lines = [
        "",
        f"{index}. {candidate.profile_name}",
        f"   Profile:    {candidate.profile_id}",
        f"   Confidence: {candidate.confidence} ({candidate.score}/100)",
        "   Geometry:   "
        f"{geometry['cylinders']} cylinders, {geometry['heads']} head(s), "
        f"{geometry['sectors_per_track']} x {geometry['sector_size']}-byte sectors",
        "   Evidence:",
    ]
    lines.extend(
        f"     - [{item.category}] {item.message} ({item.points:+d})"
        for item in candidate.evidence
    )
    if show_files and candidate.files:
        lines.append("   Directory extents:")
        for entry in candidate.files:
            lines.append(
                f"     {entry.user:02d}: {entry.name:<12} extent={entry.extent:<3} "
                f"records={entry.records:<3} approx={entry.estimated_size:,} bytes"
            )
    return lines

