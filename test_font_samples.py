"""
サムネイル用フォントのサンプル比較画像を生成するスクリプト

output/font_samples/ に各フォントのサンプルを保存します。
中国語・日本語・英数字の混在表示を確認できます。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT_DIR  = Path("output/font_samples")
FONT_DIR = Path("C:/Windows/Fonts")

# サンプルとして使うプレイヤー名 (中国語・日本語・英数字の混在)
SAMPLE_NAMES = [
    "星辰月影#JP1",        # 简体中文 (Simplified Chinese)
    "暗夜刺客#JP1",        # 简体中文
    "葛葉#JP1",            # 日本語 (漢字)
    "senzawa#JP1",         # 英語
    "神龍#JP1",            # 日本語+中国語共通漢字
]

# テストするフォント (ファイル名: 表示名)
FONTS = {
    "meiryob.ttc":    "Meiryo Bold (現在)",
    "msyhbd.ttc":     "Microsoft YaHei Bold (簡体字中国語)",
    "msjhbd.ttc":     "Microsoft JhengHei Bold (繁体字中国語)",
    "YuGothB.ttc":    "Yu Gothic Bold (游ゴシック Bold)",
    "NotoSansJP-VF.ttf": "Noto Sans JP (Variable)",
    "msgothic.ttc":   "MS Gothic (MS ゴシック)",
}

# カラー (サムネイルと同じ)
C_BG    = (8,  10,  20)
C_BAND  = (52, 42,  94)
C_BAND2 = (22, 15,  50)
C_WHITE = (255, 255, 255)

W, H    = 1280, 120


def h_gradient(width, height, cl, cr) -> Image.Image:
    img = Image.new("RGB", (width, height))
    for x in range(width):
        t = x / width
        pixel = tuple(int(cl[i] * (1 - t) + cr[i] * t) for i in range(3))
        for y in range(height):
            img.putpixel((x, y), pixel)
    return img


def make_sample(font_path: str, font_label: str) -> Image.Image:
    # 全サンプル名を縦に並べる
    row_h = 100
    total_h = row_h * len(SAMPLE_NAMES) + 40  # 上部にラベル行
    canvas = Image.new("RGBA", (W, total_h), C_BG)
    draw   = ImageDraw.Draw(canvas)

    # フォントラベル
    try:
        label_font = ImageFont.truetype(str(FONT_DIR / "arialbd.ttf"), 22)
    except Exception:
        label_font = ImageFont.load_default()
    draw.text((20, 8), f"Font: {font_label}  ({font_path})", font=label_font,
              fill=(200, 200, 100))

    for i, name in enumerate(SAMPLE_NAMES):
        y = 40 + i * row_h

        # グラデーション帯
        band = h_gradient(W, row_h - 4, C_BAND, C_BAND2)
        canvas.paste(band.convert("RGBA"), (0, y))

        # フォント読み込み
        try:
            fp = str(FONT_DIR / font_path)
            font = ImageFont.truetype(fp, 72)
            # 幅に収まらなければ縮小
            bb = draw.textbbox((0, 0), name, font=font)
            if bb[2] - bb[0] > W - 40:
                for sz in range(72, 27, -2):
                    font = ImageFont.truetype(fp, sz)
                    bb = draw.textbbox((0, 0), name, font=font)
                    if bb[2] - bb[0] <= W - 40:
                        break
        except Exception:
            font = ImageFont.load_default()

        # テキスト描画
        bb  = draw.textbbox((0, 0), name, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        tx = 20
        ty = y + (row_h - th) // 2 - bb[1]
        # 影
        draw.text((tx + 2, ty + 2), name, font=font, fill=(0, 0, 0, 200))
        draw.text((tx, ty), name, font=font, fill=C_WHITE)

    return canvas.convert("RGB")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"フォントサンプルを {OUT_DIR} に生成中...")

    for font_file, font_label in FONTS.items():
        fp = FONT_DIR / font_file
        if not fp.exists():
            print(f"  スキップ (ファイルなし): {font_file}")
            continue

        try:
            img  = make_sample(font_file, font_label)
            safe = font_file.replace(".", "_").replace(" ", "_")
            out  = OUT_DIR / f"sample_{safe}.jpg"
            img.save(str(out), "JPEG", quality=95)
            print(f"  生成: {out.name}")
        except Exception as e:
            print(f"  エラー: {font_file} → {e}")
            import traceback; traceback.print_exc()

    print("\n完了！output/font_samples/ フォルダを開いて確認してください。")
    print("気に入ったフォントを教えてください。create_thumbnails.py に反映します。")


if __name__ == "__main__":
    main()
