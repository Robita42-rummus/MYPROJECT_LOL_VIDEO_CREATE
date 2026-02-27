"""
LOL チャレンジャー JG 自動録画・投稿パイプライン

使い方:
    python main.py              # 全試合を録画 → サムネイル生成 → YouTube アップロード
    python main.py --record-only  # 録画のみ (アップロードしない)
    python main.py --upload-only  # 未アップロードの録画済み試合をまとめてアップロード
    python main.py --max 3        # 最大3試合まで処理 (デフォルト: MAX_MATCHES の設定値)
"""
from __future__ import annotations

import queue
import sys
import threading
import argparse
from pathlib import Path

import yaml
from loguru import logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logger.remove()
logger.add(
    sys.stdout, level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
)
Path("logs").mkdir(exist_ok=True)
logger.add("logs/main.log", level="DEBUG", rotation="50MB", retention="7 days")


def load_config() -> dict:
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


_STOP_SENTINEL = None  # アップロードキューの終了シグナル


def _upload_worker(q: queue.Queue, config: dict, results: dict):
    """
    アップロードスレッドのワーカー。
    キューから game_id を取り出し、サムネイル生成 → アップロードを逐次処理する。
    None を受け取ったら終了する。
    """
    from create_thumbnails import generate as thumb_generate
    from upload_video import upload_game

    while True:
        game_id = q.get()
        if game_id is _STOP_SENTINEL:
            q.task_done()
            break
        try:
            logger.info(f"[アップロード開始] {game_id}")
            thumb_generate([game_id])
            url = upload_game(game_id, config)
            if url:
                results[game_id] = url
                logger.info(f"[アップロード完了] {game_id} → {url}")
            else:
                logger.error(f"[アップロード失敗] {game_id}")
        except Exception as e:
            logger.error(f"[アップロードエラー] {game_id}: {e}")
        finally:
            q.task_done()


def step_record_parallel(max_matches: int, config: dict, record_only: bool) -> tuple[list[int], dict[int, str]]:
    """
    録画しながら並行してアップロードを行う。
    1試合録画完了 → すぐにアップロードキューへ投入。
    Returns: (録画済み game_id リスト, アップロード結果 {game_id: url})
    """
    from record_matches import run as record_run

    upload_results: dict[int, str] = {}

    if record_only:
        # アップロードなし: そのまま録画のみ
        logger.info("=" * 60)
        logger.info("録画 (アップロードなし)")
        logger.info("=" * 60)
        return record_run(max_matches=max_matches), {}

    # アップロードキューとワーカースレッドを起動
    upload_q: queue.Queue = queue.Queue()
    worker = threading.Thread(
        target=_upload_worker,
        args=(upload_q, config, upload_results),
        daemon=True,
        name="uploader",
    )
    worker.start()
    logger.info("=" * 60)
    logger.info("録画 + 並行アップロード")
    logger.info("=" * 60)

    def on_recorded(game_id: int):
        logger.info(f"[キュー追加] {game_id} → アップロード待機")
        upload_q.put(game_id)

    recorded = record_run(max_matches=max_matches, on_recorded=on_recorded)

    # 録画完了 → 終了シグナルを送ってワーカーが全件処理するのを待つ
    upload_q.put(_STOP_SENTINEL)
    logger.info("録画完了。アップロード完了待機中...")
    worker.join()
    logger.info("全アップロード完了")

    return recorded, upload_results


def step_thumbnails(game_ids: list[int]) -> None:
    """指定 game_id のサムネイルを生成する (upload-only モード用)"""
    if not game_ids:
        return
    from create_thumbnails import generate as thumb_generate
    thumb_generate(game_ids)


def step_upload(game_ids: list[int], config: dict) -> dict[int, str]:
    """指定 game_id を YouTube にアップロードする (upload-only モード用)"""
    if not game_ids:
        return {}
    from upload_video import upload_game
    results = {}
    for gid in game_ids:
        logger.info(f"アップロード: {gid}")
        url = upload_game(gid, config)
        if url:
            results[gid] = url
            logger.info(f"  ✓ {url}")
        else:
            logger.error(f"  ✗ アップロード失敗: {gid}")
    return results


def get_unuploaded_game_ids() -> list[int]:
    """match_list.csv から youtube_url が空の game_id を返す (動画ファイルが存在するもの)"""
    from src.db.match_registry import get_all, get_row
    rows = get_all()
    ids = [int(r["game_id"]) for r in rows if not r.get("youtube_url")]
    videos_dir = Path("output/videos")
    result = []
    for gid in ids:
        # {game_id}.mp4 (古い形式) を確認
        if (videos_dir / f"{gid}.mp4").exists():
            result.append(gid)
            continue
        # video_filename (タイトルリネーム済み) を確認
        row = get_row(str(gid))
        if row and row.get("video_filename"):
            if (videos_dir / row["video_filename"]).exists():
                result.append(gid)
    return result


def show_summary(uploaded: dict[int, str]) -> None:
    """完了後にサマリーを表示"""
    from src.db.match_registry import get_all
    rows = get_all()
    total    = len(rows)
    yt_done  = sum(1 for r in rows if r.get("youtube_url"))

    logger.info("\n" + "=" * 60)
    logger.info("完了サマリー")
    logger.info("=" * 60)
    logger.info(f"  総試合数:          {total} 件")
    logger.info(f"  YouTube投稿済み:   {yt_done} 件")
    if uploaded:
        logger.info(f"  今回アップロード:  {len(uploaded)} 件")
        for gid, url in uploaded.items():
            logger.info(f"    {gid} → {url}")
    logger.info(f"\n試合一覧: python match_list.py --show")


def main():
    parser = argparse.ArgumentParser(description="LoL 自動録画・投稿パイプライン")
    parser.add_argument("--record-only",  action="store_true", help="録画のみ (アップロードしない)")
    parser.add_argument("--upload-only",  action="store_true", help="未アップロード試合をアップロードのみ")
    parser.add_argument("--cleanup",      action="store_true", help="アップロード済み古いファイルを削除")
    parser.add_argument("--dry-run",      action="store_true", help="--cleanup の削除対象を表示のみ")
    parser.add_argument("--max",          type=int, default=None, help="最大処理試合数")
    args = parser.parse_args()

    config = load_config()

    # --- cleanup モード ---
    if args.cleanup:
        from cleanup import run as cleanup_run
        days = config.get("cleanup", {}).get("cleanup_after_days", 30)
        cleanup_run(cleanup_after_days=days, dry_run=args.dry_run)
        return

    # --- upload-only モード ---
    if args.upload_only:
        unuploaded = get_unuploaded_game_ids()
        if not unuploaded:
            logger.info("未アップロードの録画済み試合はありません")
            return
        logger.info(f"未アップロード: {len(unuploaded)} 件")
        step_thumbnails(unuploaded)
        uploaded = step_upload(unuploaded, config)
        show_summary(uploaded)
        return

    # --- 通常モード (録画しながら並行アップロード) ---
    from record_matches import MAX_MATCHES
    max_matches = args.max if args.max is not None else MAX_MATCHES

    game_ids, uploaded = step_record_parallel(max_matches, config, args.record_only)

    if not game_ids:
        logger.warning("録画できた試合がありませんでした")
        return

    show_summary(uploaded)


if __name__ == "__main__":
    main()
