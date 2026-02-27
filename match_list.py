"""
試合リストを表示・再構築するスクリプト

使い方:
    python match_list.py           # output/videos/*.json から再構築して一覧表示
    python match_list.py --show    # CSVを読んで一覧表示のみ (再構築なし)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from loguru import logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

from src.db.match_registry import upsert, get_all, REGISTRY_PATH

VIDEOS_DIR     = Path("output/videos")
THUMBNAILS_DIR = Path("output/thumbnails")


def rebuild():
    """output/videos/*.json を全スキャンして match_list.csv を再構築"""
    jsons = sorted(VIDEOS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not jsons:
        logger.warning("output/videos/ にJSONが見つかりません")
        return

    logger.info(f"{len(jsons)} 件のJSONから再構築中...")
    for json_path in jsons:
        meta = json.loads(json_path.read_text(encoding="utf-8"))
        video_path = VIDEOS_DIR / f"{json_path.stem}.mp4"
        upsert(meta, video_path=video_path)

    logger.info(f"保存: {REGISTRY_PATH}")


def show():
    """match_list.csv の内容を表形式で表示"""
    rows = get_all()
    if not rows:
        logger.warning("試合リストが空です。'python match_list.py' で再構築してください")
        return

    # ヘッダー
    print(f"\n{'No':>3}  {'GameID':<12} {'日時':<18} {'チャンピオン':<14} {'プレイヤー':<22} {'KDA':<10} {'パッチ':<6}  {'MB':>5}  {'YouTube'}")
    print("-" * 120)

    for i, row in enumerate(rows, 1):
        kda = f"{row['kills']}/{row['deaths']}/{row['assists']}"
        yt  = row.get("youtube_url", "")
        yt_disp = yt if yt else "-"
        print(
            f"{i:>3}  {row['game_id']:<12} {row['game_date']:<18} {row['champion']:<14} "
            f"{row['player']:<22} {kda:<10} {row['patch']:<6}  {row['video_mb']:>5}  {yt_disp}"
        )

    uploaded = sum(1 for r in rows if r.get("youtube_url"))
    print(f"\n合計: {len(rows)} 試合  (YouTube投稿済: {uploaded} 件)")
    print(f"CSV: {REGISTRY_PATH.resolve()}\n")


def main():
    show_only = "--show" in sys.argv

    if not show_only:
        rebuild()

    show()


if __name__ == "__main__":
    main()
