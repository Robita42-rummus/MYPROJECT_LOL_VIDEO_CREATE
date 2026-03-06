"""
録画済み動画を YouTube にアップロードする

使い方:
    python upload_video.py              # 最新の録画をアップロード
    python upload_video.py 567287391    # 指定 game_id をアップロード

前提条件:
    credentials/youtube_client_secret.json が必要。
    Google Cloud Console → YouTube Data API v3 → OAuth2 クライアント ID (デスクトップ) を作成してダウンロード。
    初回実行時にブラウザで認証 → credentials/youtube_token.json に保存される。
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import yaml
from loguru import logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

from src.youtube.uploader import YouTubeUploader

OUTPUT_DIR     = Path("output/videos")
THUMBNAILS_DIR = Path("output/thumbnails")
ASSETS_DIR     = Path("assets")


def _load_rune_names() -> dict:
    """assets/runesReforged.json から {rune_id: name} マップを返す"""
    path = ASSETS_DIR / "runesReforged.json"
    if not path.exists():
        return {}
    try:
        names = {}
        for tree in json.loads(path.read_text(encoding="utf-8")):
            names[tree["id"]] = tree["name"]
            for slot in tree.get("slots", []):
                for rune in slot.get("runes", []):
                    names[rune["id"]] = rune["name"]
        return names
    except Exception:
        return {}


def _format_runes(meta: dict) -> str:
    """
    メタデータからルーン情報を1行で返す。
    例: "Electrocute (Domination / Sorcery)  Cheap Shot / Eyeball Collection / Treasure Hunter / Absolute Focus / Scorch"
    """
    rune_names = _load_rune_names()
    if not rune_names:
        return ""

    keystone     = rune_names.get(meta.get("rune_keystone", 0), "")
    primary      = rune_names.get(meta.get("rune_primary_style", 0), "")
    secondary    = rune_names.get(meta.get("rune_secondary_style", 0), "")
    selections   = meta.get("rune_selections", [])

    # キーストーン以外の選択ルーン (primary: idx1-3, secondary: idx4-5)
    sub_runes = [rune_names.get(rid, "") for rid in selections[1:] if rid]
    sub_runes = [n for n in sub_runes if n]

    tree_str   = f"{primary} / {secondary}" if primary and secondary else primary or secondary
    rune_line  = f"{keystone} ({tree_str})" if keystone else tree_str
    if sub_runes:
        rune_line += "  " + " / ".join(sub_runes)
    return rune_line


def _find_video_path(game_id: str) -> Path | None:
    """
    game_id に対応する mp4 ファイルを探す。
    {game_id}.mp4 → レジストリの video_filename の順で試す。
    """
    old_mp4 = OUTPUT_DIR / f"{game_id}.mp4"
    if old_mp4.exists():
        return old_mp4
    try:
        from src.db.match_registry import get_row
        row = get_row(game_id)
        if row and row.get("video_filename"):
            titled_mp4 = OUTPUT_DIR / row["video_filename"]
            if titled_mp4.exists():
                return titled_mp4
    except Exception:
        pass
    return None


def main():
    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # game_id を引数から取得、なければ最新の JSON
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        game_id = args[0]
    else:
        jsons = sorted(OUTPUT_DIR.glob("*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not jsons:
            logger.error("output/videos/ にメタデータ JSON が見つかりません")
            sys.exit(1)
        game_id = jsons[0].stem
        logger.info(f"最新の録画を選択: {game_id}")

    # 重複アップロードチェック
    try:
        from src.db.match_registry import get_row
        row = get_row(game_id)
        if row and row.get("youtube_url"):
            logger.info(f"スキップ (アップロード済み): {game_id} → {row['youtube_url']}")
            return
    except Exception:
        pass

    json_path  = OUTPUT_DIR     / f"{game_id}.json"
    video_path = _find_video_path(game_id)
    thumb_path = THUMBNAILS_DIR / f"{game_id}.jpg"

    if not json_path.exists():
        logger.error(f"JSON なし: {json_path}")
        sys.exit(1)
    if video_path is None:
        logger.error(f"動画なし: output/videos/{game_id}.mp4 (またはリネーム済みファイル)")
        sys.exit(1)

    # サムネイルがなければ生成する
    if not thumb_path.exists():
        try:
            from create_thumbnails import generate as thumb_generate
            thumb_generate([game_id])
        except Exception as e:
            logger.warning(f"サムネイル生成失敗: {e}")

    meta = json.loads(json_path.read_text(encoding="utf-8"))
    champion = meta.get("champion", "")
    player   = meta.get("player", "")
    kda      = meta.get("kda", "")
    rank     = meta.get("rank", "Challenger")
    patch    = meta.get("game_version", "")
    kills    = meta.get("kills", 0)
    deaths   = meta.get("deaths", 0)
    assists  = meta.get("assists", 0)
    cs       = meta.get("cs", 0)
    vision   = meta.get("vision", 0)

    logger.info(f"アップロード対象: {game_id}")
    logger.info(f"  {champion} {kda} / {player} ({rank})")
    logger.info(f"  動画: {video_path} ({video_path.stat().st_size // 1024 // 1024}MB)")  # type: ignore[union-attr]
    logger.info(f"  サムネイル: {thumb_path.name if thumb_path.exists() else 'なし'}")

    # タイトル: JSONの完成済みタイトルをそのまま使用
    title = meta.get("title", f"[JP Challenger JG] {champion} {kda} - {player}")

    # 説明文: メタデータから生成
    from datetime import datetime, timezone
    start_ms = meta.get("game_start_ms", 0)
    date_str = ""
    if start_ms:
        dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone()
        date_str = dt.strftime("%Y-%m-%d %H:%M JST")

    rune_str = _format_runes(meta)
    description_lines = [
        "【JP Challenger ジャングル】",
        "",
        f"チャンピオン: {champion}",
        f"プレイヤー: {player}",
        f"ランク: {rank}",
        f"KDA: {kda} ({kills}/{deaths}/{assists})",
        f"ルーン: {rune_str}" if rune_str else "",
        f"パッチ: {patch}" if patch else "",
        f"試合日時: {date_str}" if date_str else "",
        "",
        "#LeagueOfLegends #LoL #チャレンジャー #ジャングル #日本サーバー",
    ]
    description = "\n".join(line for line in description_lines if line is not None)

    match_info = {
        "tier":         rank,
        "top_champion": champion,
        "game_id":      meta.get("game_id", game_id),
        "participants": [],
    }

    uploader = YouTubeUploader(config["youtube"])
    url = uploader.upload(
        video_path=video_path,
        thumbnail_path=thumb_path if thumb_path.exists() else None,
        match_info=match_info,
        highlights=[],
        title=title,
        description=description,
    )

    if url:
        logger.info(f"✓ アップロード完了: {url}")
        # 試合リストにYouTube URLを記録
        try:
            from src.db.match_registry import update_youtube_url, upsert
            upsert(meta, video_path=video_path)       # 未登録の場合に備えて
            update_youtube_url(game_id, url)
            logger.info("試合リスト更新完了")
        except Exception as e:
            logger.warning(f"試合リスト更新失敗: {e}")
    else:
        logger.error("✗ アップロード失敗")
        sys.exit(1)


def upload_game(game_id: str | int, config: dict) -> str | None:
    """
    指定した game_id の動画を YouTube にアップロードする。
    main.py などから呼び出し可能。

    Returns:
        YouTube URL (失敗時は None)
        既にアップロード済みの場合は既存 URL を返す。
    """
    from datetime import datetime, timezone as tz

    game_id    = str(game_id)
    json_path  = OUTPUT_DIR     / f"{game_id}.json"
    thumb_path = THUMBNAILS_DIR / f"{game_id}.jpg"

    # 重複アップロードチェック
    try:
        from src.db.match_registry import get_row
        row = get_row(game_id)
        if row and row.get("youtube_url"):
            logger.info(f"スキップ (アップロード済み): {game_id} → {row['youtube_url']}")
            return row["youtube_url"]
    except Exception:
        pass

    video_path = _find_video_path(game_id)

    if not json_path.exists() or video_path is None:
        logger.error(f"ファイル不足: {game_id} (json={json_path.exists()}, mp4={video_path})")
        return None

    meta     = json.loads(json_path.read_text(encoding="utf-8"))
    champion = meta.get("champion", "")
    player   = meta.get("player", "")
    kda      = meta.get("kda", "")
    rank     = meta.get("rank", "Challenger")
    patch    = meta.get("game_version", "")
    kills    = meta.get("kills", 0)
    deaths   = meta.get("deaths", 0)
    assists  = meta.get("assists", 0)
    cs       = meta.get("cs", 0)
    vision   = meta.get("vision", 0)

    title    = meta.get("title", f"[JP Challenger JG] {champion} {kda} - {player}")

    start_ms = meta.get("game_start_ms", 0)
    date_str = ""
    if start_ms:
        dt = datetime.fromtimestamp(start_ms / 1000, tz=tz.utc).astimezone()
        date_str = dt.strftime("%Y-%m-%d %H:%M JST")

    rune_str = _format_runes(meta)
    description_lines = [
        "【JP Challenger ジャングル】",
        "",
        f"チャンピオン: {champion}",
        f"プレイヤー: {player}",
        f"ランク: {rank}",
        f"KDA: {kda} ({kills}/{deaths}/{assists})",
        f"ルーン: {rune_str}" if rune_str else "",
        f"パッチ: {patch}" if patch else "",
        f"試合日時: {date_str}" if date_str else "",
        "",
        "#LeagueOfLegends #LoL #チャレンジャー #ジャングル #日本サーバー",
    ]
    description = "\n".join(line for line in description_lines if line is not None)

    match_info = {
        "tier":         rank,
        "top_champion": champion,
        "game_id":      meta.get("game_id", game_id),
        "participants": [],
    }

    uploader = YouTubeUploader(config["youtube"])
    url = uploader.upload(
        video_path=video_path,
        thumbnail_path=thumb_path if thumb_path.exists() else None,
        match_info=match_info,
        highlights=[],
        title=title,
        description=description,
    )

    if url:
        try:
            from src.db.match_registry import update_youtube_url, upsert
            upsert(meta, video_path=video_path)
            update_youtube_url(game_id, url)
        except Exception as e:
            logger.warning(f"試合リスト更新失敗: {e}")

    return url


if __name__ == "__main__":
    main()
