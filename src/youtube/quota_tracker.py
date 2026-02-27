"""
YouTube Data API v3 クォータ使用量を追跡するモジュール

デフォルト上限: 10,000 units/日
リセット時刻:  毎朝 9:00 JST (= UTC 00:00)

ユニットコスト:
  videos.insert    : 1,600 units
  thumbnails.set   : 50 units
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from loguru import logger

_JST = timezone(timedelta(hours=9))
_CACHE_PATH = Path("cache/youtube_quota.json")

DAILY_LIMIT    = 10_000
UPLOAD_COST    = 1_600   # videos.insert
THUMBNAIL_COST = 50      # thumbnails.set
RESET_HOUR_JST = 9       # 毎朝9:00 JSTにリセット


def _quota_day() -> str:
    """
    クォータの「今日」キーを返す。
    9:00 JST より前は前日扱い (リセットが9:00のため)。
    例: 2026-02-27 08:59 JST → "2026-02-26"
        2026-02-27 09:00 JST → "2026-02-27"
    """
    now_jst = datetime.now(_JST)
    if now_jst.hour < RESET_HOUR_JST:
        now_jst = now_jst - timedelta(days=1)
    return now_jst.strftime("%Y-%m-%d")


def _load() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    _CACHE_PATH.parent.mkdir(exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_used() -> int:
    """今日使用したユニット数を返す"""
    return _load().get(_quota_day(), 0)


def get_remaining() -> int:
    """今日の残りユニット数を返す"""
    return max(0, DAILY_LIMIT - get_used())


def can_upload() -> bool:
    """アップロード1件分のユニットが残っているか"""
    return get_remaining() >= UPLOAD_COST


def record_upload(with_thumbnail: bool = True) -> None:
    """アップロード1件のユニット消費を記録する"""
    data = _load()
    key = _quota_day()
    cost = UPLOAD_COST + (THUMBNAIL_COST if with_thumbnail else 0)
    data[key] = data.get(key, 0) + cost
    _save(data)
    logger.debug(f"[クォータ] +{cost} units → 本日合計: {data[key]} / {DAILY_LIMIT}")


def log_status() -> None:
    """現在のクォータ状況をINFOログに出力する"""
    used      = get_used()
    remaining = get_remaining()
    day       = _quota_day()
    logger.info(f"[クォータ] {day}: {used}/{DAILY_LIMIT} units 使用 (残り {remaining} units)")
    if remaining < UPLOAD_COST:
        logger.warning(
            f"[クォータ] 残りユニット不足 ({remaining} < {UPLOAD_COST})。"
            f"翌朝 {RESET_HOUR_JST}:00 JST 以降に再実行してください。"
        )
