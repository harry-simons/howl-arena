"""Shared loader + helpers for Season 1 offline analysis.

Pure data analysis — no model/API calls. Reads the 108 stored GameRecord dicts
from games/season-1/*.json (utf-8) and exposes small helpers reused by every
analysis script. Short model labels keep tables readable.
"""
from __future__ import annotations

import glob
import json
import os
from collections import defaultdict

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASON_DIR = os.path.join(PKG, "games", "season-1")

WOLF_ROLE = "werewolf"
VILLAGE_ROLES = {"villager", "seer", "healer"}

# Short, stable labels for tables/charts.
SHORT = {
    "google/gemma-4-31b-it": "gemma-4-31b",
    "meta-llama/llama-3.3-70b-instruct": "llama-3.3-70b",
    "mistralai/mistral-nemo": "mistral-nemo",
    "z-ai/glm-4.5-air": "glm-4.5-air",
    "qwen/qwen3-32b": "qwen3-32b",
    "amazon/nova-lite-v1": "nova-lite",
    "qwen/qwen3-next-80b-a3b-instruct": "qwen3-80b",
    "openai/gpt-oss-120b": "gpt-oss-120b",
    "openai/gpt-oss-20b": "gpt-oss-20b",
}


def short(model: str) -> str:
    return SHORT.get(model, model)


def load_games(directory: str = SEASON_DIR) -> list[dict]:
    """Load all season-1 game records, sorted by game_id (play order)."""
    games = []
    for f in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            games.append(json.load(fh))
    return sorted(games, key=lambda r: r.get("game_id") or "")


def seat_maps(rec: dict):
    """Return (seat_models, seat_roles) with int keys."""
    sm = {int(k): v for k, v in rec.get("seat_models", {}).items()}
    sr = {int(k): v for k, v in rec.get("seat_roles", {}).items()}
    return sm, sr


def is_wolf(role: str) -> bool:
    return role == WOLF_ROLE


if __name__ == "__main__":
    g = load_games()
    print(f"Loaded {len(g)} games from {SEASON_DIR}")
    print("Models:", ", ".join(sorted({short(m) for r in g for m in r['seat_models'].values()})))
