from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cpm_disk_analyzer.analyzer import analyze_image
from cpm_disk_analyzer.containers import read_image
from cpm_disk_analyzer.filesystem import (
    FilesystemError,
    cpm_filename,
    extract_logical_file,
    group_directory_entries,
    insert_files_into_raw_image,
)
from cpm_disk_analyzer.profiles import get_profile


def _empty_image(path: Path, profile_id: str = "ibm-3740") -> None:
    profile = get_profile(profile_id)
    image = bytearray(profile.image_size)
    directory_length = int(profile.filesystem["directory_entries"]) * 32
    image[
        profile.directory_offset : profile.directory_offset + directory_length
    ] = b"\xe5" * directory_length
    path.write_bytes(image)


class FilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_converts_host_names_to_cpm_83(self) -> None:
        self.assertEqual(cpm_filename("Read Me.txt"), "READ_ME.TXT")
        self.assertEqual(cpm_filename("long_filename.binary"), "LONG_FIL.BIN")
        self.assertEqual(cpm_filename("no-extension"), "NO-EXTEN")

    def test_inserts_and_extracts_small_and_multi_extent_files(self) -> None:
        image_path = self.directory / "disk.img"
        small_path = self.directory / "hello.txt"
        large_path = self.directory / "large.bin"
        _empty_image(image_path)
        small_path.write_bytes(b"Hello from the host\r\n")
        large_path.write_bytes(bytes(range(256)) * 80)

        imported = insert_files_into_raw_image(
            image_path, "ibm-3740", [small_path, large_path], user=3
        )
        self.assertEqual([item.cpm_name for item in imported], ["HELLO.TXT", "LARGE.BIN"])

        result = analyze_image(image_path, "ibm-3740")
        assert result.best_candidate is not None
        logical_files = group_directory_entries(result.best_candidate.files)
        by_name = {item.name: item for item in logical_files}
        self.assertEqual(by_name["HELLO.TXT"].user, 3)
        self.assertEqual(len(by_name["LARGE.BIN"].extents), 2)

        container = read_image(image_path)
        profile = get_profile("ibm-3740")
        small = extract_logical_file(container.logical_data, profile, by_name["HELLO.TXT"])
        large = extract_logical_file(container.logical_data, profile, by_name["LARGE.BIN"])
        self.assertTrue(small.startswith(small_path.read_bytes()))
        self.assertEqual(len(small), 128)
        self.assertEqual(large, large_path.read_bytes())

    def test_refuses_to_replace_an_existing_cpm_file(self) -> None:
        image_path = self.directory / "disk.img"
        source = self.directory / "hello.txt"
        _empty_image(image_path)
        source.write_bytes(b"first")
        insert_files_into_raw_image(image_path, "ibm-3740", [source])
        before = image_path.read_bytes()

        source.write_bytes(b"second")
        with self.assertRaisesRegex(FilesystemError, "already exists"):
            insert_files_into_raw_image(image_path, "ibm-3740", [source])
        self.assertEqual(image_path.read_bytes(), before)

    def test_accepts_printable_cpm_filename_punctuation(self) -> None:
        image_path = self.directory / "punctuation.img"
        _empty_image(image_path)
        profile = get_profile("ibm-3740")
        image = bytearray(image_path.read_bytes())
        entry = bytearray(32)
        entry[0] = 0
        entry[1:9] = b"FDC+TEST"
        entry[9:12] = b"COM"
        entry[15] = 1
        entry[16] = 2
        image[profile.directory_offset : profile.directory_offset + 32] = entry
        image[
            profile.directory_offset + 2 * 1024 :
            profile.directory_offset + 2 * 1024 + 128
        ] = b"+" * 128
        image_path.write_bytes(image)

        result = analyze_image(image_path, "ibm-3740")
        assert result.best_candidate is not None
        self.assertEqual(result.best_candidate.files[0].name, "FDC+TEST.COM")

    def test_preserves_unrecognized_slot_and_reserves_its_blocks(self) -> None:
        image_path = self.directory / "unrecognized.img"
        source = self.directory / "newfile.bin"
        _empty_image(image_path)
        profile = get_profile("ibm-3740")
        image = bytearray(image_path.read_bytes())

        slot_offset = profile.directory_offset + 12 * 32
        unrecognized = bytearray(32)
        unrecognized[0] = 0
        unrecognized[1:9] = b"VENDOR  "
        unrecognized[9:12] = b"DAT"
        unrecognized[15] = 129  # Not a normal CP/M extent record count.
        unrecognized[16] = 5
        image[slot_offset : slot_offset + 32] = unrecognized

        reserved_block_offset = profile.directory_offset + 5 * 1024
        image[reserved_block_offset : reserved_block_offset + 1024] = b"\xa5" * 1024
        image_path.write_bytes(image)
        source.write_bytes(b"N" * (5 * 1024))

        insert_files_into_raw_image(image_path, "ibm-3740", [source])
        updated = image_path.read_bytes()
        self.assertEqual(updated[slot_offset : slot_offset + 32], unrecognized)
        self.assertEqual(
            updated[reserved_block_offset : reserved_block_offset + 1024],
            b"\xa5" * 1024,
        )

        result = analyze_image(image_path, "ibm-3740")
        assert result.best_candidate is not None
        new_file = next(
            item
            for item in group_directory_entries(result.best_candidate.files)
            if item.name == "NEWFILE.BIN"
        )
        used = {
            block for extent in new_file.extents for block in extent.allocation_blocks
        }
        self.assertNotIn(5, used)

    def test_refuses_out_of_range_block_in_unrecognized_slot(self) -> None:
        image_path = self.directory / "unsafe.img"
        source = self.directory / "newfile.bin"
        _empty_image(image_path)
        profile = get_profile("ibm-3740")
        image = bytearray(image_path.read_bytes())
        entry = bytearray(32)
        entry[0] = 0
        entry[1:9] = b"VENDOR  "
        entry[9:12] = b"DAT"
        entry[15] = 129
        entry[16] = 250
        image[profile.directory_offset : profile.directory_offset + 32] = entry
        image_path.write_bytes(image)
        source.write_bytes(b"new")
        before = image_path.read_bytes()

        with self.assertRaisesRegex(FilesystemError, "out-of-range allocation"):
            insert_files_into_raw_image(image_path, "ibm-3740", [source])
        self.assertEqual(image_path.read_bytes(), before)

    def test_import_round_trip_for_each_declared_raw_profile(self) -> None:
        source = self.directory / "roundtrip.dat"
        source.write_bytes(bytes(range(251)) * 9)
        for profile_id in ("dsi-sd26", "dsi-dd58", "kaypro-ii"):
            with self.subTest(profile=profile_id):
                image_path = self.directory / f"{profile_id}.img"
                _empty_image(image_path, profile_id)
                insert_files_into_raw_image(image_path, profile_id, [source], user=7)
                result = analyze_image(image_path, profile_id)
                assert result.best_candidate is not None
                logical_file = group_directory_entries(result.best_candidate.files)[0]
                extracted = extract_logical_file(
                    read_image(image_path).logical_data,
                    get_profile(profile_id),
                    logical_file,
                )
                self.assertEqual(logical_file.user, 7)
                self.assertTrue(extracted.startswith(source.read_bytes()))

    def test_refuses_to_write_an_imd_container(self) -> None:
        image_path = self.directory / "disk.imd"
        source = self.directory / "hello.txt"
        image_path.write_bytes(b"IMD test\x1a")
        source.write_text("hello")
        with self.assertRaisesRegex(FilesystemError, "ImageDisk"):
            insert_files_into_raw_image(image_path, "ibm-3740", [source])


if __name__ == "__main__":
    unittest.main()
