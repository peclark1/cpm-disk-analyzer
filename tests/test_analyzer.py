from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cpm_disk_analyzer.analyzer import analyze_image
from cpm_disk_analyzer.cli import main
from cpm_disk_analyzer.profiles import get_profile
from cpm_disk_analyzer.report import as_json


def _make_ibm3740(path: Path) -> None:
    profile = get_profile("ibm-3740")
    image = bytearray(profile.image_size)
    offset = profile.directory_offset
    image[offset : offset + 64 * 32] = b"\xe5" * (64 * 32)

    entry = bytearray(32)
    entry[0] = 0
    entry[1:9] = b"HELLO   "
    entry[9:12] = b"COM"
    entry[12] = 0
    entry[15] = 3
    entry[16] = 2
    image[offset : offset + 32] = entry
    path.write_bytes(image)


class AnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_detects_ibm3740_and_directory(self) -> None:
        image_path = self.directory / "sample.img"
        _make_ibm3740(image_path)
        result = analyze_image(image_path)

        self.assertEqual(result.container, "raw")
        self.assertIsNotNone(result.best_candidate)
        assert result.best_candidate is not None
        self.assertIn(result.best_candidate.profile_id, {"dsi-sd26", "ibm-3740"})
        self.assertGreaterEqual(result.best_candidate.score, 55)
        self.assertEqual(result.best_candidate.files[0].name, "HELLO.COM")
        self.assertEqual(result.best_candidate.files[0].estimated_size, 384)

    def test_json_report_is_machine_readable(self) -> None:
        image_path = self.directory / "sample.img"
        _make_ibm3740(image_path)
        payload = json.loads(as_json(analyze_image(image_path)))
        self.assertEqual(payload["path"], str(image_path))
        self.assertEqual(len(payload["sha256"]), 64)
        self.assertTrue(payload["candidates"])

    def test_cli_writes_json(self) -> None:
        image_path = self.directory / "sample.img"
        report_path = self.directory / "report.json"
        _make_ibm3740(image_path)
        self.assertEqual(
            main(["analyze", str(image_path), "--json", str(report_path)]), 0
        )
        self.assertEqual(json.loads(report_path.read_text())["container"], "raw")

    def test_unknown_size_is_not_claimed_as_match(self) -> None:
        path = self.directory / "unknown.bin"
        path.write_bytes(b"not a disk image")
        result = analyze_image(path)
        self.assertEqual(result.candidates, [])


if __name__ == "__main__":
    unittest.main()
