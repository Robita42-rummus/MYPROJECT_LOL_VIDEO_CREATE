"""
試合リストを SQLite で管理するモジュール

output/lol_matches.db  ← メインストレージ (SQLite)
output/match_list.csv  ← 自動エクスポート (Excel で閲覧用)

外部API (呼び出し元のコードは変更不要):
  upsert(), bulk_upsert_candidates(), update_youtube_url(),
  update_video_filename(), get_row(), get_all()
"""
from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH       = Path("output/lol_matches.db")
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
    "status",
]

# CSV エクスポート用カラム (status は除く — 後方互換)
CSV_COLUMNS = [c for c in COLUMNS if c != "status"]

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS matches (
    game_id        TEXT PRIMARY KEY,
    match_id       TEXT    DEFAULT '',
    recorded_at    TEXT    DEFAULT '',
    champion       TEXT    DEFAULT '',
    player         TEXT    DEFAULT '',
    rank           TEXT    DEFAULT '',
    kills          TEXT    DEFAULT '',
    deaths         TEXT    DEFAULT '',
    assists        TEXT    DEFAULT '',
    cs             TEXT    DEFAULT '',
    vision         TEXT    DEFAULT '',
    gold           TEXT    DEFAULT '',
    patch          TEXT    DEFAULT '',
    game_date      TEXT    DEFAULT '',
    video_mb       TEXT    DEFAULT '',
    video_filename TEXT    DEFAULT '',
    youtube_url    TEXT    DEFAULT '',
    status         TEXT    DEFAULT 'discovered'
)
"""


# -----------------------------------------------
# 接続・初期化
# -----------------------------------------------

@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute(_CREATE_SQL)
        con.commit()
        yield con
    finally:
        con.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: (row[k] or "") for k in row.keys()}


# -----------------------------------------------
# CSV エクスポート (書き込みのたびに再生成)
# -----------------------------------------------

def _export_csv(rows: list[dict]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in CSV_COLUMNS})


# -----------------------------------------------
# CSV → SQLite 移行 (初回のみ)
# -----------------------------------------------

def _migrate_from_csv() -> None:
    """既存の match_list.csv をSQLiteに移行する (DB未存在時のみ実行)"""
    if not REGISTRY_PATH.exists():
        return
    rows = []
    with open(REGISTRY_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    if not rows:
        return
    with _conn() as con:
        for row in rows:
            # youtube_url から status を推定
            yt = row.get("youtube_url", "")
            if yt and yt != "skipped":
                status = "uploaded"
            elif yt == "skipped":
                status = "skipped"
            elif row.get("video_mb"):
                status = "recorded"
            else:
                status = "discovered"
            con.execute(
                """INSERT OR IGNORE INTO matches
                   (game_id, match_id, recorded_at, champion, player, rank,
                    kills, deaths, assists, cs, vision, gold,
                    patch, game_date, video_mb, video_filename, youtube_url, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row.get("game_id", ""),
                    row.get("match_id", ""),
                    row.get("recorded_at", ""),
                    row.get("champion", ""),
                    row.get("player", ""),
                    row.get("rank", ""),
                    row.get("kills", ""),
                    row.get("deaths", ""),
                    row.get("assists", ""),
                    row.get("cs", ""),
                    row.get("vision", ""),
                    row.get("gold", ""),
                    row.get("patch", ""),
                    row.get("game_date", ""),
                    row.get("video_mb", ""),
                    row.get("video_filename", ""),
                    row.get("youtube_url", ""),
                    status,
                ),
            )
        con.commit()
    from loguru import logger
    logger.info(f"[移行] match_list.csv → lol_matches.db ({len(rows)} 件)")


def _ensure_db() -> None:
    """DB未存在時はCSVから移行する"""
    if not DB_PATH.exists():
        _migrate_from_csv()


# -----------------------------------------------
# ユーティリティ
# -----------------------------------------------

def _kda_part(kda: str, idx: int) -> str:
    parts = kda.split("/")
    return parts[idx].strip() if len(parts) == 3 else ""


# -----------------------------------------------
# 公開 API (呼び出し元と互換)
# -----------------------------------------------

def upsert(meta: dict, video_path: Optional[Path] = None) -> None:
    """試合をDBに登録/更新する"""
    _ensure_db()
    game_id = str(meta.get("game_id", ""))
    if not game_id:
        return

    start_ms = meta.get("game_start_ms", 0)
    if start_ms:
        dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone()
        game_date = dt.strftime("%Y-%m-%d %H:%M JST")
    else:
        game_date = ""

    video_mb = ""
    if video_path and Path(video_path).exists():
        video_mb = str(Path(video_path).stat().st_size // 1024 // 1024)

    with _conn() as con:
        existing = con.execute(
            "SELECT * FROM matches WHERE game_id=?", (game_id,)
        ).fetchone()
        ex = _row_to_dict(existing) if existing else {}

        status = "recorded" if (video_mb or ex.get("video_mb")) else ex.get("status", "discovered")

        con.execute(
            """INSERT INTO matches
               (game_id, match_id, recorded_at, champion, player, rank,
                kills, deaths, assists, cs, vision, gold,
                patch, game_date, video_mb, video_filename, youtube_url, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id) DO UPDATE SET
                 match_id       = excluded.match_id,
                 recorded_at    = CASE WHEN recorded_at='' THEN excluded.recorded_at ELSE recorded_at END,
                 champion       = excluded.champion,
                 player         = excluded.player,
                 rank           = excluded.rank,
                 kills          = excluded.kills,
                 deaths         = excluded.deaths,
                 assists        = excluded.assists,
                 cs             = excluded.cs,
                 vision         = excluded.vision,
                 gold           = excluded.gold,
                 patch          = excluded.patch,
                 game_date      = excluded.game_date,
                 video_mb       = CASE WHEN excluded.video_mb!='' THEN excluded.video_mb ELSE video_mb END,
                 video_filename = CASE WHEN excluded.video_filename!='' THEN excluded.video_filename ELSE video_filename END,
                 status         = excluded.status
            """,
            (
                game_id,
                meta.get("match_id", ""),
                ex.get("recorded_at") or datetime.now().strftime("%Y-%m-%d %H:%M"),
                meta.get("champion", ""),
                meta.get("player", ""),
                meta.get("rank", ""),
                str(meta.get("kills", "") or _kda_part(meta.get("kda", ""), 0)),
                str(meta.get("deaths", "") or _kda_part(meta.get("kda", ""), 1)),
                str(meta.get("assists", "") or _kda_part(meta.get("kda", ""), 2)),
                str(meta.get("cs", "")),
                str(meta.get("vision", "")),
                str(meta.get("gold", "")),
                meta.get("game_version", ""),
                game_date,
                video_mb or ex.get("video_mb", ""),
                meta.get("video_filename", "") or ex.get("video_filename", ""),
                ex.get("youtube_url", ""),
                status,
            ),
        )
        con.commit()
        rows = [_row_to_dict(r) for r in con.execute(
            "SELECT * FROM matches ORDER BY CAST(game_id AS INTEGER)"
        ).fetchall()]
    _export_csv(rows)


def bulk_upsert_candidates(candidates: list[dict]) -> None:
    """find_downloadable_matches() の候補リストを一括でDBに暫定登録する"""
    _ensure_db()
    with _conn() as con:
        changed = False
        for c in candidates:
            game_id = str(c.get("game_id", ""))
            if not game_id:
                continue
            existing = con.execute(
                "SELECT video_mb, youtube_url FROM matches WHERE game_id=?", (game_id,)
            ).fetchone()
            if existing and existing["video_mb"]:
                continue  # 録画済みは触らない

            start_ms = c.get("game_start_ms", 0)
            if start_ms:
                dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone()
                game_date = dt.strftime("%Y-%m-%d %H:%M JST")
            else:
                game_date = ""

            gv    = c.get("game_version", "")
            patch = ".".join(gv.split(".")[:2]) if gv else ""

            yt = (existing["youtube_url"] if existing else "") or ""
            status = "skipped" if yt == "skipped" else (
                "uploaded" if yt else "discovered"
            )

            con.execute(
                """INSERT INTO matches
                   (game_id, match_id, recorded_at, champion, player, rank,
                    kills, deaths, assists, cs, vision, gold,
                    patch, game_date, video_mb, video_filename, youtube_url, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(game_id) DO UPDATE SET
                     match_id  = excluded.match_id,
                     champion  = excluded.champion,
                     player    = excluded.player,
                     kills     = excluded.kills,
                     deaths    = excluded.deaths,
                     assists   = excluded.assists,
                     cs        = excluded.cs,
                     vision    = excluded.vision,
                     gold      = excluded.gold,
                     patch     = excluded.patch,
                     game_date = excluded.game_date,
                     status    = CASE WHEN video_mb!='' THEN status ELSE excluded.status END
                """,
                (
                    game_id,
                    c.get("match_id", ""),
                    "",
                    c.get("champion", ""),
                    c.get("player", ""),
                    "Challenger",
                    str(c.get("kills", "")),
                    str(c.get("deaths", "")),
                    str(c.get("assists", "")),
                    str(c.get("cs", "")),
                    str(c.get("vision", "")),
                    str(c.get("gold", "")),
                    patch,
                    game_date,
                    "",
                    "",
                    yt,
                    status,
                ),
            )
            changed = True

        if changed:
            con.commit()
            rows = [_row_to_dict(r) for r in con.execute(
                "SELECT * FROM matches ORDER BY CAST(game_id AS INTEGER)"
            ).fetchall()]
    if changed:
        _export_csv(rows)


def update_youtube_url(game_id: str | int, url: str) -> None:
    """YouTube URL を登録/更新する"""
    _ensure_db()
    gid = str(game_id)
    status = "skipped" if url == "skipped" else ("uploaded" if url else "recorded")
    with _conn() as con:
        con.execute(
            "UPDATE matches SET youtube_url=?, status=? WHERE game_id=?",
            (url, status, gid),
        )
        con.commit()
        rows = [_row_to_dict(r) for r in con.execute(
            "SELECT * FROM matches ORDER BY CAST(game_id AS INTEGER)"
        ).fetchall()]
    _export_csv(rows)


def update_video_filename(game_id: str | int, filename: str) -> None:
    """動画ファイル名を登録/更新する"""
    _ensure_db()
    gid = str(game_id)
    with _conn() as con:
        con.execute(
            "UPDATE matches SET video_filename=? WHERE game_id=?",
            (filename, gid),
        )
        con.commit()
        rows = [_row_to_dict(r) for r in con.execute(
            "SELECT * FROM matches ORDER BY CAST(game_id AS INTEGER)"
        ).fetchall()]
    _export_csv(rows)


def get_row(game_id: str | int) -> Optional[dict]:
    """指定 game_id の行を返す (存在しない場合 None)"""
    _ensure_db()
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM matches WHERE game_id=?", (str(game_id),)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_all() -> list[dict]:
    """全行をリストで返す (game_id 昇順)"""
    _ensure_db()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM matches ORDER BY CAST(game_id AS INTEGER)"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
