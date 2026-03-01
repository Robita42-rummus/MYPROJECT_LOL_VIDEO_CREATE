"""
チャレンジャー試合のリプレイを自動収集・ダウンロードする

発見: LCUの POST /lol-replays/v1/rofls/{gameId}/download は
現在パッチのゲームなら誰の試合でもダウンロード可能。
古いパッチは state=incompatible になりDL不可。
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Optional

import requests
import urllib3
from loguru import logger

from .lcu_client import LCUClient
from ..riot.api_client import RiotAPIClient

urllib3.disable_warnings()


class ReplayDownloader:
    """
    チャレンジャー試合のリプレイをまとめてダウンロードする

    フロー:
    1. Riot API でチャレンジャー選手の最新マッチIDを収集
    2. 現在のLCUパッチバージョンを取得
    3. 同パッチの試合のみフィルタリング
    4. LCU download API で .rofl ファイルをダウンロード
    5. ダウンロード完了を確認
    """

    def __init__(self, lcu: LCUClient, riot_client: RiotAPIClient):
        self.lcu = lcu
        self.riot_client = riot_client
        self._current_patch: Optional[str] = None

    def get_current_patch(self) -> str:
        """LCUから現在のゲームバージョン(パッチ)を取得"""
        if self._current_patch:
            return self._current_patch

        data = self.lcu._lcu_get("/lol-replays/v1/configuration")
        if data and "gameVersion" in data:
            # "16.4.748.0682" → "16.4"
            ver = data["gameVersion"]
            patch = ".".join(ver.split(".")[:2])
            self._current_patch = patch
            logger.info(f"現在パッチ: {patch}")
            return patch

        # フォールバック: Riot APIから取得
        patch = self.riot_client.get_latest_patch()
        self._current_patch = ".".join(patch.split(".")[:2])
        return self._current_patch

    def collect_jungle_players(self, top_n: int = 100) -> list[dict]:
        """
        Challenger + Grandmaster を LP 降順で合算し、上位 top_n 人を取得して
        ロールを判定し、JUNGLEプレイヤーのリストを返す。

        PlayerTracker でキャッシュを使い、未登録・期限切れのみ再判定。

        Returns:
            [{"puuid": "...", "game_name": "...", "tag": "...", "lp": 0, "tier": "Challenger"}]
        """
        from .player_tracker import PlayerTracker

        tracker = PlayerTracker()

        # Challenger + Grandmaster を合算して LP 降順上位 top_n 人を取得
        challenger  = self.riot_client.get_challenger_players()
        grandmaster = self.riot_client.get_grandmaster_players()

        all_entries = []
        for e in challenger:
            e["_tier"] = "Challenger"
            all_entries.append(e)
        for e in grandmaster:
            e["_tier"] = "Grandmaster"
            all_entries.append(e)

        all_entries.sort(key=lambda x: x["leaguePoints"], reverse=True)
        entries = all_entries[:top_n]

        ch_count = sum(1 for e in entries if e.get("_tier") == "Challenger")
        gm_count = sum(1 for e in entries if e.get("_tier") == "Grandmaster")
        logger.info(
            f"Challenger {ch_count}人 + Grandmaster {gm_count}人 = 上位 {len(entries)} 人を対象にロール判定..."
        )

        player_infos = []
        for e in entries:
            puuid = e.get("puuid")
            if not puuid:
                summoner = self.riot_client.get_summoner_by_id(e.get("summonerId", ""))
                if summoner:
                    puuid = summoner.get("puuid", "")
            if not puuid:
                continue
            player_infos.append({
                "puuid":     puuid,
                "game_name": e.get("summonerName", ""),
                "tag":       "",
                "lp":        e.get("leaguePoints", 0),
                "tier":      e.get("_tier", "Challenger"),
            })

        new_count, skip_count = tracker.bulk_detect(player_infos, self.riot_client)
        logger.info(f"ロール判定: 新規 {new_count} 人 / キャッシュ済み {skip_count} 人")

        summary = tracker.summary()
        role_str = "  ".join(f"{r}:{n}" for r, n in sorted(summary.items()))
        logger.info(f"ロール内訳 (キャッシュ全体): {role_str}")

        jungle_puuids = set(tracker.get_jungle_puuids())
        jungle_players = [p for p in player_infos if p["puuid"] in jungle_puuids]
        logger.info(f"上位 {top_n} 人中 JUNGLE: {len(jungle_players)} 人")
        return jungle_players

    def find_downloadable_matches(
        self,
        target_tiers: list = None,  # 後方互換のため残す (未使用)
        max_matches: int = 200,
        max_age_hours: float = 24.0,
        top_n: int = 100,
    ) -> list[dict]:
        """
        チャレンジャー上位 top_n 人のうち JUNGLE ロールのプレイヤーが
        直近 max_age_hours 時間以内にプレイした試合を全件収集する。

        フロー:
        1. 上位 top_n 人を取得
        2. PlayerTracker でロール判定 (キャッシュ活用)
        3. JUNGLEプレイヤーの直近24時間の全試合を収集
        4. 現パッチのみ、勝利JGが対象プレイヤーの試合のみ返す

        Returns:
            [{"match_id": "JP1_xxx", "game_id": 123, ...}]
        """
        from datetime import datetime, timezone

        current_patch = self.get_current_patch()
        logger.info(f"現在パッチ {current_patch} の試合を収集中...")

        jungle_players = self.collect_jungle_players(top_n=top_n)
        if not jungle_players:
            logger.warning("JUNGLEプレイヤーが見つかりません")
            return []

        jungle_puuid_set = {p["puuid"] for p in jungle_players}

        now = datetime.now(timezone.utc)
        start_time_sec = int(now.timestamp() - max_age_hours * 3600)

        results = []
        seen_match_ids: set = set()

        for player in jungle_players:
            if len(results) >= max_matches:
                break

            puuid = player["puuid"]
            match_ids = self.riot_client.get_matches_by_puuid(
                puuid, queue=420, count=20, start_time=start_time_sec
            )

            for match_id in match_ids:
                if match_id in seen_match_ids:
                    continue
                seen_match_ids.add(match_id)

                match = self.riot_client.get_match(match_id)
                if not match:
                    continue

                info = match.get("info", {})
                game_version = info.get("gameVersion", "")
                match_patch = ".".join(game_version.split(".")[:2])

                # 現パッチ以外はスキップ (LCUでDL不可)
                if match_patch != current_patch:
                    continue

                start_ms = info.get("gameStartTimestamp", 0)
                age_hours = 0.0
                if start_ms:
                    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
                    age_hours = (now - start_dt).total_seconds() / 3600

                # 24時間以上前の試合はスキップ
                if age_hours > max_age_hours:
                    continue

                # 15分以下の試合はスキップ (短すぎる試合 = 早期降参/remake 等)
                game_duration_sec = info.get("gameDuration", 0)
                if game_duration_sec < 15 * 60:
                    logger.debug(f"短い試合をスキップ ({game_duration_sec // 60}分): {match_id}")
                    continue

                # 勝利ジャングラーが対象プレイヤーかチェック
                winning_jungler_puuid = None
                jungler = {}
                for p in info.get("participants", []):
                    if p.get("teamPosition") == "JUNGLE" and p.get("win"):
                        winning_jungler_puuid = p.get("puuid")
                        gn  = p.get("riotIdGameName", "")
                        tag = p.get("riotIdTagline", "")
                        jungler = {
                            "champion": p.get("championName", ""),
                            "player":   f"{gn}#{tag}" if tag else gn,
                            "kills":    p.get("kills", 0),
                            "deaths":   p.get("deaths", 0),
                            "assists":  p.get("assists", 0),
                            "cs":       p.get("totalMinionsKilled", 0),
                            "vision":   p.get("visionScore", 0),
                            "gold":     p.get("goldEarned", 0),
                            "team_id":  p.get("teamId", 100),
                            "puuid":    p.get("puuid", ""),
                        }
                        break

                if winning_jungler_puuid not in jungle_puuid_set:
                    continue

                game_id = info.get("gameId", 0)
                results.append({
                    "match_id":      match_id,
                    "game_id":       game_id,
                    "game_version":  game_version,
                    "age_hours":     age_hours,
                    "game_start_ms": start_ms,
                    "participants":  [p.get("championName", "") for p in info.get("participants", [])],
                    **jungler,
                })

        # 新しい順にソート (age_hours 昇順 = 新しい順)
        results.sort(key=lambda r: r["age_hours"])

        # 同一プレイヤーの試合は最新1件のみ残す
        # (1日5件制限があるため、同じプレイヤーの複数試合は避ける)
        seen_puuids: set = set()
        deduped = []
        for r in results:
            p = r.get("puuid", "")
            if p and p in seen_puuids:
                logger.debug(
                    f"同一プレイヤーの重複試合をスキップ: {r.get('player')} ({r['match_id']})"
                )
                continue
            if p:
                seen_puuids.add(p)
            deduped.append(r)
        if len(deduped) < len(results):
            logger.info(
                f"同一プレイヤー重複除去: {len(results)} → {len(deduped)} 件"
            )
        results = deduped

        logger.info(f"ダウンロード候補 {len(results)} 件")
        return results

    def download_replay(
        self,
        game_id: int,
        wait_timeout: int = 120,
    ) -> bool:
        """
        LCU経由でリプレイをダウンロード

        Args:
            game_id: ゲームID (数値)
            wait_timeout: ダウンロード完了待ちのタイムアウト秒
        Returns:
            True = 成功, False = 失敗
        """
        replay_folder = self.lcu.get_replay_folder()
        if not replay_folder:
            logger.error("リプレイフォルダが取得できません")
            return False

        rofl_path = replay_folder / f"JP1-{game_id}.rofl"

        # すでにある場合はスキップ
        if rofl_path.exists() and rofl_path.stat().st_size > 1024 * 1024:
            logger.info(f"既存rofl: {rofl_path.name} ({rofl_path.stat().st_size // 1024 // 1024}MB)")
            return True

        logger.info(f"ダウンロード開始: JP1-{game_id}.rofl")

        result = self.lcu.download_replay(game_id)
        if not result:
            logger.warning(f"download APIが失敗: {game_id}")
            return False

        # ダウンロード完了を待機
        for i in range(wait_timeout // 3):
            time.sleep(3)

            # ファイルが存在するか確認
            if rofl_path.exists() and rofl_path.stat().st_size > 1024 * 1024:
                size_mb = rofl_path.stat().st_size // 1024 // 1024
                logger.info(f"ダウンロード完了: {rofl_path.name} ({size_mb}MB)")
                return True

            # LCU状態確認
            state = self.lcu.get_replay_state(game_id)
            if state == "incompatible":
                logger.warning(f"パッチ不一致でDL不可: {game_id}")
                return False
            if state == "watch":
                # watchになったらファイルあり
                if rofl_path.exists():
                    size_mb = rofl_path.stat().st_size // 1024 // 1024
                    logger.info(f"ダウンロード完了: {rofl_path.name} ({size_mb}MB)")
                    return True

        logger.error(f"ダウンロードタイムアウト: {game_id}")
        return False

    def bulk_download(
        self,
        target_tiers: list = None,
        max_new_downloads: int = 5,
        already_processed: set = None,
    ) -> list[dict]:
        """
        チャレンジャー試合をまとめてダウンロードし、
        ダウンロードできた試合のリストを返す

        Args:
            max_new_downloads: 最大新規ダウンロード数
            already_processed: 処理済みのmatch_idセット (スキップ用)
        Returns:
            ダウンロード済み試合のリスト
        """
        if already_processed is None:
            already_processed = set()

        candidates = self.find_downloadable_matches(
            target_tiers=target_tiers or ["CHALLENGER", "GRANDMASTER"],
            max_matches=max_new_downloads * 3,  # 失敗を考慮して多めに取得
        )

        downloaded = []
        for match in candidates:
            if match["match_id"] in already_processed:
                continue
            if len(downloaded) >= max_new_downloads:
                break

            success = self.download_replay(match["game_id"])
            if success:
                downloaded.append(match)

        logger.info(f"新規ダウンロード: {len(downloaded)} 件")
        return downloaded
