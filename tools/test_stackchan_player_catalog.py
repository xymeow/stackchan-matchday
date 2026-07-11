from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import stackchan_player_catalog as players  # noqa: E402


def record(
    display_en: str,
    display_zh: str,
    *,
    aliases: list[str] | None = None,
    casual_zh: str = "",
    chant_zh: str = "",
    featured: bool = False,
) -> dict:
    value = {
        "aliases": aliases or [display_en],
        "display_name": {"zh": display_zh, "en": display_en},
        "featured": featured,
    }
    if casual_zh:
        value["casual_name"] = {"zh": casual_zh}
    if chant_zh:
        value["goal_chant"] = {"zh": chant_zh}
    return value


class PlayerAliasNormalizationTests(unittest.TestCase):
    def test_unicode_accents_apostrophes_and_hyphens_normalize(self):
        self.assertEqual(
            players.normalize_player_alias("  Ousmane Dembélé "),
            players.normalize_player_alias("OUSMANE DEMBELE"),
        )
        self.assertEqual(
            players.normalize_player_alias("N’Golo Kanté"),
            players.normalize_player_alias("N'Golo Kante"),
        )
        self.assertEqual(
            players.normalize_player_alias("Jean‑Pierre"),
            players.normalize_player_alias("Jean-Pierre"),
        )
        self.assertEqual(
            players.normalize_player_alias("Ｌ． Ｙａｍａｌ"),
            players.normalize_player_alias("L Yamal"),
        )


class PlayerCatalogTests(unittest.TestCase):
    def catalog(self) -> players.PlayerCatalog:
        return players.PlayerCatalog.from_dict(
            {
                "schema_version": 1,
                "players": {
                    "espn:1": record(
                        "Ousmane Dembélé",
                        "登贝莱",
                        aliases=["Ousmane Dembélé", "O. Dembélé"],
                        casual_zh="登子",
                        chant_zh="{name}打进去了！",
                        featured=True,
                    ),
                    "espn:2": record("Alex Smith", "亚历克斯·史密斯"),
                    "espn:3": record("Alex Smith", "另一位亚历克斯·史密斯"),
                },
            }
        )

    def test_stable_id_is_authoritative(self):
        entry = self.catalog().resolve(
            athlete_id="1", name="Alex Smith", short_name="A. Smith"
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry.key, "espn:1")

    def test_unknown_nonempty_id_does_not_fall_back_to_matching_alias(self):
        entry = self.catalog().resolve(
            athlete_id="999",
            name="Ousmane Dembele",
            short_name="O. Dembele",
        )

        self.assertIsNone(entry)

    def test_unique_normalized_alias_resolves_without_id(self):
        entry = self.catalog().resolve(name="Ousmane Dembele", short_name="O Dembele")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.athlete_id, "1")

    def test_ambiguous_alias_does_not_guess(self):
        catalog = self.catalog()
        self.assertIsNone(catalog.resolve(name="Alex Smith"))
        self.assertIsNone(catalog.resolve_alias("Alex Smith"))
        self.assertEqual(catalog.resolve(athlete_id="2").key, "espn:2")

    def test_schema_requires_numeric_espn_key_and_bilingual_display_name(self):
        with self.assertRaisesRegex(players.PlayerCatalogError, "espn:<numeric"):
            players.PlayerCatalog.from_dict(
                {
                    "schema_version": 1,
                    "players": {"Ousmane Dembélé": record("Name", "名字")},
                }
            )
        with self.assertRaisesRegex(players.PlayerCatalogError, "missing: en"):
            players.PlayerCatalog.from_dict(
                {
                    "schema_version": 1,
                    "players": {
                        "espn:1": {
                            "display_name": {"zh": "名字"},
                            "featured": False,
                        }
                    },
                }
            )

    def test_repository_catalog_loads_and_resolves_yamal(self):
        catalog = players.load_default_player_catalog()
        entry = catalog.resolve(athlete_id="362150", name="Lamine Yamal")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.display_name("zh"), "亚马尔")
        self.assertTrue(entry.featured)

    def test_repository_catalog_contains_verified_current_match_stars(self):
        catalog = players.load_default_player_catalog()
        expected = {
            "142200": ("凯恩", "飓风凯恩"),
            "253989": ("哈兰德", "哈宝"),
            "280555": ("萨卡", "小辣椒萨卡"),
            "291281": ("贝林厄姆", "贝林厄姆"),
        }

        for athlete_id, (display_name, casual_name) in expected.items():
            entry = catalog.resolve(athlete_id=athlete_id)
            with self.subTest(athlete_id=athlete_id):
                self.assertIsNotNone(entry)
                self.assertEqual(entry.display_name("zh"), display_name)
                self.assertEqual(entry.casual_name("zh"), casual_name)
                self.assertTrue(entry.featured)

    def test_repository_catalog_seeds_argentina_switzerland_stars(self):
        catalog = players.load_default_player_catalog()
        expected = {
            "45843": ("梅西", "梅西"),
            "149981": ("扎卡", "扎卡"),
            "158626": ("埃米利亚诺·马丁内斯", "埃米利亚诺·马丁内斯"),
            "214562": ("阿坎吉", "阿坎吉"),
            "219713": ("劳塔罗·马丁内斯", "劳塔罗·马丁内斯"),
            "277206": ("阿尔瓦雷斯", "小蜘蛛"),
        }

        for athlete_id, (display_name, casual_name) in expected.items():
            entry = catalog.resolve(athlete_id=athlete_id)
            with self.subTest(athlete_id=athlete_id):
                self.assertIsNotNone(entry)
                self.assertEqual(entry.display_name("zh"), display_name)
                self.assertEqual(entry.casual_name("zh"), casual_name)
                self.assertTrue(entry.featured)


class LegacyOverlayTests(unittest.TestCase):
    def setUp(self):
        self.catalog = players.PlayerCatalog.from_dict(
            {
                "schema_version": 1,
                "players": {
                    "espn:1": record(
                        "Ousmane Dembélé",
                        "登贝莱",
                        aliases=["Ousmane Dembélé", "O. Dembélé"],
                        casual_zh="登子",
                        chant_zh="目录口号：{name}",
                        featured=True,
                    )
                },
            }
        )

    def test_legacy_name_and_chant_override_catalog_but_keep_casual_name(self):
        profile = players.resolve_player_profile(
            self.catalog,
            athlete_id="1",
            name="Ousmane Dembélé",
            language="zh",
            player_names={"Ousmane Dembele": "奥斯曼·登贝莱"},
            star_chants={"espn:1": "旧配置口号：{name}"},
        )
        self.assertEqual(profile.catalog_key, "espn:1")
        self.assertEqual(profile.display_name, "奥斯曼·登贝莱")
        self.assertEqual(profile.casual_name, "登子")
        self.assertEqual(profile.goal_chant, "旧配置口号：{name}")
        self.assertEqual(profile.source, "catalog+legacy")
        self.assertTrue(profile.featured)

    def test_legacy_display_name_is_the_casual_fallback_without_catalog_nickname(self):
        catalog = players.PlayerCatalog.from_dict(
            {
                "schema_version": 1,
                "players": {
                    "espn:1": record("Lamine Yamal", "亚马尔", featured=True)
                },
            }
        )
        profile = players.resolve_player_profile(
            catalog,
            athlete_id="1",
            player_names={"1": "拉明·亚马尔"},
        )
        self.assertEqual(profile.display_name, "拉明·亚马尔")
        self.assertEqual(profile.casual_name, "拉明·亚马尔")

    def test_legacy_only_profile_keeps_old_config_compatible(self):
        profile = players.resolve_player_profile(
            self.catalog,
            athlete_id="99",
            name="Unknown Player",
            player_names={"Unknown Player": {"zh": "已配置球员"}},
            star_chants={"99": "旧口号"},
        )
        self.assertIsNone(profile.catalog_key)
        self.assertEqual(profile.display_name, "已配置球员")
        self.assertEqual(profile.casual_name, "已配置球员")
        self.assertEqual(profile.goal_chant, "旧口号")
        self.assertEqual(profile.source, "legacy")
        self.assertTrue(profile.featured)

    def test_unknown_profile_leaves_fallback_to_watcher(self):
        profile = players.resolve_player_profile(
            self.catalog,
            athlete_id="99",
            name="Unknown Player",
        )
        self.assertEqual(profile.source, "unknown")
        self.assertEqual(profile.display_name, "")
        self.assertEqual(profile.casual_name, "")
        self.assertFalse(profile.featured)

    def test_conflicting_normalized_legacy_aliases_are_ignored(self):
        value = players.legacy_overlay_value(
            {
                "Ousmane Dembélé": "登贝莱",
                "Ousmane Dembele": "另一个名字",
            },
            name="Ousmane Dembele",
        )
        self.assertEqual(value, "")


if __name__ == "__main__":
    unittest.main()
