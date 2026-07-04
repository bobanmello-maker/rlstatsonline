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

        for p in team.get("players", []):
            stats = p.get("stats", {})
            core = stats.get("core", {})
            boost = stats.get("boost", {})
            movement = stats.get("movement", {})
            positioning = stats.get("positioning", {})
            demo = stats.get("demo", {})

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
                "goals": core.get("goals", 0),
                "assists": core.get("assists", 0),
                "saves": core.get("saves", 0),
                "shots": core.get("shots", 0),
                "score": core.get("score", 0),
                "mvp": bool(core.get("mvp", False)),
                "shooting_percentage": core.get("shooting_percentage", 0),
                "bpm": boost.get("bpm", 0),
                "avg_boost": boost.get("avg_amount", 0),
                "amount_stolen": boost.get("amount_stolen", 0),
                "percent_zero_boost": boost.get("percent_zero_boost", 0),
                "percent_full_boost": boost.get("percent_full_boost", 0),
                "avg_speed": movement.get("avg_speed", 0),
                "percent_supersonic_speed": movement.get("percent_supersonic_speed", 0),
                "percent_behind_ball": positioning.get("percent_behind_ball", 0),
                "percent_defensive_third": positioning.get("percent_defensive_third", 0),
                "percent_offensive_third": positioning.get("percent_offensive_third", 0),
                "avg_distance_to_ball": positioning.get("avg_distance_to_ball", 0),
                "avg_distance_to_mates": positioning.get("avg_distance_to_mates", 0),
                "demos_inflicted": demo.get("inflicted", 0),
                "demos_taken": demo.get("taken", 0),
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
