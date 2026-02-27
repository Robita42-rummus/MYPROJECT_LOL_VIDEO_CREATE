"""
LCU (League Client Update) API クライアント
LoLクライアントが起動している間、127.0.0.1:{port} で動作するローカルAPIを操作する。

lockfileの場所: C:/Riot Games/League of Legends/lockfile
フォーマット: {name}:{pid}:{port}:{token}:{protocol}
認証: Basic auth ("riot", {token})
"""
from __future__ import annotations
import base64
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests
import urllib3
from loguru import logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class LCUClient:
    """
    LoLクライアントのローカルAPIクライアント

    主な用途:
    - スペクテイターモードでゲームを起動
    - リプレイファイルの存在確認・起動
    """

    def __init__(self, lol_install_path: str):
        self.lol_install_path = Path(lol_install_path)
        self.lockfile_path = self.lol_install_path / "lockfile"
        self._port: Optional[int] = None
        self._token: Optional[str] = None
        self._session: Optional[requests.Session] = None

    def _read_lockfile(self) -> bool:
        """lockfileを読んでポート番号と認証トークンを取得"""
        if not self.lockfile_path.exists():
            return False
        try:
            content = self.lockfile_path.read_text()
            # フォーマット: LeagueClient:12345:65432:token:https
            parts = content.strip().split(":")
            if len(parts) < 5:
                return False
            self._port = int(parts[2])
            self._token = parts[3]
            return True
        except Exception as e:
            logger.error(f"lockfileの読み込みに失敗: {e}")
            return False

    def _get_session(self) -> Optional[requests.Session]:
        if not self._read_lockfile():
            return None
        if self._session is None:
            token_b64 = base64.b64encode(f"riot:{self._token}".encode()).decode()
            session = requests.Session()
            session.verify = False
            session.headers.update({
                "Authorization": f"Basic {token_b64}",
                "Content-Type": "application/json",
            })
            self._session = session
        return self._session

    def _lcu_get(self, path: str) -> Optional[dict]:
        session = self._get_session()
        if not session:
            logger.error("LCU: クライアントが起動していません")
            return None
        try:
            resp = session.get(f"https://127.0.0.1:{self._port}{path}", timeout=5)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            logger.error(f"LCU GET {path} 失敗: {e}")
            return None

    def _lcu_post(self, path: str, data: dict = None) -> Optional[dict]:
        session = self._get_session()
        if not session:
            return None
        try:
            resp = session.post(
                f"https://127.0.0.1:{self._port}{path}",
                json=data or {},
                timeout=10
            )
            if resp.status_code in (200, 201, 204):
                return resp.json() if resp.content else {}
            logger.warning(f"LCU POST {path} → {resp.status_code}: {resp.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"LCU POST {path} 失敗: {e}")
            return None

    def is_client_running(self) -> bool:
        """クライアントが起動しているか確認"""
        return self._lcu_get("/lol-summoner/v1/current-summoner") is not None

    def wait_for_client(self, timeout: int = 120) -> bool:
        """クライアントが起動するまで待機"""
        logger.info("LoLクライアントの起動を待機中...")
        for _ in range(timeout // 5):
            if self.is_client_running():
                logger.info("クライアント起動確認")
                return True
            time.sleep(5)
        logger.error("クライアントの起動タイムアウト")
        return False

    def get_current_summoner(self) -> Optional[dict]:
        """現在ログイン中のサモナー情報"""
        return self._lcu_get("/lol-summoner/v1/current-summoner")

    # =============================================
    # スペクテイター
    # =============================================

    def launch_spectator(
        self,
        game_id: int,
        encryption_key: str,
        platform_id: str = "JP1",
        spectator_host: str = "spectator-consumer.jp01.lol.pvp.net",
        spectator_port: int = 8088,
    ) -> bool:
        """
        スペクテイターモードでゲームを起動する

        LCUのエンドポイント経由でスペクテイターを起動。
        失敗した場合はコマンドライン経由でも試みる。
        """
        logger.info(f"スペクテイター起動: gameId={game_id}")

        # 方法1: LCU API経由
        result = self._lcu_post("/lol-spectator/v1/spectate/launch", {
            "gameId": game_id,
            "encryptionKey": encryption_key,
            "platformId": platform_id,
        })
        if result is not None:
            logger.info("LCU API経由でスペクテイター起動成功")
            return True

        # 方法2: コマンドライン経由 (フォールバック)
        logger.info("LCU API失敗 → コマンドライン経由で試行")
        return self._launch_spectator_cli(
            game_id, encryption_key, platform_id,
            spectator_host, spectator_port
        )

    def _launch_spectator_cli(
        self,
        game_id: int,
        encryption_key: str,
        platform_id: str,
        spectator_host: str,
        spectator_port: int,
    ) -> bool:
        """コマンドライン経由でスペクテイターを起動"""
        game_exe = self.lol_install_path / "Game" / "League of Legends.exe"
        if not game_exe.exists():
            logger.error(f"LOL実行ファイルが見つかりません: {game_exe}")
            return False

        cmd = [
            str(game_exe),
            f"spectator {spectator_host}:{spectator_port}",
            encryption_key,
            str(game_id),
            platform_id,
        ]
        try:
            subprocess.Popen(cmd)
            logger.info(f"スペクテイター起動コマンド実行: {' '.join(cmd)}")
            time.sleep(5)  # 起動待ち
            return True
        except Exception as e:
            logger.error(f"スペクテイター起動失敗: {e}")
            return False

    # =============================================
    # リプレイ管理
    # =============================================

    def get_replay_state(self, game_id: int) -> Optional[str]:
        """
        リプレイの状態を取得
        Returns: "watch" (再生可能), "download" (DL可能), None (不明/なし)

        正しいエンドポイント: GET /lol-replays/v1/metadata/{gameId}
        """
        data = self._lcu_get(f"/lol-replays/v1/metadata/{game_id}")
        if data:
            return data.get("state")
        return None

    def scan_local_replays(self) -> bool:
        """ローカルのroflファイルをスキャンしてLCUに認識させる"""
        result = self._lcu_post("/lol-replays/v1/rofls/scan")
        return result is not None

    def get_replay_folder(self) -> Optional[Path]:
        """リプレイ保存フォルダのパスを取得"""
        data = self._lcu_get("/lol-replays/v1/rofls/path")
        if data:
            folder = data if isinstance(data, str) else str(data)
            return Path(folder.strip('"'))
        return None

    def download_replay(self, game_id: int) -> bool:
        """
        リプレイをダウンロード
        ※ ローカルにroflがない場合のみ有効。Riot側に存在する期間のみDL可能。
        """
        result = self._lcu_post(f"/lol-replays/v1/rofls/{game_id}/download")
        return result is not None

    def launch_replay(self, game_id: int) -> bool:
        """
        リプレイをクライアントで再生開始

        事前にスキャンしてリプレイを認識させてから起動する。
        roflファイルがローカルに存在しない場合は失敗する。
        """
        logger.info(f"リプレイ起動: gameId={game_id}")

        # ローカルをスキャンしてLCUに認識させる
        self.scan_local_replays()
        time.sleep(1)

        # 状態を確認
        state = self.get_replay_state(game_id)
        if state is None:
            logger.error(
                f"リプレイが見つかりません (gameId={game_id})\n"
                "roflファイルがローカルに存在しない可能性があります。\n"
                "自分でプレイ/スペクテイターした試合のみ起動可能です。"
            )
            return False

        logger.info(f"リプレイ状態: {state}")

        # 再生開始
        result = self._lcu_post(f"/lol-replays/v1/rofls/{game_id}/watch")
        if result is not None:
            logger.info("リプレイ再生コマンド送信成功 (ゲームクライアント起動中...)")
            return True

        logger.error(f"リプレイ再生コマンド失敗: gameId={game_id}")
        return False

    def wait_for_replay_ready(self, timeout: int = 180) -> bool:
        """
        リプレイAPIが使用可能になるまで待機
        LoLゲームクライアントの起動に1〜3分かかる場合がある。

        Args:
            timeout: 最大待機秒数 (デフォルト: 180秒)
        """
        from ..lol.replay_api import ReplayAPIClient
        replay_api = ReplayAPIClient()
        logger.info(f"Replay API の起動を待機中 (最大{timeout}秒)...")
        for i in range(timeout // 5):
            if replay_api.is_available():
                logger.info("Replay API 利用可能")
                return True
            elapsed = (i + 1) * 5
            if elapsed % 30 == 0:
                logger.info(f"  待機中... {elapsed}秒経過")
            time.sleep(5)
        logger.error(f"Replay API タイムアウト ({timeout}秒)")
        return False
