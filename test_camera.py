"""
カメラ制御テストスクリプト (Interception版)
リプレイを起動してカメラキーが動作するか確認する。
"""
from __future__ import annotations
import sys
import time

import requests
import urllib3
import yaml
from loguru import logger

urllib3.disable_warnings()
logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

from src.lol.lcu_client import LCUClient
from record_matches import _send_key, press_jungler_key, SCAN_2, SCAN_W, _INTERCEPTION_OK

REPLAY_BASE = "https://127.0.0.1:2999"


def wait_for_replay(timeout: int = 300) -> bool:
    logger.info("リプレイ API 起動待機中...")
    for i in range(timeout):
        try:
            r = requests.get(f"{REPLAY_BASE}/liveclientdata/gamestats", timeout=3, verify=False)
            if r.status_code == 200:
                logger.info(f"  Replay API 起動確認 ({i}秒)")
                return True
        except Exception:
            pass
        if i % 10 == 9:
            logger.info(f"  待機中... {i+1}秒経過")
        time.sleep(1)
    return False


def seek_to_start():
    for i in range(10):
        try:
            requests.post(f"{REPLAY_BASE}/replay/playback",
                          json={"paused": True, "time": 0.0}, timeout=5, verify=False)
            time.sleep(2.0)
            requests.post(f"{REPLAY_BASE}/replay/playback",
                          json={"paused": False, "speed": 1.0}, timeout=5, verify=False)
            logger.info("  t=0 にシーク → 再生開始")
            return
        except Exception:
            if i < 9:
                logger.info(f"  シーク待機中... ({i+1}/10)")
                time.sleep(3)
    logger.warning("  seek_to_start 失敗")


def main():
    logger.info(f"Interception OK: {_INTERCEPTION_OK}")

    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    lcu = LCUClient(config["lol"]["install_path"])

    game_id = int(sys.argv[1]) if len(sys.argv) > 1 else 568697084
    team_id = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    logger.info(f"=== カメラ制御テスト === game_id={game_id}, team={'BLUE' if team_id==100 else 'RED'}")

    # 既存LoLを終了
    import subprocess
    subprocess.run(["taskkill", "/F", "/IM", "League of Legends.exe"], capture_output=True)
    time.sleep(3)

    # リプレイ起動
    logger.info("リプレイ起動中...")
    if not lcu.launch_replay(game_id):
        logger.error("リプレイ起動失敗")
        sys.exit(1)

    # Replay API 待機
    if not wait_for_replay(timeout=300):
        logger.error("Replay API タイムアウト")
        sys.exit(1)

    time.sleep(5)
    seek_to_start()

    # 再生開始まで待つ
    logger.info("再生開始待機中...")
    for _ in range(30):
        try:
            r = requests.get(f"{REPLAY_BASE}/replay/playback", timeout=3, verify=False)
            if r.status_code == 200 and r.json().get("time", 0) > 1.0:
                logger.info(f"  再生確認 t={r.json().get('time', 0):.1f}s")
                break
        except Exception:
            pass
        time.sleep(1)

    time.sleep(3)

    # カメラキー注入
    logger.info(f"カメラキー注入: {'BLUE→キー2' if team_id==100 else 'RED→キーW'}")
    press_jungler_key(team_id)
    logger.info("注入完了 → カメラが動いたか確認してください")

    # 10秒後にもう一度
    logger.info("10秒後に再注入します...")
    time.sleep(10)
    press_jungler_key(team_id)
    logger.info("再注入完了")


if __name__ == "__main__":
    main()
