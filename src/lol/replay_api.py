"""
LoL Replay API クライアント
ゲームクライアントがリプレイ/スペクテイター中に 127.0.0.1:2999 で動作するAPIを制御する。

主な機能:
- 再生位置の制御 (特定時間にジャンプ)
- カメラ制御 (チャンピオンを追跡、視点変更)
- HUD非表示 (クリーンな映像のため)
- 内蔵録画機能 (OBS不要で指定シーンを録画)
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Optional

import requests
import urllib3
from loguru import logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REPLAY_API_BASE = "https://127.0.0.1:2999"


class ReplayAPIClient:
    """
    LoL 内蔵 Replay API

    参考: https://developer.riotgames.com/docs/lol#game-client-api_replay-api
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False

    def _get(self, path: str) -> Optional[dict]:
        try:
            resp = self.session.get(f"{REPLAY_API_BASE}{path}", timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def _post(self, path: str, data: dict) -> Optional[dict]:
        try:
            resp = self.session.post(
                f"{REPLAY_API_BASE}{path}",
                json=data,
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def is_available(self) -> bool:
        """Replay APIが利用可能か確認"""
        return self._get("/replay/game") is not None

    # =============================================
    # ゲーム情報
    # =============================================

    def get_game_info(self) -> Optional[dict]:
        """現在のゲーム基本情報"""
        return self._get("/replay/game")

    def get_playback(self) -> Optional[dict]:
        """再生状態取得 (時刻、速度、一時停止状態など)"""
        return self._get("/replay/playback")

    def get_render_settings(self) -> Optional[dict]:
        """レンダリング設定取得"""
        return self._get("/replay/render")

    # =============================================
    # 再生制御
    # =============================================

    def set_playback_time(self, game_time: float) -> bool:
        """
        再生位置をゲーム時刻 (秒) に設定する
        例: 600.0 = ゲーム開始から10分
        """
        result = self._post("/replay/playback", {"time": game_time})
        return result is not None

    def set_playback_speed(self, speed: float) -> bool:
        """再生速度設定 (1.0=通常, 0.5=スロー, 4.0=早送り)"""
        result = self._post("/replay/playback", {"speed": speed})
        return result is not None

    def pause(self) -> bool:
        """一時停止"""
        result = self._post("/replay/playback", {"paused": True})
        return result is not None

    def resume(self) -> bool:
        """再生再開"""
        result = self._post("/replay/playback", {"paused": False})
        return result is not None

    def seek_to(self, game_time: float, buffer_seconds: float = 0) -> bool:
        """
        指定した時刻にジャンプし、バッファ分手前から再生開始

        Args:
            game_time: ハイライトのゲーム内時刻 (秒)
            buffer_seconds: 何秒前から再生開始するか
        """
        target = max(0.0, game_time - buffer_seconds)
        logger.debug(f"シーク: {target:.1f}秒 (ハイライト: {game_time:.1f}秒)")
        return self.set_playback_time(target)

    def wait_until(self, target_time: float, check_interval: float = 0.5) -> bool:
        """
        再生位置が target_time に達するまで待機

        Args:
            target_time: ゲーム内時刻 (秒)
            check_interval: チェック間隔 (秒)
        Returns:
            True=到達, False=タイムアウトまたはエラー
        """
        timeout = (target_time + 30) * 2  # 余裕を持ったタイムアウト
        elapsed = 0
        while elapsed < timeout:
            state = self.get_playback()
            if not state:
                return False
            current = state.get("time", 0)
            if current >= target_time:
                return True
            time.sleep(check_interval)
            elapsed += check_interval
        return False

    # =============================================
    # レンダリング設定
    # =============================================

    def hide_hud(self) -> bool:
        """HUD (ミニマップ、HP バーなど) を非表示にする"""
        result = self._post("/replay/render", {
            "interfaceAll": False,
            "interfaceHealth": False,
            "interfaceScore": False,
            "interfaceMinimap": False,
            "interfaceTimers": False,
            "interfaceFrames": False,
        })
        return result is not None

    def show_hud(self) -> bool:
        """HUDを表示する"""
        result = self._post("/replay/render", {
            "interfaceAll": True,
        })
        return result is not None

    def set_camera_follow(self, unit_name: str) -> bool:
        """
        特定のチャンピオンにカメラを追従させる

        Args:
            unit_name: チャンピオン名 (例: "Ahri", "Jinx")
        """
        result = self._post("/replay/render", {
            "cameraAttached": True,
            "selectionName": unit_name,
        })
        return result is not None

    def set_free_camera(self) -> bool:
        """フリーカメラモードに切り替え"""
        result = self._post("/replay/render", {"cameraAttached": False})
        return result is not None

    def set_fov(self, fov: float = 70.0) -> bool:
        """視野角設定 (デフォルト: 70)"""
        result = self._post("/replay/render", {"fieldOfView": fov})
        return result is not None

    # =============================================
    # 内蔵録画機能 (OBS不要)
    # =============================================

    def start_recording(
        self,
        output_path: Path,
        start_time: float,
        end_time: float,
        width: int = 1920,
        height: int = 1080,
        fps: int = 60,
        lossless: bool = False,
    ) -> bool:
        """
        内蔵録画機能でハイライトクリップを録画する

        LOLクライアントが指定した時間範囲を自動録画する。
        録画はバックグラウンドで実行され、完了後にファイルが保存される。

        Args:
            output_path: 出力ファイルパス (.webm)
            start_time: 録画開始 (ゲーム内時刻、秒)
            end_time: 録画終了 (ゲーム内時刻、秒)
            width: 幅
            height: 高さ
            fps: フレームレート
            lossless: ロスレス品質
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "path": str(output_path),
            "codec": "webm",
            "startTime": start_time,
            "endTime": end_time,
            "enforceFrameRate": True,
            "framesPerSecond": fps,
            "height": height,
            "width": width,
            "lossless": lossless,
        }

        logger.info(
            f"録画開始: {output_path.name} "
            f"({start_time:.0f}秒 ~ {end_time:.0f}秒)"
        )
        result = self._post("/replay/recordings", payload)
        return result is not None

    def get_recording_status(self) -> Optional[dict]:
        """録画状態確認"""
        return self._get("/replay/recordings")

    def wait_for_recording_complete(self, expected_path: Path, timeout: int = 300) -> bool:
        """
        録画完了を待機

        Args:
            expected_path: 録画先ファイルパス
            timeout: タイムアウト秒数
        Returns:
            True = 録画完了, False = タイムアウト
        """
        logger.info(f"録画完了待機: {expected_path.name}")
        for _ in range(timeout // 2):
            status = self.get_recording_status()
            if status:
                recording = status.get("recording", True)
                if not recording and expected_path.exists():
                    size = expected_path.stat().st_size
                    if size > 0:
                        logger.info(f"録画完了: {expected_path.name} ({size // 1024}KB)")
                        return True
            time.sleep(2)

        logger.error(f"録画タイムアウト: {expected_path.name}")
        return False

    # =============================================
    # ハイライト録画 (ワンショット)
    # =============================================

    def record_highlight_clip(
        self,
        output_path: Path,
        highlight_time: float,
        pre_buffer: float = 8.0,
        post_buffer: float = 5.0,
        width: int = 1920,
        height: int = 1080,
        fps: int = 60,
        hide_hud: bool = True,
        follow_unit: Optional[str] = None,
    ) -> bool:
        """
        ハイライトの指定時刻周辺を録画する

        Args:
            output_path: 出力ファイルパス
            highlight_time: ハイライトイベントのゲーム内時刻 (秒)
            pre_buffer: ハイライト何秒前から録画開始
            post_buffer: ハイライト何秒後まで録画
            hide_hud: HUDを非表示にするか
            follow_unit: 追従するチャンピオン名 (None=フリーカメラ)
        """
        start_time = max(0.0, highlight_time - pre_buffer)
        end_time = highlight_time + post_buffer

        # カメラ設定
        if hide_hud:
            self.hide_hud()
        if follow_unit:
            self.set_camera_follow(follow_unit)

        # 録画実行 (LOL内蔵録画)
        success = self.start_recording(
            output_path=output_path,
            start_time=start_time,
            end_time=end_time,
            width=width,
            height=height,
            fps=fps,
        )

        if not success:
            logger.error(f"録画開始失敗: {output_path.name}")
            return False

        # 録画完了待機
        clip_duration = end_time - start_time
        return self.wait_for_recording_complete(
            output_path,
            timeout=int(clip_duration * 3 + 30)
        )
