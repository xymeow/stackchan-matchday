from __future__ import annotations

import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = (ROOT / "mod" / "ui.js").read_text(encoding="utf-8")
WEB_SOURCE = (ROOT / "mod" / "web.js").read_text(encoding="utf-8")
HTTP_SOURCE = (ROOT / "mod" / "http-server-safe.js").read_text(encoding="utf-8")
STATE_SOURCE = (ROOT / "mod" / "state.js").read_text(encoding="utf-8")
MOD_SOURCE = (ROOT / "mod" / "mod.js").read_text(encoding="utf-8")
MANIFEST_SOURCE = (ROOT / "mod" / "manifest.json").read_text(encoding="utf-8")
AUDIO_SOURCE = (ROOT / "mod" / "audio.js").read_text(encoding="utf-8")
COMMANDS_SOURCE = (ROOT / "mod" / "commands.js").read_text(encoding="utf-8")


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

    def test_probability_split_uses_a_clamped_football_icon(self):
        body = UI_SOURCE.split("class ProbabilityBarBehavior", 1)[1].split(
            "export function hideProbabilityBar", 1
        )[0]

        self.assertIn("name: 'pkBall'", body)
        self.assertIn("texture: { path: 'football.png' }", UI_SOURCE)
        self.assertIn("ball.x = clamp(leftWidth - PK_BALL_SIZE / 2", body)
        self.assertNotIn("name: 'pkDivider'", body)
        self.assertIn('"./assets/ui/*"', MANIFEST_SOURCE)
        football = (ROOT / "mod" / "assets" / "ui" / "football.png").read_bytes()
        self.assertEqual(football[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", football[16:24]), (24, 24))
        self.assertEqual(football[24:26], bytes((8, 6)))  # 8-bit RGBA

    def test_setup_qr_uses_the_generated_texture_dimensions(self):
        body = UI_SOURCE.split("function createSetupQrEffect", 1)[1].split(
            "export function hideSetupQr", 1
        )[0]

        self.assertIn("new Texture('setup-qr.png')", body)
        self.assertIn("width: qrWidth", body)
        self.assertIn("height: qrHeight", body)
        self.assertNotIn("width: 168", body)
        self.assertNotIn("height: 168", body)

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

    def test_device_setup_page_queues_persistent_commentary_style_changes(self):
        self.assertIn("COMMENTARY_STYLES = ['casual', 'balanced', 'professional']", STATE_SOURCE)
        self.assertIn("commentaryStyle: 'balanced'", STATE_SOURCE)
        for style in ("casual", "balanced", "professional"):
            self.assertIn(f'name="commentary_style" value="{style}"', WEB_SOURCE)
        self.assertIn("'/api/match-setup/style'", WEB_SOURCE)
        self.assertIn("style_only: true", WEB_SOURCE)
        self.assertIn("commentary_style: commentaryStyle", WEB_SOURCE)
        self.assertIn("savePreference('matchSetupPending', JSON.stringify(pending))", WEB_SOURCE)
        self.assertIn("savePreference('commentaryStyle', value)", WEB_SOURCE)
        self.assertIn("readPreference('commentaryStyle', 'balanced')", MOD_SOURCE)

    def test_commentary_style_is_reported_and_applied_only_after_ack(self):
        payload = WEB_SOURCE.split("function matchSetupPayload", 1)[1].split(
            "function syncMatchSetup", 1
        )[0]
        acknowledgement = WEB_SOURCE.split("function acknowledgeMatchSetup", 1)[1].split(
            "// ---------------------------------------------------------------------------", 1
        )[0]

        self.assertIn("commentary_style: state.matchSetup.commentaryStyle", payload)
        self.assertIn("applyCommentaryStyle(payload?.commentary_style)", acknowledgement)
        self.assertIn("pending?.style_only", acknowledgement)
        self.assertIn("if (!state.matchSetup.pending)", acknowledgement)
        self.assertIn("if (!requestId)", acknowledgement)
        self.assertIn("!== requestId", acknowledgement)
        self.assertIn("id=\"styleHint\"", WEB_SOURCE)

    def test_full_setup_absorbs_style_pending_without_losing_the_preference(self):
        queue = WEB_SOURCE.split("function queueMatchSetup", 1)[1].split(
            "function acknowledgeMatchSetup", 1
        )[0]
        page = WEB_SOURCE.split("function setupPageHtml", 1)[1].split(
            "// ---------------------------------------------------------------------------", 1
        )[0]

        self.assertIn("!state.matchSetup.pending.style_only", queue)
        self.assertIn("state.matchSetup.pending?.commentary_style", queue)
        self.assertIn("commentary_style: commentaryStyle", queue)
        self.assertIn("pending.commentary_style = commentaryStyle", queue)
        self.assertIn("commentary_style:commentaryStyle", page)
        self.assertIn("data.pending&&data.pending.commentary_style", page)
        self.assertIn("result.status ?? 400", WEB_SOURCE)

    def test_device_setup_page_offers_standalone_market_watch(self):
        self.assertIn('id="kalshiUrl"', WEB_SOURCE)
        self.assertIn('id="watchMarket"', WEB_SOURCE)
        self.assertIn("standalone:true", WEB_SOURCE)
        self.assertIn("payload?.standalone", WEB_SOURCE)
        self.assertIn("kalshi_url required", WEB_SOURCE)

    def test_mod_uses_disconnect_safe_http_service(self):
        self.assertIn("from 'matchday/http-server-safe'", WEB_SOURCE)
        self.assertIn("headers.set('content-length', this.#body.byteLength)", HTTP_SOURCE)
        self.assertIn("request.arrayBuffer().then", HTTP_SOURCE)
        self.assertIn("connection.respondWith(response).catch", HTTP_SOURCE)
        self.assertIn("HTTP response closed", HTTP_SOURCE)


class MuteBossKeyTests(unittest.TestCase):
    def test_mute_gates_every_audible_and_motion_path(self):
        self.assertIn("skipped (muted)", AUDIO_SOURCE)
        # speech, tone clips, and celebrations all check the mute flag
        self.assertGreaterEqual(AUDIO_SOURCE.count("state.mute.on"), 3)
        # alert light flash and raw tone are gated in the command layer
        self.assertIn("ok light flash skipped (muted)", COMMANDS_SOURCE)
        self.assertIn("ok tone skipped (muted)", COMMANDS_SOURCE)

    def test_clip_skips_while_tts_stream_is_active(self):
        self.assertIn("skipped (tts busy)", AUDIO_SOURCE)

    def test_long_press_toggles_mute_and_release_is_not_a_tap(self):
        self.assertIn("MUTE_LONG_PRESS_MS", MOD_SOURCE)
        self.assertIn("top-long-press", MOD_SOURCE)
        self.assertIn("longPressFired = false", MOD_SOURCE)

    def test_mute_has_command_badge_and_persistence(self):
        self.assertIn("'mute on [minutes]'", COMMANDS_SOURCE)
        self.assertIn("function executeMuteCommand", COMMANDS_SOURCE)
        self.assertIn("savePreference('muted'", COMMANDS_SOURCE)
        self.assertIn("function setMuteBadge", UI_SOURCE)
        self.assertIn("readPreference('muted', false)", MOD_SOURCE)


if __name__ == "__main__":
    unittest.main()
