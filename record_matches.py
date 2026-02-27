"""
JP チャレンジャー試合 フルゲーム自動録画スクリプト

フロー:
1. Riot API で最近の JP チャレンジャー試合を収集
2. LCU でリプレイをダウンロード
3. 各試合について:
   - 勝利チームのジャングラーを特定
   - リプレイ起動 → t=0 から OBS でフルゲーム録画 (60fps / NVENC)
   - 起動時に AHK でカメラをジャングラーに固定 (1回のみ)
   - MKV → MP4 リマックス → output/videos/{game_id}.mp4 に保存

使い方: python record_matches.py

前提条件:
  OBS Studio を起動し、ツール → WebSocket サーバー設定 → 「WebSocket サーバーを有効にする」をONにすること
"""
from __future__ import annotations
import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import requests
import simpleobsws
import urllib3
import yaml
from loguru import logger

urllib3.disable_warnings()
logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add("logs/record_matches.log", level="DEBUG", rotation="50MB")

from src.riot.api_client import RiotAPIClient
from src.lol.lcu_client import LCUClient
from src.lol.replay_downloader import ReplayDownloader

# -----------------------------------------------
# 設定
# -----------------------------------------------
REPLAY_BASE  = "https://127.0.0.1:2999"
MAX_MATCHES  = 50       # 1回の実行で録画する最大試合数 (スキップは含まない)
OUTPUT_DIR   = Path("output/videos")

AHK_BLUE  = Path("press_key2.ahk")   # ブルーチームジャングラー (キー2)
AHK_RED   = Path("press_keyw.ahk")   # レッドチームジャングラー (キーW)
AHK_KEY_O = Path("press_keyo.ahk")   # スコアボードトグル (O キー)

# Summoner's Rift マップ座標 (カメラ x,z)
# ベース判定閾値 (APIから実測調整可)
BASE_THRESHOLD_BLUE = 3500   # x < this AND z < this → ブルーベース
BASE_THRESHOLD_RED  = 11500  # x > this AND z > this → レッドベース

SCOREBOARD_DURATION = 12.0   # スコアボード表示秒数
SCOREBOARD_COOLDOWN = 60.0   # 再表示までの最短インターバル (秒)

# OBS WebSocket 設定
# OBS → ツール → WebSocket サーバー設定 → 有効化 が必要
OBS_HOST     = "localhost"
OBS_PORT     = 4455
OBS_PASSWORD = "BzcENdiYpJEJXOkb"


# -----------------------------------------------
# AHK・カメラ操作
# -----------------------------------------------

def _run_ahk(script: Path):
    try:
        subprocess.run(["AutoHotkey.exe", str(script)],
                       timeout=10, capture_output=True)
        return
    except FileNotFoundError:
        pass
    for path in [
        r"C:\Program Files\AutoHotkey\AutoHotkey.exe",
        r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
        r"C:\Program Files (x86)\AutoHotkey\AutoHotkey.exe",
    ]:
        if Path(path).exists():
            subprocess.run([path, str(script)], timeout=10, capture_output=True)
            return
    logger.warning("AutoHotkey.exe が見つからない")


def press_jungler_key(team_id: int):
    """勝利チームのジャングラーキーを AHK で押す"""
    _run_ahk(AHK_BLUE if team_id == 100 else AHK_RED)




# -----------------------------------------------
# マッチ情報
# -----------------------------------------------

def get_winning_jungler(match_data: dict) -> tuple[int, str]:
    """勝利チームのジャングラー (teamId, championName) を返す"""
    for p in match_data["info"]["participants"]:
        if p.get("teamPosition") == "JUNGLE" and p.get("win"):
            team = "BLUE" if p["teamId"] == 100 else "RED"
            name = p["championName"]
            logger.info(f"  勝利ジャングラー: {name} ({team}チーム)")
            return p["teamId"], name
    logger.warning("  ジャングラー検出失敗 → ブルーチームとして扱う")
    return 100, ""


def get_player_stats(match_data: dict) -> dict:
    """勝利ジャングラーのプレイヤー名・KDA・アイテム・ルーン等を返す"""
    for p in match_data["info"]["participants"]:
        if p.get("teamPosition") == "JUNGLE" and p.get("win"):
            game_name = p.get("riotIdGameName", "")
            tag       = p.get("riotIdTagline", "")
            player    = f"{game_name}#{tag}" if tag else game_name

            # アイテム (item0-item6, 0=空きスロット)
            items = [p.get(f"item{i}", 0) for i in range(7)]

            # サモナースペル
            spells = [p.get("summoner1Id", 0), p.get("summoner2Id", 0)]

            # ルーン
            perks = p.get("perks", {})
            styles = perks.get("styles", [])
            rune_keystone        = 0
            rune_primary_style   = 0
            rune_secondary_style = 0
            rune_selections      = []
            if len(styles) >= 1:
                rune_primary_style = styles[0].get("style", 0)
                sels = styles[0].get("selections", [])
                if sels:
                    rune_keystone = sels[0].get("perk", 0)
                rune_selections = [s.get("perk", 0) for s in sels]
            if len(styles) >= 2:
                rune_secondary_style = styles[1].get("style", 0)
                rune_selections += [s.get("perk", 0) for s in styles[1].get("selections", [])]

            info = match_data["info"]
            game_version = ".".join(info.get("gameVersion", "").split(".")[:2])
            game_start_ms = info.get("gameStartTimestamp", 0)

            return {
                "player":               player,
                "puuid":                p.get("puuid", ""),
                "champion":             p.get("championName", ""),
                "kills":                p.get("kills", 0),
                "deaths":               p.get("deaths", 0),
                "assists":              p.get("assists", 0),
                "cs":                   p.get("totalMinionsKilled", 0),
                "vision":               p.get("visionScore", 0),
                "gold":                 p.get("goldEarned", 0),
                "items":                items,
                "summoner_spells":      spells,
                "rune_keystone":        rune_keystone,
                "rune_primary_style":   rune_primary_style,
                "rune_secondary_style": rune_secondary_style,
                "rune_selections":      rune_selections,
                "game_version":         game_version,
                "game_start_ms":        game_start_ms,
            }
    return {}


def get_player_rank(riot_client, puuid: str) -> str:
    """PUUIDからランク文字列を返す (例: 'Challenger 1500 LP')"""
    try:
        summoner = riot_client.get_summoner_by_puuid(puuid)
        if not summoner:
            return "Challenger"
        entries = riot_client._get(
            riot_client.PLATFORM_URL,
            f"/lol/league/v4/entries/by-summoner/{summoner['id']}"
        )
        if not entries:
            return "Challenger"
        for entry in entries:
            if entry.get("queueType") == "RANKED_SOLO_5x5":
                tier = entry.get("tier", "CHALLENGER").title()
                rank = entry.get("rank", "")
                lp   = entry.get("leaguePoints", 0)
                return f"{tier} {rank} {lp} LP".strip() if rank else f"{tier} {lp} LP"
        return "Challenger"
    except Exception:
        return "Challenger"


def make_title(stats: dict) -> str:
    """動画タイトル文字列を生成する"""
    kda = f"{stats['kills']}/{stats['deaths']}/{stats['assists']}"
    return f"[JP Challenger JG] {stats['champion']} {kda} - {stats['player']}"


def safe_filename(title: str) -> str:
    """YouTube タイトルを Windows ファイル名として使える文字列に変換する"""
    # / はKDA区切りに使われる → ハイフンに
    name = title.replace("/", "-")
    # Windows 禁止文字を除去
    name = re.sub(r'[\\:*?"<>|]', "", name)
    name = name.strip()
    # 長さ制限 (255字 - ".mp4" の余裕)
    if len(name) > 200:
        name = name[:200]
    return name


def save_metadata(game_id: int, match_id: str, stats: dict, riot_client=None):
    """output/videos/{game_id}.json にメタデータを保存する"""
    title = make_title(stats)

    # ランク取得 (API呼び出し2回)
    rank = "Challenger"
    if riot_client and stats.get("puuid"):
        rank = get_player_rank(riot_client, stats["puuid"])

    meta = {
        "game_id":              game_id,
        "match_id":             match_id,
        "title":                title,
        "champion":             stats["champion"],
        "player":               stats["player"],
        "rank":                 rank,
        "kda":                  f"{stats['kills']}/{stats['deaths']}/{stats['assists']}",
        "kills":                stats["kills"],
        "deaths":               stats["deaths"],
        "assists":              stats["assists"],
        "cs":                   stats["cs"],
        "vision":               stats["vision"],
        "gold":                 stats["gold"],
        "items":                stats.get("items", []),
        "summoner_spells":      stats.get("summoner_spells", []),
        "rune_keystone":        stats.get("rune_keystone", 0),
        "rune_primary_style":   stats.get("rune_primary_style", 0),
        "rune_secondary_style": stats.get("rune_secondary_style", 0),
        "rune_selections":      stats.get("rune_selections", []),
        "game_version":         stats.get("game_version", ""),
        "game_start_ms":        stats.get("game_start_ms", 0),
    }
    meta_path = OUTPUT_DIR / f"{game_id}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    logger.info(f"  タイトル: {title}")
    logger.info(f"  ランク: {rank}")
    logger.info(f"  メタデータ: {meta_path.name}")

    # 試合リストに登録
    try:
        from src.db.match_registry import upsert as registry_upsert
        registry_upsert(meta, video_path=OUTPUT_DIR / f"{game_id}.mp4")
    except Exception as e:
        logger.warning(f"試合リスト登録失敗: {e}")


def get_game_duration(match_data: dict) -> float:
    """試合時間 (秒) を返す"""
    duration = match_data["info"].get("gameDuration", 0)
    mins = int(duration // 60)
    secs = int(duration % 60)
    logger.info(f"  試合時間: {mins}分{secs}秒 ({duration}秒)")
    return float(duration)


# -----------------------------------------------
# Replay API
# -----------------------------------------------

def get_playback_time() -> float | None:
    try:
        r = requests.get(f"{REPLAY_BASE}/replay/playback", timeout=3, verify=False)
        if r.status_code == 200:
            return r.json().get("time")
    except Exception:
        pass
    return None


def get_camera_xz() -> tuple[float | None, float | None]:
    """Replay API からカメラの x,z 座標を返す"""
    try:
        r = requests.get(f"{REPLAY_BASE}/replay/render", timeout=3, verify=False)
        if r.status_code == 200:
            pos = r.json().get("cameraPosition", {})
            return pos.get("x"), pos.get("z")
    except Exception:
        pass
    return None, None


def is_in_base(cx: float | None, cz: float | None, team_id: int) -> bool:
    """カメラ座標がベースエリアにあるか判定する"""
    if cx is None or cz is None:
        return False
    if team_id == 100:
        return cx < BASE_THRESHOLD_BLUE and cz < BASE_THRESHOLD_BLUE
    else:
        return cx > BASE_THRESHOLD_RED and cz > BASE_THRESHOLD_RED


def wait_for_replay(timeout: int = 300) -> bool:
    logger.info("  リプレイ API 起動待機中...")
    for _ in range(timeout):
        try:
            r = requests.get(f"{REPLAY_BASE}/liveclientdata/gamestats",
                             timeout=3, verify=False)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def seek_to_start(retries: int = 10, interval: float = 3.0):
    """t=0 にシークして再生開始 (接続失敗時はリトライ)"""
    for i in range(retries):
        try:
            requests.post(f"{REPLAY_BASE}/replay/playback",
                          json={"paused": True, "time": 0.0},
                          timeout=5, verify=False)
            time.sleep(2.0)
            requests.post(f"{REPLAY_BASE}/replay/playback",
                          json={"paused": False, "speed": 1.0},
                          timeout=5, verify=False)
            time.sleep(0.5)
            return
        except Exception:
            if i < retries - 1:
                logger.info(f"  シーク待機中... ({i+1}/{retries})")
                time.sleep(interval)
    logger.warning("  seek_to_start: 全リトライ失敗")


# -----------------------------------------------
# OBS WebSocket
# -----------------------------------------------

def _obs_call(req_type: str, data: dict = None):
    """OBS WebSocket リクエストを同期実行して Response を返す"""
    async def _run():
        ws = simpleobsws.WebSocketClient(
            url=f"ws://{OBS_HOST}:{OBS_PORT}",
            password=OBS_PASSWORD,
        )
        await ws.connect()
        await ws.wait_until_identified()
        try:
            resp = await ws.call(simpleobsws.Request(req_type, data or {}))
            return resp
        finally:
            await ws.disconnect()
    return asyncio.run(_run())


def obs_start_record():
    """OBS録画を開始する"""
    resp = _obs_call("StartRecord")
    if not resp.ok():
        raise RuntimeError(f"OBS StartRecord 失敗: {resp.requestStatus}")
    logger.info("  OBS録画開始")


def obs_stop_record() -> Path | None:
    """OBS録画を停止してファイルパスを返す"""
    resp = _obs_call("StopRecord")
    if resp.ok() and resp.responseData:
        path = resp.responseData.get("outputPath")
        if path:
            return Path(path)
    logger.warning(f"  OBS StopRecord 失敗または outputPath なし: {resp.requestStatus}")
    return None


def obs_set_record_directory(directory: Path):
    """OBS録画保存先ディレクトリを設定する"""
    resp = _obs_call("SetRecordDirectory", {"recordDirectory": str(directory.resolve())})
    if not resp.ok():
        logger.warning(f"  OBS SetRecordDirectory 失敗: {resp.requestStatus}")


def wait_for_game_end(game_duration: float, team_id: int):
    """再生時刻をポーリングしてゲーム終了を検出し、スコアボード表示も制御する"""
    t = get_playback_time() or 0.0
    remaining = game_duration - t
    wait_limit = int(remaining) + 120
    logger.info(f"  ゲーム終了待機 (残り最大 {wait_limit}秒)")

    # スコアボード制御状態
    was_outside_base   = False   # 一度ベース外に出たか (序盤の初期位置を除外)
    scoreboard_open    = False   # 現在スコアボードが開いているか
    scoreboard_open_at = 0.0     # スコアボードを開いた時刻
    last_show_time     = -SCOREBOARD_COOLDOWN  # 最後に表示した時刻

    for i in range(wait_limit):
        time.sleep(1)
        now = time.time()

        # ゲーム終了チェック (10秒ごと)
        if i % 10 == 9:
            cur = get_playback_time()
            if cur is not None and cur >= game_duration:
                logger.info(f"  ゲーム終了検出 (t={cur:.0f}s >= {game_duration:.0f}s)")
                break
            if i % 60 == 59:
                prog = f"t={cur:.0f}s" if cur else "不明"
                logger.info(f"  録画中... {i+1}秒経過 ({prog} / {game_duration:.0f}s)")

        # スコアボードを閉じる (表示から SCOREBOARD_DURATION 秒後)
        if scoreboard_open and (now - scoreboard_open_at) >= SCOREBOARD_DURATION:
            _run_ahk(AHK_KEY_O)
            scoreboard_open = False
            logger.info("  スコアボード非表示")

        # カメラ位置チェック (2秒ごと、スコアボード未表示かつクールダウン完了時)
        if i % 2 == 0 and not scoreboard_open and (now - last_show_time) > SCOREBOARD_COOLDOWN:
            cx, cz = get_camera_xz()
            in_base = is_in_base(cx, cz, team_id)
            if not in_base:
                was_outside_base = True
            elif was_outside_base:
                # ベース外→ベース内への遷移 = リコール検出
                _run_ahk(AHK_KEY_O)
                scoreboard_open    = True
                scoreboard_open_at = now
                last_show_time     = now
                logger.info(f"  スコアボード表示 (リコール検出 x={cx:.0f}, z={cz:.0f})")

    # 終了時にスコアボードが開いたままなら閉じる
    if scoreboard_open:
        _run_ahk(AHK_KEY_O)
        logger.info("  スコアボード非表示 (終了処理)")

    logger.info("  待機タイムアウト → 録画停止")


def mkv_to_mp4(mkv: Path, mp4: Path) -> bool:
    """MKV → MP4 リマックス (再エンコードなし、高速)"""
    logger.info(f"  mp4 変換中: {mkv.name} → {mp4.name}")
    cmd = ["ffmpeg", "-y", "-i", str(mkv), "-c", "copy", str(mp4)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode == 0 and mp4.exists() and mp4.stat().st_size > 0:
        size_mb = mp4.stat().st_size // (1024 * 1024)
        logger.info(f"  mp4 完成: {mp4.name} ({size_mb}MB)")
        mkv.unlink(missing_ok=True)
        return True
    logger.error(f"  mp4 変換失敗: {r.stderr[-300:]}")
    return False


def kill_lol():
    subprocess.run(["taskkill", "/F", "/IM", "League of Legends.exe"],
                   capture_output=True, timeout=10)
    time.sleep(3)


# -----------------------------------------------
# 1試合分の処理
# -----------------------------------------------

def process_match(
    game_id: int,
    match_id: str,
    team_id: int,
    game_duration: float,
    lcu: LCUClient,
) -> Path | None:
    """
    1試合のリプレイを起動してフル録画し、mp4 パスを返す。
    失敗時は None を返す。
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    mp4_path = OUTPUT_DIR / f"{game_id}.mp4"

    # 既存のLoLゲームを終了
    kill_lol()

    # リプレイ起動 (ローディング画面は録画しない)
    logger.info(f"リプレイ起動: {match_id}")
    if not lcu.launch_replay(game_id):
        logger.error("  リプレイ起動失敗")
        kill_lol()
        return None
    if not wait_for_replay(timeout=300):
        logger.error("  リプレイ API タイムアウト")
        kill_lol()
        return None
    time.sleep(5)

    # t=0 にシーク
    seek_to_start()

    # ゲームが再生状態になるまで待機してからOBS録画開始
    logger.info("  ゲーム再生待機中...")
    for _ in range(30):
        t = get_playback_time()
        if t is not None and t > 1.0:
            break
        time.sleep(1)
    time.sleep(3)  # 再生開始後も少し待つ

    # OBS録画開始 (ゲーム開始後)
    obs_set_record_directory(OUTPUT_DIR)
    try:
        obs_start_record()
    except RuntimeError as e:
        logger.error(f"  {e}")
        kill_lol()
        return None

    # カメラ設定 (AHK) - 起動時に1回だけ押す
    logger.info(f"  カメラ設定: {'BLUE' if team_id == 100 else 'RED'}チームジャングラー")
    press_jungler_key(team_id)

    # ゲーム開始時のスコアボード表示
    logger.info("  スコアボード表示 (ゲーム開始)")
    _run_ahk(AHK_KEY_O)
    time.sleep(SCOREBOARD_DURATION)
    _run_ahk(AHK_KEY_O)
    logger.info("  スコアボード非表示")

    # ゲーム終了まで待機 → OBS録画停止
    try:
        wait_for_game_end(game_duration, team_id)
    finally:
        obs_mkv = obs_stop_record()
        kill_lol()

    if obs_mkv is None or not obs_mkv.exists():
        logger.error("  OBS録画ファイルが見つからない")
        return None

    # MKV → MP4 リマックス (再エンコードなし)
    if not mkv_to_mp4(obs_mkv, mp4_path):
        return None

    return mp4_path


# -----------------------------------------------
# メイン
# -----------------------------------------------

def run(max_matches: int = MAX_MATCHES, on_recorded=None, interactive: bool = False) -> list:
    """
    録画パイプラインを実行し、録画できた game_id のリストを返す。
    main.py などから呼び出し可能。

    Args:
        on_recorded:  1試合録画完了時に呼ばれるコールバック (game_id: int) -> None
        interactive:  True の場合、録画前に候補一覧を表示して確認プロンプトを出す
    """
    Path("logs").mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    riot_client = RiotAPIClient(config["riot"]["api_key"])
    lcu         = LCUClient(config["lol"]["install_path"])
    downloader  = ReplayDownloader(lcu=lcu, riot_client=riot_client)

    top_n = config.get("crawler", {}).get("top_n_players", 100)

    # Step 1: JP チャレンジャー上位プレイヤーのロールを判定し、試合を収集
    logger.info("=== JP チャレンジャー上位選手の JUNGLE 試合を収集中 ===")
    candidates = downloader.find_downloadable_matches(
        max_matches=200,
        top_n=top_n,
    )
    if not candidates:
        logger.error("ダウンロード可能な試合が見つからない (現パッチの試合のみ対応)")
        sys.exit(1)

    # 候補リストをキャッシュに保存 (game_start_ms 昇順=古い順)
    try:
        Path("cache").mkdir(exist_ok=True)
        save_list = sorted(candidates, key=lambda c: c.get("game_start_ms", 0))
        with open("cache/match_candidates.json", "w", encoding="utf-8") as f:
            json.dump(save_list, f, ensure_ascii=False, indent=2)
        logger.info(f"候補リストを保存: cache/match_candidates.json ({len(save_list)} 件)")
        # 候補をCSVに暫定登録 (champion/player/KDA含む、video_mbは録画後に更新)
        from src.db.match_registry import bulk_upsert_candidates
        bulk_upsert_candidates(save_list)
        logger.info(f"候補 {len(save_list)} 件をCSVに暫定登録")

        # 日次スナップショット保存
        try:
            import json as _json
            from src.db.daily_snapshot import save_jungle_matches, save_players
            save_jungle_matches(save_list)
            _roles = {}
            _roles_path = Path("cache/player_roles.json")
            if _roles_path.exists():
                _roles = _json.loads(_roles_path.read_text(encoding="utf-8"))
            save_players(config["riot"]["api_key"], _roles)
        except Exception as _e:
            logger.warning(f"日次スナップショット保存失敗: {_e}")

    except Exception as e:
        logger.warning(f"候補リスト保存失敗: {e}")

    # 録画済みを除外した実際の対象一覧
    # (タイトルリネーム済みの場合は video_filename でも確認)
    try:
        from src.db.match_registry import get_row as registry_get_row
    except Exception:
        registry_get_row = None

    def _is_already_recorded(game_id) -> bool:
        gid_str = str(game_id)
        # {game_id}.mp4 が存在する場合
        old_mp4 = OUTPUT_DIR / f"{gid_str}.mp4"
        if old_mp4.exists() and old_mp4.stat().st_size > 1_000_000:
            return True
        # レジストリに video_filename が登録されていればそちらも確認
        if registry_get_row:
            row = registry_get_row(gid_str)
            if row and row.get("video_filename"):
                titled_mp4 = OUTPUT_DIR / row["video_filename"]
                if titled_mp4.exists() and titled_mp4.stat().st_size > 1_000_000:
                    return True
        return False

    unrecorded = [c for c in candidates if not _is_already_recorded(c["game_id"])]

    logger.info(f"\n{'='*50}")
    logger.info(f"録画候補: {len(unrecorded)} 件 (録画済みスキップ: {len(candidates) - len(unrecorded)} 件)")
    logger.info(f"{'='*50}")
    for i, c in enumerate(unrecorded, 1):
        logger.info(f"  {i:2}. {c['match_id']}  ({c['age_hours']:.1f}h前)")
    logger.info(f"{'='*50}\n")

    if interactive:
        ans = input(f"{len(unrecorded)} 試合を録画しますか？ [Y/n] ").strip().lower()
        if ans == "n":
            logger.info("録画をキャンセルしました")
            return []

    # Step 2: 各試合を処理 (録画済みは既に除外済みの unrecorded を使用)
    processed = 0
    recorded_game_ids = []
    for match_info in unrecorded:
        if processed >= max_matches:
            break

        game_id  = match_info["game_id"]
        match_id = match_info["match_id"]

        logger.info(f"\n{'='*50}")
        logger.info(f"処理開始: {match_id}")

        # マッチデータ取得
        match_data = riot_client.get_match(match_id)
        if not match_data:
            logger.warning("マッチデータ取得失敗 → スキップ")
            continue

        team_id, _ = get_winning_jungler(match_data)
        game_duration = get_game_duration(match_data)
        stats         = get_player_stats(match_data)

        if game_duration < 60:
            logger.warning(f"試合時間が短すぎる ({game_duration}s) → スキップ")
            continue

        # リプレイDL
        logger.info("リプレイダウンロード中...")
        if not downloader.download_replay(game_id):
            logger.warning("ダウンロード失敗 → スキップ")
            continue

        # 録画
        result = process_match(
            game_id=game_id,
            match_id=match_id,
            team_id=team_id,
            game_duration=game_duration,
            lcu=lcu,
        )

        if result:
            if stats:
                save_metadata(game_id, match_id, stats, riot_client=riot_client)

            # mp4 をタイトルベースのファイル名にリネーム
            if stats:
                title = make_title(stats)
                safe_name = safe_filename(title)
                new_mp4 = OUTPUT_DIR / f"{safe_name}.mp4"
                old_mp4 = OUTPUT_DIR / f"{game_id}.mp4"
                if old_mp4.exists() and old_mp4 != new_mp4:
                    old_mp4.rename(new_mp4)
                    logger.info(f"  リネーム: {old_mp4.name} → {new_mp4.name}")
                    result = new_mp4
                # レジストリに video_filename を記録
                try:
                    from src.db.match_registry import update_video_filename
                    update_video_filename(game_id, new_mp4.name)
                except Exception as e:
                    logger.warning(f"video_filename更新失敗: {e}")

            logger.info(f"✓ 保存: {result}")
            recorded_game_ids.append(game_id)
            processed += 1
            if on_recorded:
                on_recorded(game_id)
        else:
            logger.error(f"✗ 録画失敗: {match_id}")

    logger.info(f"\n=== 完了: {processed} 試合を録画 ===")
    logger.info(f"保存先: {OUTPUT_DIR.resolve()}")

    # 試合リスト表示
    if processed > 0:
        try:
            from src.db.match_registry import get_all
            rows = get_all()
            logger.info(f"\n--- 試合リスト ({len(rows)} 件) ---")
            for r in rows[:10]:  # 最新10件
                kda = f"{r['kills']}/{r['deaths']}/{r['assists']}"
                yt  = "✓" if r.get("youtube_url") else "-"
                logger.info(f"  {r['game_id']}  {r['champion']:<12} {kda:<10} {r['player']}  [{yt}]")
            logger.info(f"詳細: python match_list.py --show")
        except Exception:
            pass

    return recorded_game_ids


def main():
    run()


if __name__ == "__main__":
    main()
