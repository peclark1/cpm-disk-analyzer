from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cpm_disk_analyzer.containers import ImageFormatError, read_image


class ImdReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_reads_uncompressed_and_compressed_sectors(self) -> None:
        # One 128-byte track containing logical sector 2 followed physically by 1.
        header = b"IMD test fixture\r\n\x1a"
        track = bytes([0, 0, 0, 2, 0]) + bytes([2, 1])
        sector_2 = bytes([2, 0xE5])  # compressed
        sector_1 = bytes([1]) + b"A" * 128
        path = self.directory / "test.imd"
        path.write_bytes(header + track + sector_2 + sector_1)

        result = read_image(path)
        self.assertEqual(result.kind, "imd")
        self.assertEqual(result.logical_data, b"A" * 128 + b"\xE5" * 128)
        self.assertEqual(result.metadata["cylinders"], 1)
        self.assertEqual(result.metadata["sectors_per_track"], [2])

    def test_rejects_truncated_imd(self) -> None:
        path = self.directory / "bad.imd"
        path.write_bytes(b"IMD broken\x1a\x00")
        with self.assertRaises(ImageFormatError):
            read_image(path)


if __name__ == "__main__":
    unittest.main()

