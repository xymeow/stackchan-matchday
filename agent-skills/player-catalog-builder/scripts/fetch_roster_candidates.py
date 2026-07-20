#!/usr/bin/env python3
"""Fetch ESPN team rosters and emit player-catalog skeleton candidates.

Diffs a team's roster against config/espn_player_catalog.json and prints
compact JSON skeletons for the players still missing, so an agent can
propose Chinese display names for human confirmation without pasting raw
ESPN responses into context. Standard library only.

Examples:
    fetch_roster_candidates.py --espn-league soccer/eng.1 --list-teams
    fetch_roster_candidates.py --espn-league soccer/eng.1 --team 359
    fetch_roster_candidates.py --espn-league soccer/eng.1 --team Arsenal
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
USER_AGENT = "stackchan-matchday-catalog/1.0"
TIMEOUT_SECONDS = 20
DEFAULT_CATALOG = Path(__file__).resolve().parents[3] / "config" / "espn_player_catalog.json"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def league_teams(league: str) -> list[dict]:
    payload = fetch_json(f"{ESPN_BASE}/{league}/teams")
    teams = []
    for sport in payload.get("sports", []):
        for league_blob in sport.get("leagues", []):
            for wrapper in league_blob.get("teams", []):
                team = wrapper.get("team") or {}
                teams.append(
                    {
                        "id": str(team.get("id", "")),
                        "name": team.get("displayName"),
                        "abbreviation": team.get("abbreviation"),
                    }
                )
    return teams


def resolve_team_id(league: str, query: str) -> tuple[str, str]:
    if query.isdigit():
        return query, query
    needle = query.casefold()
    matches = [
        team
        for team in league_teams(league)
        if needle in str(team.get("name", "")).casefold()
        or needle == str(team.get("abbreviation", "")).casefold()
    ]
    if len(matches) != 1:
        names = ", ".join(str(team.get("name")) for team in matches) or "none"
        raise SystemExit(
            f"--team {query!r} matched {len(matches)} teams ({names}); "
            "use the numeric id from --list-teams"
        )
    return str(matches[0]["id"]), str(matches[0]["name"])


def roster_athletes(league: str, team_id: str) -> list[dict]:
    payload = fetch_json(f"{ESPN_BASE}/{league}/teams/{urllib.parse.quote(team_id)}/roster")
    athletes = payload.get("athletes") or []
    flattened: list[dict] = []
    for entry in athletes:
        # Some sports group the roster ({position, items: [...]}), others
        # return athletes directly.
        if isinstance(entry, dict) and isinstance(entry.get("items"), list):
            flattened.extend(entry["items"])
        elif isinstance(entry, dict):
            flattened.append(entry)
    return flattened


def known_catalog_ids(catalog_path: Path) -> set[str]:
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    return set(payload.get("players", {}))


def skeleton(athlete: dict, team_name: str) -> tuple[str, dict] | None:
    athlete_id = str(athlete.get("id") or "")
    display_name = str(athlete.get("displayName") or athlete.get("fullName") or "")
    if not athlete_id or not display_name:
        return None
    short_name = str(athlete.get("shortName") or "")
    aliases = [display_name]
    if short_name and short_name != display_name:
        aliases.append(short_name)
    position = athlete.get("position") or {}
    entry = {
        "aliases": aliases,
        "display_name": {"zh": "", "en": display_name},
        "_context": {
            "team": team_name,
            "position": position.get("abbreviation") or position.get("name") or "",
            "jersey": str(athlete.get("jersey") or ""),
        },
    }
    return f"espn:{athlete_id}", entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--espn-league", required=True,
                        help="ESPN sport/league path (e.g. soccer/eng.1)")
    parser.add_argument("--team", default="",
                        help="team id or name substring")
    parser.add_argument("--list-teams", action="store_true")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG,
                        help="existing catalog to diff against")
    parser.add_argument("--include-known", action="store_true",
                        help="also list players already in the catalog")
    args = parser.parse_args()

    try:
        if args.list_teams:
            output = {
                "league": args.espn_league,
                "teams": league_teams(args.espn_league),
            }
        else:
            if not args.team:
                raise SystemExit("--team or --list-teams is required")
            team_id, team_name = resolve_team_id(args.espn_league, args.team)
            known = known_catalog_ids(args.catalog)
            candidates: dict[str, dict] = {}
            skipped_known = 0
            for athlete in roster_athletes(args.espn_league, team_id):
                built = skeleton(athlete, team_name)
                if built is None:
                    continue
                key, entry = built
                if key in known and not args.include_known:
                    skipped_known += 1
                    continue
                candidates[key] = entry
            output = {
                "league": args.espn_league,
                "team_id": team_id,
                "team": team_name,
                "already_in_catalog": skipped_known,
                "count": len(candidates),
                "candidates": candidates,
            }
    except Exception as error:  # noqa: BLE001 - agent needs the reason
        json.dump({"error": str(error)}, sys.stdout)
        print()
        raise SystemExit(1)

    json.dump(output, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()
