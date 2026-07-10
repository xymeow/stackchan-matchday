from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = (ROOT / "mod" / "ui.js").read_text(encoding="utf-8")
WEB_SOURCE = (ROOT / "mod" / "web.js").read_text(encoding="utf-8")
HTTP_SOURCE = (ROOT / "mod" / "http-server-safe.js").read_text(encoding="utf-8")


class ModLayoutTests(unittest.TestCase):
    def test_probability_bar_is_anchored_to_top(self):
        frame = UI_SOURCE.split("function createProbabilityBarEffect", 1)[1].split(
            "contents:", 1
        )[0]

        self.assertIn("top: PK_BAR_TOP", frame)
        self.assertNotIn("bottom: 0", frame)

    def test_probability_updates_keep_setup_qr_modal(self):
        body = UI_SOURCE.split("export function setProbabilityBar", 1)[1].split(
            "// ---------------------------------------------------------------------------", 1
        )[0]

        self.assertIn("keepSetupQrOnTop(robot)", body)

    def test_balloon_and_ticker_keep_setup_qr_modal(self):
        balloon = UI_SOURCE.split("export function showBalloon", 1)[1].split(
            "export function hideBalloon", 1
        )[0]
        ticker = UI_SOURCE.split("export function setTicker", 1)[1].split(
            "// ---------------------------------------------------------------------------", 1
        )[0]

        self.assertIn("keepSetupQrOnTop(robot)", balloon)
        self.assertIn("keepSetupQrOnTop(robot)", ticker)

    def test_temporary_balloon_remains_anchored_to_bottom(self):
        frame = UI_SOURCE.split("function createBalloonEffect", 1)[1].split(
            "contents:", 1
        )[0]

        self.assertIn("bottom: BALLOON.bottom", frame)

    def test_device_setup_page_exposes_language_and_avoids_overlapping_refreshes(self):
        self.assertIn('name="language"', WEB_SOURCE)
        self.assertIn("position_team:position,language", WEB_SOURCE)
        self.assertIn("setTimeout(refresh,3000)", WEB_SOURCE)
        self.assertNotIn("setInterval(refresh,1500)", WEB_SOURCE)

    def test_mod_uses_disconnect_safe_http_service(self):
        self.assertIn("from 'matchday/http-server-safe'", WEB_SOURCE)
        self.assertIn("headers.set('content-length', this.#body.byteLength)", HTTP_SOURCE)
        self.assertIn("request.arrayBuffer().then", HTTP_SOURCE)
        self.assertIn("connection.respondWith(response).catch", HTTP_SOURCE)
        self.assertIn("HTTP response closed", HTTP_SOURCE)


if __name__ == "__main__":
    unittest.main()
