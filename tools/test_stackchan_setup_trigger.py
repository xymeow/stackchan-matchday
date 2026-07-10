from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SetupTriggerTests(unittest.TestCase):
    def test_mod_registers_non_screen_setup_inputs(self):
        source = (ROOT / "mod" / "mod.js").read_text(encoding="utf-8")

        self.assertIn("globalThis.button?.power", source)
        self.assertIn("power-button", source)
        self.assertIn("robot.touchPanel", source)
        self.assertIn("top-double-tap", source)
        self.assertIn("SETUP_TOP_DOUBLE_TAP_WINDOW_MS", source)
        self.assertIn("SETUP_TOP_MIN_INTER_TAP_MS", source)
        self.assertIn("SETUP_TOP_COOLDOWN_MS", source)
        self.assertIn("if (state.setup.visible)", source)
        self.assertNotIn("robot.touch.on", source)
        self.assertNotIn("robot.imu", source)


if __name__ == "__main__":
    unittest.main()
