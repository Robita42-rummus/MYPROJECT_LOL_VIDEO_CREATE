"""
毎日のスナップショットを CSV に保存するモジュール

output/daily/
  players/   YYYY-MM-DD.csv  — チャレンジャー上位プレイヤーリスト
  jungle/    YYYY-MM-DD.csv  — JG 試合候補リスト
"""
from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

DAILY_DIR = Path("output/daily")

PLAYER_COLUMNS = [
    "rank", "player", "role", "lp", "wins", "losses", "win_rate",
]

JUNGLE_COLUMNS = [
    "game_id", "match_id", "champion", "player",
    "kills", "deaths", "assists", "cs", "vision", "gold",
    "patch", "game_date", "age_hours",
]


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def save_jungle_matches(candidates: list[dict], date: Optional[str] = None) -> Path:
    """
    ジャングル試合候補リストを日付別 CSV に保存する。
    当日分がすでに存在する場合は上書きする。

    output/daily/jungle/YYYY-MM-DD.csv
    """
    d = date or _today()
    out_dir = DAILY_DIR / "jungle"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{d}.csv"

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=JUNGLE_COLUMNS)
        writer.writeheader()
        for c in sorted(candidates, key=lambda x: x.get("game_start_ms", 0)):
            start_ms = c.get("game_start_ms", 0)
            if start_ms:
                dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone()
                game_date = dt.strftime("%Y-%m-%d %H:%M JST")
            else:
                game_date = ""

            gv = c.get("game_version", "")
            patch = ".".join(gv.split(".")[:2]) if gv else ""

            writer.writerow({
                "game_id":   c.get("game_id", ""),
                "match_id":  c.get("match_id", ""),
                "champion":  c.get("champion", ""),
                "player":    c.get("player", ""),
                "kills":     c.get("kills", ""),
                "deaths":    c.get("deaths", ""),
                "assists":   c.get("assists", ""),
                "cs":        c.get("cs", ""),
                "vision":    c.get("vision", ""),
                "gold":      c.get("gold", ""),
                "patch":     patch,
                "game_date": game_date,
                "age_hours": f"{c.get('age_hours', 0):.1f}",
            })

    logger.info(f"[日次] ジャングル試合: {path.name} ({len(candidates)} 件)")
    return path


def save_players(api_key: str, roles_cache: dict, date: Optional[str] = None) -> Path:
    """
    チャレンジャープレイヤーリストを日付別 CSV に保存する。
    当日分がすでに存在する場合はスキップする (余分な API 呼び出しを避けるため)。

    output/daily/players/YYYY-MM-DD.csv
    """
    d = date or _today()
    out_dir = DAILY_DIR / "players"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{d}.csv"

    if path.exists():
        logger.info(f"[日次] プレイヤーリスト: {path.name} 既存のためスキップ")
        return path

    # Challenger リスト取得 (1 回)
    resp = requests.get(
        "https://jp1.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5",
        params={"api_key": api_key},
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error(f"[日次] Challenger API エラー {resp.status_code}")
        return path

    entries = sorted(
        resp.json().get("entries", []),
        key=lambda e: e["leaguePoints"],
        reverse=True,
    )

    # PUUID → プレイヤー名 (account API)
    names: dict[str, str] = {}
    for e in entries:
        puuid = e["puuid"]
        for attempt in range(2):
            r = requests.get(
                f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-puuid/{puuid}",
                params={"api_key": api_key},
                timeout=10,
            )
            if r.status_code == 200:
                d_resp = r.json()
                names[puuid] = f"{d_resp.get('gameName', '')}#{d_resp.get('tagLine', '')}"
                break
            if r.status_code == 429 and attempt == 0:
                time.sleep(2)
        time.sleep(0.05)

    # CSV 書き出し
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PLAYER_COLUMNS)
        writer.writeheader()
        for i, e in enumerate(entries, 1):
            puuid  = e["puuid"]
            wins   = e.get("wins", 0)
            losses = e.get("losses", 0)
            total  = wins + losses
            wr     = f"{wins / total * 100:.1f}%" if total else ""
            role   = roles_cache.get(puuid, {}).get("role", "")
            name   = names.get(puuid, "")
            writer.writerow({
                "rank":     i,
                "player":   name,
                "role":     role,
                "lp":       e["leaguePoints"],
                "wins":     wins,
                "losses":   losses,
                "win_rate": wr,
            })

    logger.info(f"[日次] プレイヤーリスト: {path.name} ({len(entries)} 人)")
    return path
