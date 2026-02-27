"""
試合リストを CSV で管理するモジュール

output/match_list.csv に全試合の情報を記録する。
record_matches.py と upload_video.py から呼び出される。
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REGISTRY_PATH = Path("output/match_list.csv")

COLUMNS = [
    "game_id",
    "match_id",
    "recorded_at",
    "champion",
    "player",
    "rank",
    "kills",
    "deaths",
    "assists",
    "cs",
    "vision",
    "gold",
    "patch",
    "game_date",
    "video_mb",
    "video_filename",
    "youtube_url",
]


def _load() -> dict[str, dict]:
    """CSVを読み込み {game_id: row_dict} を返す"""
    rows = {}
    if not REGISTRY_PATH.exists():
        return rows
    with open(REGISTRY_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows[row["game_id"]] = row
    return rows


def _save(rows: dict[str, dict]):
    """rowsをCSVに書き出す (ゲーム実施時間の昇順 = game_id昇順)"""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(rows.values(), key=lambda r: int(r["game_id"]))
    with open(REGISTRY_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in sorted_rows:
            writer.writerow({col: row.get(col, "") for col in COLUMNS})


def _kda_part(kda: str, idx: int) -> str:
    """'10/4/9' のような kda 文字列から idx 番目 (0=K,1=D,2=A) を返す"""
    parts = kda.split("/")
    return parts[idx].strip() if len(parts) == 3 else ""


def upsert(meta: dict, video_path: Optional[Path] = None):
    """
    試合をリストに登録/更新する。

    Args:
        meta: output/videos/{game_id}.json の内容
        video_path: mp4ファイルパス (サイズ取得用)
    """
    rows = _load()
    game_id = str(meta.get("game_id", ""))
    if not game_id:
        return

    # 試合日時
    start_ms = meta.get("game_start_ms", 0)
    if start_ms:
        dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone()
        game_date = dt.strftime("%Y-%m-%d %H:%M JST")
    else:
        game_date = ""

    # 動画サイズ
    video_mb = ""
    if video_path and Path(video_path).exists():
        video_mb = str(Path(video_path).stat().st_size // 1024 // 1024)

    existing = rows.get(game_id, {})
    rows[game_id] = {
        "game_id":     game_id,
        "match_id":    meta.get("match_id", ""),
        "recorded_at": existing.get("recorded_at") or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "champion":    meta.get("champion", ""),
        "player":      meta.get("player", ""),
        "rank":        meta.get("rank", ""),
        "kills":       str(meta.get("kills", "") or _kda_part(meta.get("kda", ""), 0)),
        "deaths":      str(meta.get("deaths", "") or _kda_part(meta.get("kda", ""), 1)),
        "assists":     str(meta.get("assists", "") or _kda_part(meta.get("kda", ""), 2)),
        "cs":          str(meta.get("cs", "")),
        "vision":      str(meta.get("vision", "")),
        "gold":        str(meta.get("gold", "")),
        "patch":       meta.get("game_version", ""),
        "game_date":   game_date,
        "video_mb":       video_mb or existing.get("video_mb", ""),
        "video_filename": meta.get("video_filename", "") or existing.get("video_filename", ""),
        "youtube_url":    existing.get("youtube_url", ""),
    }
    _save(rows)


def bulk_upsert_candidates(candidates: list[dict]):
    """
    find_downloadable_matches() の候補リストを一括でCSVに暫定登録する。
    録画済み（video_mbあり）の行は上書きしない。
    """
    rows = _load()
    changed = False

    for c in candidates:
        game_id = str(c.get("game_id", ""))
        if not game_id:
            continue

        existing = rows.get(game_id, {})
        if existing.get("video_mb"):   # 録画済みは触らない
            continue

        start_ms = c.get("game_start_ms", 0)
        if start_ms:
            dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone()
            game_date = dt.strftime("%Y-%m-%d %H:%M JST")
        else:
            game_date = ""

        gv    = c.get("game_version", "")
        patch = ".".join(gv.split(".")[:2]) if gv else ""

        rows[game_id] = {
            "game_id":        game_id,
            "match_id":       c.get("match_id", ""),
            "recorded_at":    existing.get("recorded_at", ""),
            "champion":       c.get("champion", ""),
            "player":         c.get("player", ""),
            "rank":           existing.get("rank", "Challenger"),
            "kills":          str(c.get("kills", "")),
            "deaths":         str(c.get("deaths", "")),
            "assists":        str(c.get("assists", "")),
            "cs":             str(c.get("cs", "")),
            "vision":         str(c.get("vision", "")),
            "gold":           str(c.get("gold", "")),
            "patch":          patch,
            "game_date":      game_date,
            "video_mb":       "",
            "video_filename": "",
            "youtube_url":    existing.get("youtube_url", ""),
        }
        changed = True

    if changed:
        _save(rows)


def update_youtube_url(game_id: str | int, url: str):
    """YouTube URLを登録/更新する"""
    rows = _load()
    gid = str(game_id)
    if gid in rows:
        rows[gid]["youtube_url"] = url
        _save(rows)


def update_video_filename(game_id: str | int, filename: str):
    """動画ファイル名を登録/更新する"""
    rows = _load()
    gid = str(game_id)
    if gid in rows:
        rows[gid]["video_filename"] = filename
        _save(rows)


def get_row(game_id: str | int) -> Optional[dict]:
    """指定 game_id の行を返す (存在しない場合 None)"""
    rows = _load()
    return rows.get(str(game_id))


def get_all() -> list[dict]:
    """全行をリストで返す (ゲーム実施時間の昇順 = game_id昇順)"""
    rows = _load()
    return sorted(rows.values(), key=lambda r: int(r["game_id"]))
