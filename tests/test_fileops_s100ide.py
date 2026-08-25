from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cpm_disk_analyzer.analyzer import analyze_image
from cpm_disk_analyzer.containers import read_image
from cpm_disk_analyzer.fileops import (
    delete_file_from_raw_image,
    rename_file_in_raw_image,
    set_file_attributes_in_raw_image,
)
from cpm_disk_analyzer.filesystem import (
    FilesystemError,
    extract_logical_file,
    group_directory_entries,
    insert_files_into_raw_image,
)
from cpm_disk_analyzer.layout import from_filesystem_order
from cpm_disk_analyzer.profiles import get_profile


def _empty_image(path: Path, profile_id: str) -> None:
    profile = get_profile(profile_id)
    image = bytearray(profile.image_size)
    directory_length = int(profile.filesystem["directory_entries"]) * 32
    image[
        profile.directory_offset : profile.directory_offset + directory_length
    ] = b"\xe5" * directory_length
    path.write_bytes(from_filesystem_order(image, profile))


def _short_s100_image(path: Path) -> None:
    profile = get_profile("s100ide")
    directory_length = int(profile.filesystem["directory_entries"]) * 32
    directory_end = profile.directory_offset + directory_length
    image = bytearray(directory_end)
    image[profile.directory_offset:directory_end] = b"\xe5" * directory_length
    path.write_bytes(image)


class FileOperationsAndS100IdeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_rename_attributes_and_delete_all_extents(self) -> None:
        image_path = self.directory / "disk.img"
        source = self.directory / "original.bin"
        _empty_image(image_path, "ibm-3740")
        payload = bytes(range(256)) * 80
        source.write_bytes(payload)
        insert_files_into_raw_image(image_path, "ibm-3740", [source], user=3)

        result = analyze_image(image_path, "ibm-3740")
        assert result.best_candidate is not None
        original = group_directory_entries(result.best_candidate.files)[0]
        self.assertEqual(original.name, "ORIGINAL.BIN")
        self.assertEqual(len(original.extents), 2)

        changed = rename_file_in_raw_image(
            image_path,
            "ibm-3740",
            user=3,
            old_name="ORIGINAL.BIN",
            new_name="RENAMED.BIN",
        )
        self.assertEqual(changed, 2)
        changed = set_file_attributes_in_raw_image(
            image_path,
            "ibm-3740",
            user=3,
            name="RENAMED.BIN",
            read_only=True,
            system=True,
            archive=True,
        )
        self.assertEqual(changed, 2)

        result = analyze_image(image_path, "ibm-3740")
        assert result.best_candidate is not None
        renamed = group_directory_entries(result.best_candidate.files)[0]
        self.assertEqual(renamed.name, "RENAMED.BIN")
        self.assertTrue(renamed.read_only)
        self.assertTrue(renamed.system)
        self.assertTrue(renamed.archive)
        self.assertEqual(renamed.attribute_text, "R/O SYS ARC")
        self.assertTrue(all(extent.read_only for extent in renamed.extents))
        self.assertTrue(all(extent.system for extent in renamed.extents))
        self.assertTrue(all(extent.archive for extent in renamed.extents))
        extracted = extract_logical_file(
            read_image(image_path).logical_data,
            get_profile("ibm-3740"),
            renamed,
        )
        self.assertEqual(extracted, payload)

        deleted = delete_file_from_raw_image(
            image_path, "ibm-3740", user=3, name="RENAMED.BIN"
        )
        self.assertEqual(deleted, 2)
        result = analyze_image(image_path, "ibm-3740")
        assert result.best_candidate is not None
        self.assertEqual(group_directory_entries(result.best_candidate.files), [])

    def test_rename_refuses_collision_and_invalid_name(self) -> None:
        image_path = self.directory / "disk.img"
        first = self.directory / "first.txt"
        second = self.directory / "second.txt"
        _empty_image(image_path, "ibm-3740")
        first.write_text("first")
        second.write_text("second")
        insert_files_into_raw_image(image_path, "ibm-3740", [first, second])
        before = image_path.read_bytes()

        with self.assertRaisesRegex(FilesystemError, "already contains"):
            rename_file_in_raw_image(
                image_path,
                "ibm-3740",
                user=0,
                old_name="FIRST.TXT",
                new_name="SECOND.TXT",
            )
        self.assertEqual(image_path.read_bytes(), before)

        with self.assertRaisesRegex(FilesystemError, "valid CP/M 8.3"):
            rename_file_in_raw_image(
                image_path,
                "ibm-3740",
                user=0,
                old_name="FIRST.TXT",
                new_name="this-name-is-too-long.txt",
            )
        self.assertEqual(image_path.read_bytes(), before)

    def test_short_s100ide_image_is_detected_and_extended_on_import(self) -> None:
        image_path = self.directory / "s100.img"
        source = self.directory / "hello.txt"
        _short_s100_image(image_path)
        original_size = image_path.stat().st_size
        self.assertEqual(original_size, 65536)

        initial = analyze_image(image_path, "s100ide")
        assert initial.best_candidate is not None
        self.assertEqual(initial.best_candidate.profile_id, "s100ide")
        self.assertGreaterEqual(initial.best_candidate.score, 30)

        source.write_bytes(b"Hello from S100 IDE\r\n")
        insert_files_into_raw_image(image_path, "s100ide", [source], user=0)
        self.assertGreater(image_path.stat().st_size, original_size)
        self.assertLess(image_path.stat().st_size, get_profile("s100ide").image_size)

        result = analyze_image(image_path, "s100ide")
        assert result.best_candidate is not None
        logical_files = group_directory_entries(result.best_candidate.files)
        self.assertEqual(len(logical_files), 1)
        self.assertEqual(logical_files[0].name, "HELLO.TXT")
        extracted = extract_logical_file(
            read_image(image_path).logical_data,
            get_profile("s100ide"),
            logical_files[0],
        )
        self.assertTrue(extracted.startswith(source.read_bytes()))

    def test_s100ide_profile_matches_dual_cf_geometry(self) -> None:
        profile = get_profile("s100ide")
        self.assertEqual(profile.image_size, 8388608)
        self.assertEqual(profile.directory_offset, 32768)
        self.assertEqual(profile.filesystem["block_size"], 2048)
        self.assertEqual(profile.filesystem["directory_entries"], 1024)
        self.assertEqual(profile.filesystem["allocation_pointer_bytes"], 2)
        self.assertTrue(profile.filesystem["variable_image_size"])


if __name__ == "__main__":
    unittest.main()
