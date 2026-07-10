from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import stackchan_tts_server as tts


class VoiceSelectionTests(unittest.TestCase):
    def test_auto_selects_tingting_for_chinese(self):
        with patch.object(tts, "DEFAULT_ZH_VOICE", "Tingting"):
            self.assertEqual(tts.choose_voice("比赛开始了！", {}), "Tingting")

    def test_auto_selects_samantha_for_english(self):
        with patch.object(tts, "DEFAULT_EN_VOICE", "Samantha"):
            self.assertEqual(tts.choose_voice("The match has started!", {}), "Samantha")

    def test_explicit_lang_overrides_text_detection(self):
        with (
            patch.object(tts, "DEFAULT_ZH_VOICE", "Tingting"),
            patch.object(tts, "DEFAULT_EN_VOICE", "Samantha"),
        ):
            self.assertEqual(
                tts.choose_voice("The match has started!", {"lang": ["zh-CN"]}),
                "Tingting",
            )
            self.assertEqual(
                tts.choose_voice("比赛开始了！", {"lang": ["en-US"]}),
                "Samantha",
            )

    def test_explicit_voice_overrides_lang_and_text_detection(self):
        query = {"voice": ["Daniel"], "lang": ["zh-CN"]}

        self.assertEqual(tts.choose_voice("比赛开始了！", query), "Daniel")


if __name__ == "__main__":
    unittest.main()
