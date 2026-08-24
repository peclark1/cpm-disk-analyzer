from __future__ import annotations

import unittest
from inspect import getsource

from cpm_disk_analyzer import gui


class GuiBoundaryTests(unittest.TestCase):
    def test_gui_module_does_not_import_gtk_eagerly(self) -> None:
        self.assertEqual(gui.APPLICATION_ID, "io.github.peclark1.CpmDiskAnalyzer")

    def test_missing_gtk_error_contains_ubuntu_install_command(self) -> None:
        try:
            gui._load_gtk()
        except RuntimeError as exc:
            self.assertIn("sudo apt install python3-gi", str(exc))

    def test_empty_state_is_wired_as_an_open_action(self) -> None:
        source = getsource(gui.create_application)
        self.assertIn('Gtk.Button(label="Open…")', source)
        self.assertIn("row.set_activatable_widget(open_button)", source)

    def test_uses_retained_asynchronous_file_dialogs(self) -> None:
        source = getsource(gui.create_application)
        self.assertNotIn("FileChooserNative", source)
        self.assertIn("Gtk.FileDialog()", source)
        self.assertIn("self._open_dialog = dialog", source)
        self.assertIn("self._save_dialog = dialog", source)


if __name__ == "__main__":
    unittest.main()
