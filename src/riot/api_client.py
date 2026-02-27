"""
Riot Games API クライアント
レート制限を自動管理し、JP1サーバーの試合データを取得する
"""
from __future__ import annotations
import time
import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import requests
from loguru import logger


@dataclass
class RateLimiter:
    """Riot API のレート制限に対応する簡易実装"""
    # 開発用キー: 20 req/s, 100 req/2min
    # 本番キー:   50 req/s, 1000 req/10min
    short_limit: int = 20
    short_window: int = 1
    long_limit: int = 100
    long_window: int = 120

    _short_calls: list = field(default_factory=list)
    _long_calls: list = field(default_factory=list)

    def wait_if_needed(self):
        now = time.time()
        # 古い記録を削除
        self._short_calls = [t for t in self._short_calls if now - t < self.short_window]
        self._long_calls  = [t for t in self._long_calls  if now - t < self.long_window]

        if len(self._short_calls) >= self.short_limit:
            wait = self.short_window - (now - self._short_calls[0]) + 0.05
            if wait > 0:
                logger.debug(f"Rate limit (short): {wait:.2f}秒待機")
                time.sleep(wait)
        if len(self._long_calls) >= self.long_limit:
            wait = self.long_window - (now - self._long_calls[0]) + 0.05
            if wait > 0:
                logger.debug(f"Rate limit (long): {wait:.2f}秒待機")
                time.sleep(wait)

        now = time.time()
        self._short_calls.append(now)
        self._long_calls.append(now)


class RiotAPIClient:
    """
    Riot Games API クライアント

    JP1サーバー対応:
    - summoner/league/spectator: https://jp1.api.riotgames.com
    - match/account:             https://asia.api.riotgames.com
    """

    PLATFORM_URL = "https://jp1.api.riotgames.com"
    REGIONAL_URL = "https://asia.api.riotgames.com"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.rate_limiter = RateLimiter()
        self.session = requests.Session()
        self.session.headers.update({"X-Riot-Token": api_key})

    def _get(self, base_url: str, path: str, params: dict = None) -> dict:
        """GET リクエスト (レート制限・リトライ付き)"""
        self.rate_limiter.wait_if_needed()
        url = f"{base_url}{path}"
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    logger.warning(f"429 Too Many Requests: {retry_after}秒待機")
                    time.sleep(retry_after)
                elif resp.status_code == 404:
                    return None  # データなし（エラーではない）
                else:
                    logger.error(f"API エラー {resp.status_code}: {url}")
                    return None
            except requests.RequestException as e:
                logger.error(f"リクエスト失敗 (試行{attempt+1}/3): {e}")
                time.sleep(2 ** attempt)
        return None

    # =============================================
    # Summoner API
    # =============================================

    def get_summoner_by_id(self, summoner_id: str) -> Optional[dict]:
        """サモナーIDからサモナー情報取得"""
        return self._get(self.PLATFORM_URL, f"/lol/summoner/v4/summoners/{summoner_id}")

    def get_summoner_by_puuid(self, puuid: str) -> Optional[dict]:
        """PUUIDからサモナー情報取得"""
        return self._get(self.PLATFORM_URL, f"/lol/summoner/v4/summoners/by-puuid/{puuid}")

    # =============================================
    # League API (ランク情報)
    # =============================================

    def get_challenger_players(self, queue: str = "RANKED_SOLO_5x5") -> list[dict]:
        """チャレンジャープレイヤー一覧取得"""
        data = self._get(self.PLATFORM_URL, f"/lol/league/v4/challengerleagues/by-queue/{queue}")
        if not data:
            return []
        # LP降順でソート
        entries = sorted(data.get("entries", []), key=lambda x: x["leaguePoints"], reverse=True)
        logger.info(f"チャレンジャー {len(entries)} 人取得")
        return entries

    def get_grandmaster_players(self, queue: str = "RANKED_SOLO_5x5") -> list[dict]:
        """グランドマスタープレイヤー一覧取得"""
        data = self._get(self.PLATFORM_URL, f"/lol/league/v4/grandmasterleagues/by-queue/{queue}")
        if not data:
            return []
        return sorted(data.get("entries", []), key=lambda x: x["leaguePoints"], reverse=True)

    def get_master_players(self, queue: str = "RANKED_SOLO_5x5") -> list[dict]:
        """マスタープレイヤー一覧取得"""
        data = self._get(self.PLATFORM_URL, f"/lol/league/v4/masterleagues/by-queue/{queue}")
        if not data:
            return []
        return data.get("entries", [])

    # =============================================
    # Spectator API (ライブゲーム) ※Production APIキーが必要
    # =============================================

    def get_active_game_by_puuid(self, puuid: str) -> Optional[dict]:
        """
        現在進行中の試合情報取得 (PUUID指定)
        ※ Production APIキーが必要。開発用キーでは403。
        """
        return self._get(
            self.PLATFORM_URL,
            f"/lol/spectator/v5/active-games/by-puuid/{puuid}"
        )

    def get_featured_games(self) -> list:
        """注目試合一覧 ※Production APIキーが必要"""
        data = self._get(self.PLATFORM_URL, "/lol/spectator/v5/featured-games")
        if not data:
            return []
        return data.get("gameList", [])

    # =============================================
    # Match API (試合結果・タイムライン)
    # =============================================

    def get_matches_by_puuid(
        self,
        puuid: str,
        queue: int = 420,  # 420 = Ranked Solo/Duo
        count: int = 10,
        start_time: Optional[int] = None,  # Unix秒 (この時刻以降の試合のみ)
    ) -> list[str]:
        """PUUIDから試合IDリスト取得"""
        params: dict = {"queue": queue, "count": count, "type": "ranked"}
        if start_time is not None:
            params["startTime"] = start_time
        data = self._get(
            self.REGIONAL_URL,
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            params=params,
        )
        return data or []

    def get_match(self, match_id: str) -> Optional[dict]:
        """試合詳細データ取得"""
        return self._get(self.REGIONAL_URL, f"/lol/match/v5/matches/{match_id}")

    def get_match_timeline(self, match_id: str) -> Optional[dict]:
        """
        試合タイムライン取得
        - キル・デス・アシスト
        - オブジェクト (バロン、ドラゴン等)
        - 1フレーム = 1分単位のチャンピオン位置
        - イベントは秒単位で記録
        """
        return self._get(self.REGIONAL_URL, f"/lol/match/v5/matches/{match_id}/timeline")

    # =============================================
    # Data Dragon (アセット取得)
    # =============================================

    def get_latest_patch(self) -> str:
        """最新パッチバージョン取得"""
        resp = requests.get(
            "https://ddragon.leagueoflegends.com/api/versions.json",
            timeout=10
        )
        return resp.json()[0] if resp.ok else "14.4.1"

    def get_champion_data(self, patch: str = None) -> dict:
        """全チャンピオンデータ取得"""
        if not patch:
            patch = self.get_latest_patch()
        resp = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{patch}/data/ja_JP/champion.json",
            timeout=10
        )
        return resp.json().get("data", {}) if resp.ok else {}

    def get_champion_icon_url(self, champion_name: str, patch: str = None) -> str:
        """チャンピオンアイコンURLを返す (ダウンロードは別途)"""
        if not patch:
            patch = self.get_latest_patch()
        return f"https://ddragon.leagueoflegends.com/cdn/{patch}/img/champion/{champion_name}.png"

    def download_champion_icon(self, champion_name: str, save_dir: Path, patch: str = None) -> Path:
        """チャンピオンアイコンをローカルに保存"""
        save_dir.mkdir(parents=True, exist_ok=True)
        icon_path = save_dir / f"{champion_name}.png"
        if icon_path.exists():
            return icon_path  # キャッシュ済み

        url = self.get_champion_icon_url(champion_name, patch)
        resp = requests.get(url, timeout=10)
        if resp.ok:
            icon_path.write_bytes(resp.content)
            logger.debug(f"アイコン保存: {champion_name}")
        return icon_path
