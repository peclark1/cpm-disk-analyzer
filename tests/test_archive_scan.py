from pathlib import Path

from cpm_disk_analyzer.archive_scan import (
    binary_z80_evidence,
    infer_density,
    scan_archive,
    source_z80_evidence,
    write_scan_csv,
    write_scan_json,
)
from cpm_disk_analyzer.layout import from_filesystem_order
from cpm_disk_analyzer.profiles import get_profile


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
    # 0100: JMP 0107
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


def test_recursive_archive_scan_extracts_files_and_preserves_density_bucket(tmp_path: Path) -> None:
    profile = get_profile("ibm-3740")
    archive = tmp_path / "archive"
    single = archive / "SingleDensity"
    single.mkdir(parents=True)
    image_path = single / "z80-test.img"

    image = bytearray(profile.image_size)
    directory_offset = profile.directory_offset
    directory_length = int(profile.filesystem["directory_entries"]) * 32
    image[directory_offset : directory_offset + directory_length] = b"\xe5" * directory_length

    entry = bytearray(32)
    entry[0] = 0
    entry[1:9] = b"Z80TEST "
    entry[9:12] = b"COM"
    entry[15] = 1
    entry[16] = 2
    image[directory_offset : directory_offset + 32] = entry

    block_size = int(profile.filesystem["block_size"])
    payload_offset = directory_offset + 2 * block_size
    payload = bytes([0xC3, 0x03, 0x01, 0xDD, 0x21, 0x00, 0x20]).ljust(128, b"\x00")
    image[payload_offset : payload_offset + len(payload)] = payload
    image_path.write_bytes(from_filesystem_order(image, profile))

    report = scan_archive(archive)

    assert report["summary"]["images_seen"] == 1
    assert report["summary"]["images_recognized"] == 1
    assert report["summary"]["z80_files"] == 1
    assert report["images"][0]["density_bucket"] == "single"
    assert report["files"][0]["name"] == "Z80TEST.COM"
    assert report["files"][0]["z80_evidence"][0]["instruction"] == "IX (DDh)"

    csv_path = tmp_path / "inventory.csv"
    json_path = tmp_path / "inventory.json"
    write_scan_csv(report, csv_path)
    write_scan_json(report, json_path)
    assert "Z80TEST.COM" in csv_path.read_text(encoding="utf-8")
    assert '"density_bucket": "single"' in json_path.read_text(encoding="utf-8")
