from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cpm_disk_analyzer.settings import WindowState, load_window_state, save_window_state


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_home = Path(self.temporary_directory.name)
        self.environment = patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self.config_home)}, clear=False
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_round_trips_window_state(self) -> None:
        save_window_state(1440, 900, True)
        self.assertEqual(load_window_state(), WindowState(1440, 900, True))

    def test_invalid_settings_use_defaults(self) -> None:
        path = self.config_home / "cpm-disk-analyzer" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text("not json", encoding="utf-8")
        self.assertEqual(load_window_state(), WindowState())


if __name__ == "__main__":
    unittest.main()
