"""
アップロード済み動画と関連ファイルを自動削除するスクリプト

削除対象:
  - output/videos/{video_filename}  (リネーム済み mp4)
  - output/videos/{game_id}.mp4     (リネーム前 mp4 が残っている場合)
  - output/videos/{game_id}.json    (メタデータ)
  - output/thumbnails/{game_id}.jpg (サムネイル)

条件:
  - youtube_url が登録済み (アップロード完了)
  - recorded_at から cleanup_after_days 日以上経過

使い方:
  python cleanup.py              # config.yaml の cleanup_after_days を使用
  python cleanup.py --days 14   # 14日以上経過したものを削除
  python cleanup.py --dry-run   # 削除対象を表示するのみ (実際には削除しない)
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

OUTPUT_DIR     = Path("output/videos")
THUMBNAILS_DIR = Path("output/thumbnails")


def run(cleanup_after_days: int = 30, dry_run: bool = False) -> int:
    """
    アップロード済みの古いファイルを削除する。

    Args:
        cleanup_after_days: 録画完了から何日後に削除するか
        dry_run: True の場合は削除せず対象を表示するのみ

    Returns:
        削除したファイル数 (dry_run の場合は対象ファイル数)
    """
    from src.db.match_registry import get_all

    rows = get_all()
    cutoff = datetime.now() - timedelta(days=cleanup_after_days)

    deleted_count = 0
    target_count  = 0

    logger.info(f"{'[DRY-RUN] ' if dry_run else ''}削除チェック開始 "
                f"(基準: {cleanup_after_days}日以上経過 + アップロード済み)")

    for row in rows:
        # アップロード済みかチェック
        if not row.get("youtube_url"):
            continue

        # recorded_at のパース
        recorded_at_str = row.get("recorded_at", "")
        if not recorded_at_str:
            continue
        try:
            recorded_at = datetime.strptime(recorded_at_str, "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                recorded_at = datetime.fromisoformat(recorded_at_str)
            except ValueError:
                continue

        # 経過日数チェック
        if recorded_at > cutoff:
            continue

        game_id  = row.get("game_id", "")
        age_days = (datetime.now() - recorded_at).days

        # 削除対象ファイルを列挙
        targets: list[Path] = []

        # mp4 (リネーム済みファイル)
        vf = row.get("video_filename", "")
        if vf:
            titled_mp4 = OUTPUT_DIR / vf
            if titled_mp4.exists():
                targets.append(titled_mp4)

        # mp4 (古い game_id ベースの名前が残っている場合)
        old_mp4 = OUTPUT_DIR / f"{game_id}.mp4"
        if old_mp4.exists() and old_mp4 not in targets:
            targets.append(old_mp4)

        # JSON
        json_path = OUTPUT_DIR / f"{game_id}.json"
        if json_path.exists():
            targets.append(json_path)

        # サムネイル
        thumb_path = THUMBNAILS_DIR / f"{game_id}.jpg"
        if thumb_path.exists():
            targets.append(thumb_path)

        if not targets:
            continue

        target_count += len(targets)
        logger.info(f"  game_id={game_id}  ({age_days}日前 / {row.get('champion','?')} "
                    f"/ {row.get('youtube_url','')})")
        for t in targets:
            size_mb = t.stat().st_size // 1024 // 1024
            logger.info(f"    {'[削除予定]' if dry_run else '[削除]'} {t}  ({size_mb}MB)")
            if not dry_run:
                t.unlink()
                deleted_count += 1
            else:
                deleted_count += 1

    if deleted_count == 0:
        logger.info(f"削除対象なし (アップロード済み + {cleanup_after_days}日以上の試合はありません)")
    else:
        label = "削除予定" if dry_run else "削除完了"
        logger.info(f"{label}: {deleted_count} ファイル")

    return deleted_count


def main():
    import argparse
    import yaml

    parser = argparse.ArgumentParser(description="アップロード済み動画の自動削除")
    parser.add_argument("--days",    type=int, default=None,  help="削除基準日数 (未指定は config.yaml の値)")
    parser.add_argument("--dry-run", action="store_true",     help="削除せずに対象を表示のみ")
    args = parser.parse_args()

    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    cleanup_after_days = args.days
    if cleanup_after_days is None:
        cleanup_after_days = config.get("cleanup", {}).get("cleanup_after_days", 30)

    run(cleanup_after_days=cleanup_after_days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
