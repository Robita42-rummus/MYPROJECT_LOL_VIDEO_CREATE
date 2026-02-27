"""
プレイヤーのロール（ポジション）をキャッシュするモジュール

ロール判定方法:
  直近 ROLE_DETECT_GAMES 件のランクゲームの teamPosition を集計し、
  最頻値をそのプレイヤーのメインロールとする。

キャッシュ: cache/player_roles.json
  {
    "puuid": {
      "role": "JUNGLE",
      "game_name": "senzawa",
      "tag": "JP1",
      "lp": 1500,
      "updated_at": "2026-02-26T00:00:00+00:00"
    },
    ...
  }
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

CACHE_PATH        = Path("cache/player_roles.json")
ROLE_DETECT_GAMES = 10    # ロール判定に使う試合数
REFRESH_DAYS      = 7     # キャッシュ有効期間 (日)

# Riot API の teamPosition → 表示名
POSITION_MAP = {
    "TOP":     "TOP",
    "JUNGLE":  "JUNGLE",
    "MIDDLE":  "MID",
    "BOTTOM":  "ADC",
    "UTILITY": "SUP",
}


class PlayerTracker:
    def __init__(self, cache_path: Path = CACHE_PATH):
        self.cache_path = cache_path
        self._cache: dict = self._load()

    # ------------------------------------------------------------------
    # キャッシュ I/O
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # 有効期限チェック
    # ------------------------------------------------------------------

    def _is_stale(self, entry: dict) -> bool:
        """キャッシュが REFRESH_DAYS 日以上古ければ True"""
        updated = entry.get("updated_at")
        if not updated:
            return True
        try:
            dt = datetime.fromisoformat(updated)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt) > timedelta(days=REFRESH_DAYS)
        except Exception:
            return True

    # ------------------------------------------------------------------
    # ロール判定
    # ------------------------------------------------------------------

    def _detect_role(self, puuid: str, riot_client) -> str:
        """直近 ROLE_DETECT_GAMES 試合の teamPosition を集計して最頻値を返す"""
        try:
            match_ids = riot_client.get_matches_by_puuid(puuid, queue=420, count=ROLE_DETECT_GAMES)
        except Exception:
            return "UNKNOWN"

        role_counts: dict[str, int] = {}
        for match_id in match_ids:
            try:
                match = riot_client.get_match(match_id)
                if not match:
                    continue
                for p in match["info"]["participants"]:
                    if p.get("puuid") == puuid:
                        pos = p.get("teamPosition", "")
                        if pos in POSITION_MAP:
                            key = POSITION_MAP[pos]
                            role_counts[key] = role_counts.get(key, 0) + 1
                        break
            except Exception:
                continue

        if not role_counts:
            return "UNKNOWN"
        return max(role_counts, key=role_counts.get)

    def get_role(
        self,
        puuid: str,
        riot_client,
        game_name: str = "",
        tag: str = "",
        lp: int = 0,
    ) -> str:
        """
        ロールを返す。キャッシュが有効なら即返し、
        なければ試合履歴から判定してキャッシュに保存する。
        """
        entry = self._cache.get(puuid)
        if entry and not self._is_stale(entry):
            return entry["role"]

        role = self._detect_role(puuid, riot_client)
        self._cache[puuid] = {
            "role":       role,
            "game_name":  game_name,
            "tag":        tag,
            "lp":         lp,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        logger.debug(f"ロール判定: {game_name}#{tag} ({lp}LP) → {role}")
        return role

    # ------------------------------------------------------------------
    # バルク処理
    # ------------------------------------------------------------------

    def bulk_detect(self, entries: list[dict], riot_client) -> tuple[int, int]:
        """
        エントリーリスト ({puuid, game_name, tag, lp}) を受け取り、
        未キャッシュ or 期限切れのプレイヤーのみロール判定を実行する。

        Returns:
            (新規判定数, スキップ数)
        """
        new_count = skip_count = 0
        for e in entries:
            puuid = e.get("puuid", "")
            if not puuid:
                continue
            existing = self._cache.get(puuid)
            if existing and not self._is_stale(existing):
                skip_count += 1
                continue
            self.get_role(
                puuid,
                riot_client,
                e.get("game_name", ""),
                e.get("tag", ""),
                e.get("lp", 0),
            )
            new_count += 1

        return new_count, skip_count

    # ------------------------------------------------------------------
    # クエリ
    # ------------------------------------------------------------------

    def get_jungle_puuids(self) -> list[str]:
        """キャッシュ内の JUNGLE プレイヤーの PUUID リストを返す"""
        return [
            puuid
            for puuid, e in self._cache.items()
            if e.get("role") == "JUNGLE"
        ]

    def summary(self) -> dict[str, int]:
        """ロール別人数を返す"""
        counts: dict[str, int] = {}
        for e in self._cache.values():
            role = e.get("role", "UNKNOWN")
            counts[role] = counts.get(role, 0) + 1
        return counts
