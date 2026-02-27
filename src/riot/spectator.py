"""
JP1サーバーの試合をクロールして「録画する価値のある試合」を見つける

【注意】Spectator API は Production キーが必要。
開発用キーでは match-v5 マッチ履歴ポーリングで代替する。
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger
from .api_client import RiotAPIClient


@dataclass
class FinishedGame:
    """処理対象の完了済み試合"""
    game_id: int
    match_id: str        # "JP1_1234567890" 形式
    platform_id: str     # "JP1"
    puuids: list         # 参加者のPUUID (先頭はチャレンジャー)

    @property
    def numeric_game_id(self) -> int:
        return self.game_id


@dataclass
class GameCrawler:
    """
    チャレンジャー/GMのマッチ履歴を定期ポーリングし、
    新しい試合を見つける。

    Spectator API の代替 (開発用キーでも動作)。
    """
    client: RiotAPIClient
    processed_cache_path: Path
    target_tiers: list = field(default_factory=lambda: ["CHALLENGER", "GRANDMASTER"])

    def __post_init__(self):
        self.processed_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._processed: set = self._load_processed()
        self._puuid_pool: list = []   # チャレンジャーのPUUIDリスト

    def _load_processed(self) -> set:
        if self.processed_cache_path.exists():
            data = json.loads(self.processed_cache_path.read_text())
            return set(data)
        return set()

    def _save_processed(self):
        self.processed_cache_path.write_text(json.dumps(list(self._processed)))

    def mark_processed(self, match_id: str):
        self._processed.add(match_id)
        self._save_processed()
        logger.info(f"処理済みマーク: {match_id}")

    def refresh_puuid_pool(self):
        """チャレンジャー/GMのPUUIDリストを更新 (1時間ごと)"""
        puuids = []
        if "CHALLENGER" in self.target_tiers:
            entries = self.client.get_challenger_players()
            puuids.extend([e["puuid"] for e in entries if "puuid" in e])
        if "GRANDMASTER" in self.target_tiers:
            entries = self.client.get_grandmaster_players()
            puuids.extend([e["puuid"] for e in entries if "puuid" in e])

        self._puuid_pool = puuids
        logger.info(f"PUUIDプール更新: {len(puuids)} 人")

    def find_new_finished_game(self) -> Optional[FinishedGame]:
        """
        チャレンジャーのマッチ履歴を確認し、
        未処理の最新試合を返す。

        Returns:
            FinishedGame もしくは None
        """
        if not self._puuid_pool:
            self.refresh_puuid_pool()

        for puuid in self._puuid_pool:
            match_ids = self.client.get_matches_by_puuid(puuid, queue=420, count=3)
            for match_id in match_ids:
                if match_id in self._processed:
                    continue

                # 新しい試合発見
                logger.info(f"新規試合発見: {match_id} (PUUID: {puuid[:20]}...)")

                # ゲームIDを数値で取得 (JP1_1234567890 → 1234567890)
                try:
                    game_id = int(match_id.split("_")[-1])
                except ValueError:
                    game_id = 0

                return FinishedGame(
                    game_id=game_id,
                    match_id=match_id,
                    platform_id="JP1",
                    puuids=[puuid],
                )

        logger.info("新規試合なし")
        return None

    def find_new_finished_games_batch(self, max_games: int = 5) -> list:
        """
        まとめて複数の未処理試合を返す (バッチ処理用)
        """
        if not self._puuid_pool:
            self.refresh_puuid_pool()

        seen_match_ids: set = set()
        results = []

        for puuid in self._puuid_pool:
            if len(results) >= max_games:
                break
            match_ids = self.client.get_matches_by_puuid(puuid, queue=420, count=5)
            for match_id in match_ids:
                if match_id in self._processed or match_id in seen_match_ids:
                    continue
                seen_match_ids.add(match_id)
                try:
                    game_id = int(match_id.split("_")[-1])
                except ValueError:
                    game_id = 0
                results.append(FinishedGame(
                    game_id=game_id,
                    match_id=match_id,
                    platform_id="JP1",
                    puuids=[puuid],
                ))
                if len(results) >= max_games:
                    break

        logger.info(f"未処理試合 {len(results)} 件発見")
        return results
