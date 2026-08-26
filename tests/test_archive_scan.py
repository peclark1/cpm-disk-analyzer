from pathlib import Path

from cpm_disk_analyzer.archive_scan import (
    binary_z80_evidence,
    infer_density,
    source_z80_evidence,
)


def test_infer_density_from_archive_directory_names(tmp_path: Path) -> None:
    single = tmp_path / "SingleDensity" / "disk.img"
    double = tmp_path / "DoubleDensity" / "disk.img"
    other = tmp_path / "Unknown" / "disk.img"

    assert infer_density(single, tmp_path) == "single"
    assert infer_density(double, tmp_path) == "double"
    assert infer_density(other, tmp_path) == "unknown"


def test_source_scan_reports_z80_only_instructions_but_ignores_comments() -> None:
    source = b"""\
START:  LXI H,0000H
        ; DJNZ HERE is only a comment
        LD IX,1234H
HERE:   DJNZ HERE
        LDIR
"""

    hits = source_z80_evidence(source)

    assert [hit["location"] for hit in hits] == ["line 3", "line 4", "line 5"]
    assert hits[0]["detail"] == "IX/IY register"
    assert hits[1]["instruction"].upper() == "DJNZ"


def test_binary_scan_ignores_unreachable_z80_looking_bytes() -> None:
    # 0100: JMP 0106
    # 0103: DD 21 00 00   (unreachable data that looks like LD IX,0000)
    # 0107: DJNZ 0107     (reachable Z80-only instruction)
    payload = bytes([0xC3, 0x07, 0x01, 0xDD, 0x21, 0x00, 0x00, 0x10, 0xFE])

    hits = binary_z80_evidence(payload, origin=0x0100, entry_points=[0x0100])

    assert len(hits) == 1
    assert hits[0]["location"] == "0107h"
    assert hits[0]["instruction"] == "DJNZ"


def test_binary_scan_reports_reachable_ix_prefix() -> None:
    payload = bytes([0xC3, 0x03, 0x01, 0xDD, 0x21, 0x00, 0x20])

    hits = binary_z80_evidence(payload, origin=0x0100, entry_points=[0x0100])

    assert len(hits) == 1
    assert hits[0]["location"] == "0103h"
    assert hits[0]["instruction"] == "IX (DDh)"
