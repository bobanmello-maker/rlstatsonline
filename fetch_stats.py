#!/usr/bin/env python3
"""
Povlači sve replay-e iz jedne (ili vise) ballchasing.com grupa i pravi
flat JSON fajl (data.json) koji dashboard (index.html) direktno cita.

Token se NIKAD ne pise u ovaj fajl - cita se iz environment varijable
BALLCHASING_TOKEN (u GitHub Actions se to postavlja preko Secrets).

Pokretanje lokalno (za test):
    export BALLCHASING_TOKEN="tvoj_token"
    export BALLCHASING_GROUPS="online-30wp20uwjv,analiza-allua49smi"
    python fetch_stats.py
"""

import os
import sys
import time
import json
import requests

API_BASE = "https://ballchasing.com/api"
TOKEN = os.environ.get("BALLCHASING_TOKEN")
GROUPS = [g.strip() for g in os.environ.get("BALLCHASING_GROUPS", "").split(",") if g.strip()]
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "data.json")

# Ballchasing rate limit za "regular" (non-patreon) nalog: 2 poziva/sekundi.
# Stavljamo malo vece kasnjenje da budemo sigurni da ne udarimo u 429.
SLEEP_BETWEEN_CALLS = 0.6

if not TOKEN:
    print("GRESKA: BALLCHASING_TOKEN nije postavljen.", file=sys.stderr)
    sys.exit(1)

if not GROUPS:
    print("GRESKA: BALLCHASING_GROUPS nije postavljen (npr. 'online-30wp20uwjv').", file=sys.stderr)
    sys.exit(1)

HEADERS = {"Authorization": TOKEN}


def api_get(path, params=None):
    url = f"{API_BASE}{path}"
    while True:
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code == 429:
            print("Rate limited, cekam 5s...")
            time.sleep(5)
            continue
        r.raise_for_status()
        time.sleep(SLEEP_BETWEEN_CALLS)
        return r.json()


def list_replay_ids(group_id):
    """Vrati listu svih replay ID-jeva unutar grupe (uz paginaciju)."""
    ids = []
    params = {"group": group_id, "count": 200}
    next_url = None
    while True:
        if next_url:
            r = requests.get(next_url, headers=HEADERS)
            time.sleep(SLEEP_BETWEEN_CALLS)
            data = r.json()
        else:
            data = api_get("/replays", params=params)
        for replay in data.get("list", []):
            ids.append(replay["id"])
        next_url = data.get("next")
        if not next_url:
            break
    return ids


def safe_get(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


CORE_FIELDS = ["shots", "shots_against", "goals", "goals_against", "saves", "assists",
               "score", "mvp", "shooting_percentage"]

BOOST_FIELDS = ["bpm", "bcpm", "avg_amount", "amount_collected", "amount_stolen",
                "amount_collected_big", "amount_stolen_big", "amount_collected_small",
                "amount_stolen_small", "count_collected_big", "count_stolen_big",
                "count_collected_small", "count_stolen_small", "amount_overfill",
                "amount_overfill_stolen", "amount_used_while_supersonic",
                "time_zero_boost", "percent_zero_boost", "time_full_boost", "percent_full_boost",
                "time_boost_0_25", "time_boost_25_50", "time_boost_50_75", "time_boost_75_100",
                "percent_boost_0_25", "percent_boost_25_50", "percent_boost_50_75", "percent_boost_75_100"]

MOVEMENT_FIELDS = ["avg_speed", "total_distance", "time_supersonic_speed", "time_boost_speed",
                    "time_slow_speed", "time_ground", "time_low_air", "time_high_air",
                    "time_powerslide", "count_powerslide", "avg_powerslide_duration",
                    "percent_slow_speed", "percent_boost_speed", "percent_supersonic_speed",
                    "percent_ground", "percent_low_air", "percent_high_air"]

POSITIONING_FIELDS = ["avg_distance_to_ball", "avg_distance_to_ball_possession",
                       "avg_distance_to_ball_no_possession", "avg_distance_to_mates",
                       "time_defensive_third", "time_neutral_third", "time_offensive_third",
                       "time_defensive_half", "time_offensive_half", "time_behind_ball",
                       "time_infront_ball", "time_most_back", "time_most_forward",
                       "goals_against_while_last_defender", "time_closest_to_ball",
                       "time_farthest_from_ball", "percent_defensive_third", "percent_offensive_third",
                       "percent_neutral_third", "percent_defensive_half", "percent_offensive_half",
                       "percent_behind_ball", "percent_infront_ball", "percent_most_back",
                       "percent_most_forward", "percent_closest_to_ball", "percent_farthest_from_ball"]

DEMO_FIELDS = ["inflicted", "taken"]


def extract_category(stats_dict, category, fields):
    src = stats_dict.get(category, {}) or {}
    return {f: src.get(f, 0) for f in fields}


def flatten_replay(replay):
    """Pretvori jedan detaljan replay JSON u listu redova (jedan red = jedan igrac)."""
    rows = []
    date = replay.get("date")
    map_name = replay.get("map_name", replay.get("map_code", "?"))
    playlist = replay.get("playlist_name", replay.get("playlist_id", "?"))
    duration = replay.get("duration")
    replay_id = replay.get("id")

    for color in ("blue", "orange"):
        team = replay.get(color)
        if not team:
            continue
        other_color = "orange" if color == "blue" else "blue"
        team_goals = safe_get(replay, color, "stats", "core", "goals", default=team.get("goals", 0))
        other_team = replay.get(other_color, {})
        opp_goals = safe_get(replay, other_color, "stats", "core", "goals", default=other_team.get("goals", 0))
        teammates = [pl.get("name", "?") for pl in team.get("players", [])]

        for p in team.get("players", []):
            stats = p.get("stats", {}) or {}
            core = extract_category(stats, "core", CORE_FIELDS)
            core["mvp"] = bool(core.get("mvp", False))

            rows.append({
                "replay_id": replay_id,
                "date": date,
                "map": map_name,
                "playlist": playlist,
                "duration": duration,
                "team_color": color,
                "team_goals": team_goals,
                "opponent_goals": opp_goals,
                "win": (team_goals or 0) > (opp_goals or 0),
                "player": p.get("name", "?"),
                "platform": safe_get(p, "id", "platform", default="offline"),
                "teammates": [t for t in teammates if t != p.get("name", "?")],
                "core": core,
                "boost": extract_category(stats, "boost", BOOST_FIELDS),
                "movement": extract_category(stats, "movement", MOVEMENT_FIELDS),
                "positioning": extract_category(stats, "positioning", POSITIONING_FIELDS),
                "demo": extract_category(stats, "demo", DEMO_FIELDS),
            })
    return rows


def main():
    all_rows = []
    seen_ids = set()

    for group_id in GROUPS:
        print(f"Grupa: {group_id}")
        try:
            replay_ids = list_replay_ids(group_id)
        except requests.HTTPError as e:
            print(f"  Ne mogu da ucitam grupu {group_id}: {e}", file=sys.stderr)
            continue

        print(f"  Nadjeno {len(replay_ids)} replay-a")
        for rid in replay_ids:
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            try:
                replay = api_get(f"/replays/{rid}")
            except requests.HTTPError as e:
                print(f"  Preskacem {rid}: {e}", file=sys.stderr)
                continue
            if replay.get("status") not in (None, "ok"):
                print(f"  Preskacem {rid}: status={replay.get('status')}", file=sys.stderr)
                continue
            rows = flatten_replay(replay)
            all_rows.extend(rows)
            print(f"  + {rid} ({len(rows)} redova)")

    all_rows.sort(key=lambda r: r.get("date") or "")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)

    print(f"\nSacuvano {len(all_rows)} redova (igrac x mec) u {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
