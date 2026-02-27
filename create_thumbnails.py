"""
LoL サムネイル自動生成スクリプト

output/videos/*.json を読み込み、ChallengerReplays 風の
サムネイル画像 (1280×720 JPEG) を output/thumbnails/ に生成する。

使い方:
    python create_thumbnails.py              # 未生成のサムネイルをすべて作成
    python create_thumbnails.py 567080016   # 指定game_idのみ再生成
    python create_thumbnails.py --force     # 全件強制再生成
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from loguru import logger

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

# -----------------------------------------------
# パス設定
# -----------------------------------------------
VIDEOS_DIR     = Path("output/videos")
THUMBNAILS_DIR = Path("output/thumbnails")
ASSETS_DIR     = Path("assets")
DDRAGON        = "https://ddragon.leagueoflegends.com"

# ランクエンブレム (assets/emblems/{Tier}.png 優先、なければ GitHub CDN)
_EMBLEM_BASE = "https://cdn.jsdelivr.net/gh/magisteriis/lol-icons-and-emblems/ranked-emblems"
RANK_EMBLEM_URLS = {
    "PLATINUM":    f"{_EMBLEM_BASE}/Emblem_Platinum.png",
    "GOLD":        f"{_EMBLEM_BASE}/Emblem_Gold.png",
    "SILVER":      f"{_EMBLEM_BASE}/Emblem_Silver.png",
    "BRONZE":      f"{_EMBLEM_BASE}/Emblem_Bronze.png",
    "IRON":        f"{_EMBLEM_BASE}/Emblem_Iron.png",
}
_JUNGLE_ICON_PATH = ASSETS_DIR / "icons" / "Jungle_icon.png"

# -----------------------------------------------
# カラー
# -----------------------------------------------
C_BG    = (8, 10, 20)
C_BAND  = (52, 42, 94)
C_BAND2 = (22, 15, 50)
C_WHITE = (255, 255, 255)
C_GOLD  = (215, 175, 55)
C_GRAY  = (170, 170, 185)
C_DARK  = (16, 18, 30)

# -----------------------------------------------
# レイアウト定数
# -----------------------------------------------
W, H    = 1280, 720
LEFT_W  = 580       # 左パネル幅 (右パネル = 700px)
RIGHT_W = W - LEFT_W
PAD     = 20        # 左パネル内側余白

# Yレイアウト
Y_ICON   = 14       # Row1: アイコン行 (height=100)
Y_PLAYER = 124      # Row2: プレイヤー名帯 (height=92)
Y_KDA    = 230      # Row3: KDAサークル (radius=44, +label20 = 108)
Y_ITEMS  = 352      # Row4: アイテム (2×84px + 8gap = 176) → ends 528
Y_BOTTOM = 538      # Row5: スペル+ルーン+スタッツ (56px) → ends 594

ICON_SZ   = 90   # チャンピオンアイコン
EMBLEM_SZ = 94   # ランクエンブレム
JUNGLE_SZ = 58   # ジャングルアイコン
KDA_R     = 44
ITEM_SZ   = 82
ITEM_GAP  = 8
SPELL_SZ  = 58
RUNE_SZ   = 58

# -----------------------------------------------
# フォント
# -----------------------------------------------
FONT_DIR = Path("C:/Windows/Fonts")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for path in [FONT_DIR / name, Path(name)]:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _fit_font(text: str, max_w: int, font_path: str,
              max_sz: int, min_size: int = 28) -> ImageFont.FreeTypeFont:
    min_sz = min_size
    dummy = ImageDraw.Draw(Image.new("L", (1, 1)))
    for sz in range(max_sz, min_sz - 1, -2):
        f = _font(font_path, sz)
        bb = dummy.textbbox((0, 0), text, font=f)
        if (bb[2] - bb[0]) <= max_w:
            return f
    return _font(font_path, min_sz)


def _get_fonts() -> dict:
    return {
        "player_lg": _font("impact.ttf",  84),
        "kda_n":     _font("impact.ttf",  46),
        "kda_l":     _font("arialbd.ttf", 15),
        "stats":     _font("arialbd.ttf", 21),
        "meta":      _font("arialbd.ttf", 22),
        "meta_sm":   _font("arial.ttf",   18),
    }


# -----------------------------------------------
# アセット取得 (キャッシュ付き)
# -----------------------------------------------

def _dl(url: str, path: Path) -> bool:
    if path.exists() and path.stat().st_size > 0:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(url, timeout=20)
        if r.ok:
            path.write_bytes(r.content)
            return True
    except Exception as e:
        logger.warning(f"DL失敗: {url} → {e}")
    return False


def _open(path: Path, size: tuple = None) -> Image.Image | None:
    if not path.exists():
        return None
    try:
        img = Image.open(path).convert("RGBA")
        if size:
            img = img.resize(size, Image.LANCZOS)
        return img
    except Exception:
        return None


def get_patch() -> str:
    path = ASSETS_DIR / "patch.txt"
    if path.exists():
        return path.read_text().strip()
    try:
        r = requests.get(f"{DDRAGON}/api/versions.json", timeout=10)
        patch = r.json()[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(patch)
        return patch
    except Exception:
        return "15.4.1"


def get_summoner_map(patch: str) -> dict:
    path = ASSETS_DIR / "summoner.json"
    _dl(f"{DDRAGON}/cdn/{patch}/data/en_US/summoner.json", path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(v["key"]): k for k, v in data["data"].items()}
    except Exception:
        return {}


def get_rune_map(patch: str) -> dict:
    path = ASSETS_DIR / "runesReforged.json"
    _dl(f"{DDRAGON}/cdn/{patch}/data/en_US/runesReforged.json", path)
    result = {}
    try:
        for tree in json.loads(path.read_text(encoding="utf-8")):
            result[tree["id"]] = tree["icon"]
            for slot in tree.get("slots", []):
                for rune in slot.get("runes", []):
                    result[rune["id"]] = rune["icon"]
    except Exception:
        pass
    return result


# -----------------------------------------------
# アイコン取得
# -----------------------------------------------

def champion_icon(champion: str, patch: str) -> Image.Image | None:
    path = ASSETS_DIR / "champion" / f"{champion}.png"
    _dl(f"{DDRAGON}/cdn/{patch}/img/champion/{champion}.png", path)
    return _open(path, (ICON_SZ, ICON_SZ))


def rank_emblem(tier_str: str, size: int) -> Image.Image | None:
    """ランク文字列 (例: 'Challenger 1500 LP') からエンブレム画像を返す"""
    tier = tier_str.upper().split()[0]
    # assets/emblems/{Tier}.png を優先 (Challenger.png, Diamond.png 等)
    local = ASSETS_DIR / "emblems" / f"{tier.title()}.png"
    if local.exists():
        return _open(local, (size, size))
    # なければ CDN からダウンロード
    url = RANK_EMBLEM_URLS.get(tier)
    if not url:
        return None
    path = ASSETS_DIR / "emblems" / f"{tier.lower()}.png"
    _dl(url, path)
    return _open(path, (size, size))


def jungle_icon(size: int) -> Image.Image:
    """ジャングルポジションアイコンをローカルPNGから読み込む"""
    if _JUNGLE_ICON_PATH.exists():
        img = Image.open(_JUNGLE_ICON_PATH).convert("RGBA")
        return img.resize((size, size), Image.LANCZOS)
    # フォールバック: 透明の空画像
    return Image.new("RGBA", (size, size), (0, 0, 0, 0))


def item_icon(item_id: int, patch: str) -> Image.Image | None:
    if not item_id:
        return None
    path = ASSETS_DIR / "items" / f"{item_id}.png"
    _dl(f"{DDRAGON}/cdn/{patch}/img/item/{item_id}.png", path)
    return _open(path, (ITEM_SZ, ITEM_SZ))


def spell_icon(spell_id: int, patch: str, summ_map: dict) -> Image.Image | None:
    name = summ_map.get(spell_id)
    if not name:
        return None
    path = ASSETS_DIR / "spells" / f"{name}.png"
    _dl(f"{DDRAGON}/cdn/{patch}/img/spell/{name}.png", path)
    return _open(path, (SPELL_SZ, SPELL_SZ))


def rune_icon(rune_id: int, patch: str, rune_map: dict) -> Image.Image | None:
    icon_path = rune_map.get(rune_id)
    if not icon_path:
        return None
    path = ASSETS_DIR / "runes" / icon_path.replace("/", "_")
    _dl(f"{DDRAGON}/cdn/img/{icon_path}", path)
    return _open(path, (RUNE_SZ, RUNE_SZ))


# -----------------------------------------------
# チャンピオン ローディングアート (縦長ポートレート)
# ローディングスクリーンアート: 308×560 (公式縦長ポートレート)
# -----------------------------------------------

def get_loading_art(champion: str) -> Image.Image | None:
    path = ASSETS_DIR / "loading" / f"{champion}.jpg"
    _dl(f"{DDRAGON}/cdn/img/champion/loading/{champion}_0.jpg", path)
    return _open(path)


# -----------------------------------------------
# 描画ユーティリティ
# -----------------------------------------------

def text_shadow(draw, pos, text, font, fill, shadow=2, sfill=(0, 0, 0, 230)):
    x, y = pos
    draw.text((x + shadow, y + shadow), text, font=font, fill=sfill)
    draw.text((x, y), text, font=font, fill=fill)


def paste_circle(canvas, icon, cx, cy):
    if icon is None:
        return
    w, h = icon.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, w - 1, h - 1], fill=255)
    canvas.paste(icon, (cx - w // 2, cy - h // 2), mask)


def paste_rounded(canvas, icon, x, y, r=6):
    if icon is None:
        return
    w, h = icon.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    canvas.paste(icon, (x, y), mask)


def h_gradient(width, height, cl, cr) -> Image.Image:
    img = Image.new("RGB", (width, height))
    xs  = np.linspace(0, 1, width)
    for x, t in enumerate(xs):
        pixel = tuple(int(cl[i] * (1 - t) + cr[i] * t) for i in range(3))
        for y in range(height):
            img.putpixel((x, y), pixel)
    return img


def slot_bg(draw, x, y, sz, outline=None, r=6):
    draw.rounded_rectangle([x, y, x + sz, y + sz], radius=r,
                            fill=(*C_DARK, 180), outline=outline or C_DARK, width=1)


# -----------------------------------------------
# サムネイル合成
# -----------------------------------------------

def make_thumbnail(meta: dict, patch: str, summ_map: dict, rune_map: dict) -> Image.Image:
    fonts    = _get_fonts()
    champion = meta.get("champion", "Unknown")
    player   = meta.get("player", "")
    tier_str = meta.get("rank", "Challenger")
    kills    = meta.get("kills", 0)
    deaths   = meta.get("deaths", 0)
    assists  = meta.get("assists", 0)
    cs       = meta.get("cs", 0)
    gold     = meta.get("gold", 0)
    items    = (meta.get("items") or []) + [0] * 7
    spells   = (meta.get("summoner_spells") or []) + [0, 0]
    keystone = meta.get("rune_keystone", 0)
    sec_sty  = meta.get("rune_secondary_style", 0)

    # パッチバージョンと試合開始日時
    game_ver = meta.get("game_version", "") or patch[:4]  # 例: "16.4"
    start_ms = meta.get("game_start_ms", 0)
    if start_ms:
        dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).astimezone()
        date_str = dt.strftime("%Y-%m-%d %H:%M JST")
    else:
        date_str = ""

    canvas = Image.new("RGBA", (W, H), C_BG)

    # =========================================================
    # 右パネル: ローディングスクリーンアート (縦長ポートレート)
    # 308×560 → 右パネル幅いっぱいにスケール → 上部を表示
    # =========================================================
    la = get_loading_art(champion)
    if la:
        lw, lh = la.size   # typically 308×560
        # 右パネル幅いっぱいにスケール
        scale  = RIGHT_W / lw
        new_h  = int(lh * scale)
        la_sc  = la.resize((RIGHT_W, new_h), Image.LANCZOS)
        # 上部 H px を表示 (頭から腰あたりまで)
        la_crop = la_sc.crop((0, 0, RIGHT_W, min(new_h, H)))
        # 高さが H に満たない場合は上端に配置
        canvas.paste(la_crop.convert("RGBA"), (LEFT_W, 0))

        # ローディングアートの左端に左→透明グラデーション (左パネルとなじませる)
        fade_w = 80
        fade = Image.new("RGBA", (fade_w, H))
        for x in range(fade_w):
            alpha = int(255 * ((1 - x / fade_w) ** 1.6))
            for y in range(H):
                fade.putpixel((x, y), (*C_BG, alpha))
        canvas.paste(fade, (LEFT_W, 0), fade)

    # =========================================================
    # 左パネル: ブラー背景 + コンテンツ
    # =========================================================
    # ローディングアートをぼかして左パネルの背景にする
    if la:
        lb = la.resize((LEFT_W, H), Image.LANCZOS).convert("RGBA")
        lb = lb.filter(ImageFilter.GaussianBlur(radius=18))
        dark_ov = Image.new("RGBA", (LEFT_W, H), (0, 0, 0, 210))
        lb.paste(dark_ov, (0, 0), dark_ov)
        canvas.paste(lb, (0, 0), lb)
    else:
        canvas.paste(Image.new("RGBA", (LEFT_W, H), (*C_BG, 255)), (0, 0))

    draw = ImageDraw.Draw(canvas)

    # =========================================================
    # Row1: チャンピオンアイコン + ランクエンブレム + ジャングルアイコン
    # =========================================================
    # チャンピオンアイコン (左端、角丸+ゴールドボーダー)
    cicon = champion_icon(champion, patch)
    if cicon:
        paste_rounded(canvas, cicon, PAD, Y_ICON, r=10)
        draw.rounded_rectangle(
            [PAD - 2, Y_ICON - 2, PAD + ICON_SZ + 1, Y_ICON + ICON_SZ + 1],
            radius=11, outline=C_GOLD, width=2,
        )

    # ジャングルアイコン (右端)
    jicon = jungle_icon(JUNGLE_SZ)
    j_x = LEFT_W - PAD - JUNGLE_SZ
    j_y = Y_ICON + (ICON_SZ - JUNGLE_SZ) // 2
    canvas.paste(jicon, (j_x, j_y), jicon)

    # =========================================================
    # Row2: プレイヤー名帯 (大きく)
    # =========================================================
    band_h = 92
    band = h_gradient(LEFT_W, band_h, C_BAND, C_BAND2)
    canvas.paste(band.convert("RGBA"), (0, Y_PLAYER))

    max_name_w = LEFT_W - PAD * 2
    pf = _fit_font(player, max_name_w, "meiryob.ttc", 84, min_size=28)
    pb = draw.textbbox((0, 0), player, font=pf)
    pw, ph = pb[2] - pb[0], pb[3] - pb[1]
    py = Y_PLAYER + (band_h - ph) // 2 - pb[1]
    text_shadow(draw, (PAD, py), player, pf, C_WHITE, shadow=3)

    # ランクエンブレム: プレイヤー名の右端のすぐ下
    emb_sz = 120
    emb = rank_emblem(tier_str, emb_sz)
    if emb:
        emb_x = min(PAD + pw, LEFT_W - PAD - emb_sz)
        emb_y = Y_PLAYER + band_h
        canvas.paste(emb, (emb_x, emb_y), emb)

    # =========================================================
    # Row3: KDA サークル
    # =========================================================
    kda_vals = [(kills, "K"), (deaths, "D"), (assists, "A")]
    for i, (val, label) in enumerate(kda_vals):
        cx = PAD + KDA_R + i * (KDA_R * 2 + 16)
        cy = Y_KDA + KDA_R
        draw.ellipse([cx - KDA_R, cy - KDA_R, cx + KDA_R, cy + KDA_R], fill=(*C_DARK, 200))
        draw.ellipse([cx - KDA_R, cy - KDA_R, cx + KDA_R, cy + KDA_R],
                     outline=C_GRAY, width=1)
        n  = str(val)
        nb = draw.textbbox((0, 0), n, font=fonts["kda_n"])
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]
        text_shadow(draw, (cx - nw // 2 - nb[0], cy - nh // 2 - nb[1] - 5),
                    n, fonts["kda_n"], C_WHITE, shadow=2)
        lb = draw.textbbox((0, 0), label, font=fonts["kda_l"])
        draw.text((cx - (lb[2] - lb[0]) // 2, cy + KDA_R - 17),
                  label, font=fonts["kda_l"], fill=C_GRAY)

    # =========================================================
    # Row4: アイテムグリッド 2×3 + トリンケット + スペル + ルーン
    # =========================================================
    main_items = items[:6]
    trinket    = items[6] if len(items) > 6 else 0

    for idx, iid in enumerate(main_items):
        col, row = idx % 3, idx // 3
        ix = PAD + col * (ITEM_SZ + ITEM_GAP)
        iy = Y_ITEMS + row * (ITEM_SZ + ITEM_GAP)
        slot_bg(draw, ix, iy, ITEM_SZ)
        ic = item_icon(iid, patch)
        if ic:
            paste_rounded(canvas, ic, ix, iy, r=6)

    # トリンケット (ゴールドボーダー)
    tri_x = PAD + 3 * (ITEM_SZ + ITEM_GAP) + 6
    tri_y = Y_ITEMS
    slot_bg(draw, tri_x, tri_y, ITEM_SZ, outline=C_GOLD)
    tri_ic = item_icon(trinket, patch)
    if tri_ic:
        paste_rounded(canvas, tri_ic, tri_x, tri_y, r=6)

    # スペル (縦2個)
    sp_x = tri_x + ITEM_SZ + 8
    for si, sid in enumerate(spells[:2]):
        sp_y = Y_ITEMS + si * (SPELL_SZ + ITEM_GAP)
        slot_bg(draw, sp_x, sp_y, SPELL_SZ)
        sic = spell_icon(sid, patch, summ_map)
        if sic:
            paste_rounded(canvas, sic, sp_x, sp_y, r=6)

    # ルーン (キーストーンのみ、円形)
    ru_x = sp_x + SPELL_SZ + 8
    ru_y = Y_ITEMS + (RUNE_SZ + ITEM_GAP) // 2  # スペル2個の縦中央に合わせる
    ric  = rune_icon(keystone, patch, rune_map)
    circ = Image.new("RGBA", (RUNE_SZ, RUNE_SZ), (*C_DARK, 200))
    cmask = Image.new("L", (RUNE_SZ, RUNE_SZ), 0)
    ImageDraw.Draw(cmask).ellipse([0, 0, RUNE_SZ - 1, RUNE_SZ - 1], fill=255)
    canvas.paste(circ, (ru_x, ru_y), cmask)
    if ric:
        paste_circle(canvas, ric, ru_x + RUNE_SZ // 2, ru_y + RUNE_SZ // 2)

    # =========================================================
    # 下部: パッチバージョン (巨大・中央) + 試合日時 (小・下端)
    # =========================================================
    # スペル行終端 y ≈ Y_ITEMS + SPELL_SZ*2 + ITEM_GAP = 352+58+8+58 = 476
    # 残り空間: 476 → 720 = 244px → その中央にパッチテキスト
    Y_CONTENT_END = Y_ITEMS + SPELL_SZ * 2 + ITEM_GAP  # ≈ 476
    Y_DATE        = H - 14  # 最下端に日時

    if game_ver:
        patch_text = f"Patch {game_ver}"
        pf_big = _fit_font(patch_text, LEFT_W - PAD * 2, "impact.ttf", 130, min_size=60)
        pb = draw.textbbox((0, 0), patch_text, font=pf_big)
        pw, ph = pb[2] - pb[0], pb[3] - pb[1]
        center_y = (Y_CONTENT_END + Y_DATE) // 2
        px = (LEFT_W - pw) // 2 - pb[0]
        py = center_y - ph // 2 - pb[1]
        text_shadow(draw, (px, py), patch_text, pf_big, C_GOLD, shadow=4)

    # 日時 (最下端、中央寄せ、グレー)
    if date_str:
        db = draw.textbbox((0, 0), date_str, font=fonts["meta_sm"])
        dw = db[2] - db[0]
        text_shadow(draw, ((LEFT_W - dw) // 2, Y_DATE - (db[3] - db[1]) - 2),
                    date_str, fonts["meta_sm"], C_GRAY, shadow=1)

    return canvas.convert("RGB")


# -----------------------------------------------
# メイン
# -----------------------------------------------

def main():
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(exist_ok=True)

    force = "--force" in sys.argv
    args  = [a for a in sys.argv[1:] if not a.startswith("--")]

    # 既存サムネイルを削除してからリフレッシュ
    old_thumbs = list(THUMBNAILS_DIR.glob("*.jpg"))
    if old_thumbs:
        for t in old_thumbs:
            t.unlink()
        logger.info(f"古いサムネイル {len(old_thumbs)} 件を削除")

    patch    = get_patch()
    summ_map = get_summoner_map(patch)
    rune_map = get_rune_map(patch)
    logger.info(f"パッチ: {patch}")

    if args:
        json_files = [VIDEOS_DIR / f"{a}.json" for a in args if a.isdigit()]
    else:
        json_files = sorted(VIDEOS_DIR.glob("*.json"))

    for json_path in json_files:
        if not json_path.exists():
            logger.warning(f"JSONなし: {json_path}")
            continue

        meta    = json.loads(json_path.read_text(encoding="utf-8"))
        game_id = meta.get("game_id", json_path.stem)
        out     = THUMBNAILS_DIR / f"{game_id}.jpg"

        if out.exists() and not force:
            logger.info(f"スキップ (生成済み): {out.name}")
            continue

        logger.info(
            f"生成中: {game_id} ({meta.get('champion')} {meta.get('kda')} / {meta.get('player')})"
        )
        try:
            img = make_thumbnail(meta, patch, summ_map, rune_map)
            img.save(str(out), "JPEG", quality=95)
            logger.info(f"  → {out}")
        except Exception as e:
            logger.error(f"  生成失敗: {e}")
            import traceback; traceback.print_exc()

    logger.info("完了")


def generate(game_ids: list) -> None:
    """
    指定した game_id のサムネイルを生成する。
    main.py などから呼び出し可能。

    Args:
        game_ids: game_id (int または str) のリスト
    """
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(exist_ok=True)

    patch    = get_patch()
    summ_map = get_summoner_map(patch)
    rune_map = get_rune_map(patch)

    for gid in game_ids:
        json_path = VIDEOS_DIR / f"{gid}.json"
        if not json_path.exists():
            logger.warning(f"JSONなし: {json_path}")
            continue

        meta    = json.loads(json_path.read_text(encoding="utf-8"))
        game_id = meta.get("game_id", json_path.stem)
        out     = THUMBNAILS_DIR / f"{game_id}.jpg"

        logger.info(f"サムネイル生成: {game_id} ({meta.get('champion')} {meta.get('kda')} / {meta.get('player')})")
        try:
            img = make_thumbnail(meta, patch, summ_map, rune_map)
            img.save(str(out), "JPEG", quality=95)
            logger.info(f"  → {out}")
        except Exception as e:
            logger.error(f"  生成失敗: {e}")


if __name__ == "__main__":
    main()
