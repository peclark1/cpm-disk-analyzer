from __future__ import annotations

import unittest

from cpm_disk_analyzer import gui


class GuiBoundaryTests(unittest.TestCase):
    def test_gui_module_does_not_import_gtk_eagerly(self) -> None:
        self.assertEqual(gui.APPLICATION_ID, "io.github.peclark1.CpmDiskAnalyzer")

    def test_missing_gtk_error_contains_ubuntu_install_command(self) -> None:
        try:
            gui._load_gtk()
        except RuntimeError as exc:
            self.assertIn("sudo apt install python3-gi", str(exc))


if __name__ == "__main__":
    unittest.main()

