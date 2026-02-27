"""
FFmpegを使ってクリップを結合し、最終動画を生成する

処理内容:
1. 各ハイライトクリップを連結
2. タイトルカード (試合情報オーバーレイ) 追加
3. ハイライトラベルのテキストオーバーレイ
4. サムネイル画像生成
"""
from __future__ import annotations
import subprocess
import json
from pathlib import Path
from typing import Optional
from datetime import datetime

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from ..analysis.highlight_detector import Highlight


class VideoComposer:
    """
    FFmpegベースの動画合成クラス

    必要環境: FFmpegがPATHに存在すること
    インストール: https://ffmpeg.org/download.html
    """

    def __init__(self, config: dict):
        self.config = config
        self.output_dir = Path(config.get("output_dir", "output/videos"))
        self.thumbnail_dir = Path(config.get("thumbnail_dir", "output/thumbnails"))
        self.font_path = config.get("font", "C:/Windows/Fonts/meiryo.ttc")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)

    def _run_ffmpeg(self, args: list[str]) -> bool:
        """FFmpegを実行する"""
        cmd = ["ffmpeg", "-y"] + args
        logger.debug(f"FFmpeg: {' '.join(cmd[:8])}...")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600
            )
            if result.returncode != 0:
                logger.error(f"FFmpeg エラー:\n{result.stderr[-500:]}")
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg タイムアウト")
            return False
        except FileNotFoundError:
            logger.error("FFmpegが見つかりません。PATH に追加されているか確認してください。")
            return False

    def concat_clips(
        self,
        clip_paths: list[Path],
        output_path: Path,
        add_transitions: bool = True,
    ) -> bool:
        """
        複数のクリップを1つの動画に結合する

        Args:
            clip_paths: 入力クリップのパスリスト
            output_path: 出力先
            add_transitions: クリップ間にフェードを追加するか
        """
        if not clip_paths:
            logger.error("結合するクリップがありません")
            return False

        existing_clips = [p for p in clip_paths if p.exists()]
        if not existing_clips:
            logger.error("存在するクリップがありません")
            return False

        if len(existing_clips) == 1:
            # 1クリップのみの場合は変換だけ
            return self._convert_clip(existing_clips[0], output_path)

        # concat filter を使用
        # 一時ファイルリスト作成
        list_file = output_path.parent / "_concat_list.txt"
        with open(list_file, "w") as f:
            for p in existing_clips:
                f.write(f"file '{p.as_posix()}'\n")

        success = self._run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path)
        ])

        list_file.unlink(missing_ok=True)
        return success

    def _convert_clip(self, input_path: Path, output_path: Path) -> bool:
        """単一クリップをMP4に変換"""
        return self._run_ffmpeg([
            "-i", str(input_path),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
            str(output_path)
        ])

    def add_highlight_labels(
        self,
        input_path: Path,
        output_path: Path,
        highlights: list[Highlight],
        clip_durations: list[float],
    ) -> bool:
        """
        各ハイライトクリップにテキストラベルを追加する

        Args:
            input_path: 連結済み動画
            output_path: テキスト付き出力
            highlights: ハイライトリスト
            clip_durations: 各クリップの長さ (秒)
        """
        filter_parts = []
        current_time = 0.0

        for i, (h, dur) in enumerate(zip(highlights, clip_durations)):
            # クリップ開始後2秒間ラベルを表示
            show_start = current_time + 0.5
            show_end   = current_time + 3.0

            # チャンピオン名とハイライトタイプ
            top_text    = h.champion_name if h.champion_name else ""
            bottom_text = h.label

            if top_text:
                filter_parts.append(
                    f"drawtext=fontfile='{self.font_path}':"
                    f"text='{top_text}':"
                    f"fontsize=36:fontcolor=white:"
                    f"borderw=3:bordercolor=black:"
                    f"x=(w-tw)/2:y=h-120:"
                    f"enable='between(t,{show_start:.1f},{show_end:.1f})'"
                )
            filter_parts.append(
                f"drawtext=fontfile='{self.font_path}':"
                f"text='{bottom_text}':"
                f"fontsize=52:fontcolor=yellow:"
                f"borderw=3:bordercolor=black:"
                f"x=(w-tw)/2:y=h-70:"
                f"enable='between(t,{show_start:.1f},{show_end:.1f})'"
            )
            current_time += dur

        if not filter_parts:
            # テキストなしで変換のみ
            return self._convert_clip(input_path, output_path)

        vf = ",".join(filter_parts)
        return self._run_ffmpeg([
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "copy",
            str(output_path)
        ])

    def add_intro_card(
        self,
        input_path: Path,
        output_path: Path,
        match_info: dict,
        duration: float = 3.0,
    ) -> bool:
        """
        動画の冒頭に試合情報カードを追加する (黒背景にテキスト)

        Args:
            match_info: {"title": str, "date": str, "participants": [...]}
        """
        # イントロ画像を生成
        intro_image = self._generate_intro_image(match_info)
        intro_image_path = self.output_dir / "_intro.png"
        intro_image.save(str(intro_image_path))

        # イントロ動画を生成 (静止画 → 動画)
        intro_video = self.output_dir / "_intro.mp4"
        success = self._run_ffmpeg([
            "-loop", "1",
            "-i", str(intro_image_path),
            "-t", str(duration),
            "-vf", "scale=1920:1080",
            "-c:v", "libx264",
            "-r", "30",
            "-pix_fmt", "yuv420p",
            str(intro_video)
        ])
        if not success:
            return self._convert_clip(input_path, output_path)

        # イントロ + 本編を連結
        list_file = self.output_dir / "_with_intro.txt"
        list_file.write_text(
            f"file '{intro_video.as_posix()}'\n"
            f"file '{input_path.as_posix()}'\n"
        )
        success = self._run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-c:a", "aac",
            str(output_path)
        ])

        # 一時ファイル削除
        intro_image_path.unlink(missing_ok=True)
        intro_video.unlink(missing_ok=True)
        list_file.unlink(missing_ok=True)

        return success

    def _generate_intro_image(self, match_info: dict) -> Image.Image:
        """イントロ用画像を生成 (黒背景+テキスト)"""
        img = Image.new("RGB", (1920, 1080), color=(10, 10, 20))
        draw = ImageDraw.Draw(img)

        try:
            font_large  = ImageFont.truetype(self.font_path, 72)
            font_medium = ImageFont.truetype(self.font_path, 42)
            font_small  = ImageFont.truetype(self.font_path, 32)
        except Exception:
            font_large = font_medium = font_small = ImageFont.load_default()

        title = match_info.get("title", "LOL Highlight")
        date  = match_info.get("date", "")
        tier  = match_info.get("tier", "")

        # タイトル
        draw.text((960, 400), title, fill=(255, 215, 0), font=font_large, anchor="mm")
        # ランク帯
        if tier:
            draw.text((960, 500), tier, fill=(200, 200, 200), font=font_medium, anchor="mm")
        # 日付
        if date:
            draw.text((960, 570), date, fill=(150, 150, 150), font=font_small, anchor="mm")
        # チャンネル名
        draw.text((960, 980), "JP Server Highlights", fill=(100, 100, 100), font=font_small, anchor="mm")

        # 参加チャンピオン
        participants = match_info.get("participants", [])
        if participants:
            champ_text = "  vs  ".join([
                " ".join(participants[:5]),
                " ".join(participants[5:10])
            ])
            draw.text((960, 640), champ_text, fill=(180, 180, 255), font=font_small, anchor="mm")

        return img

    def generate_thumbnail(
        self,
        match_info: dict,
        highlight: Highlight,
        output_path: Path,
        champion_icon_paths: dict[str, Path] = None,
    ) -> bool:
        """
        YouTubeサムネイル画像を生成 (1280x720)

        Args:
            match_info: 試合情報
            highlight: メインのハイライト (ペンタキルなど)
            champion_icon_paths: champ_name → アイコンパスのdict
        """
        img = Image.new("RGB", (1280, 720), color=(15, 15, 30))
        draw = ImageDraw.Draw(img)

        try:
            font_title = ImageFont.truetype(self.font_path, 90)
            font_sub   = ImageFont.truetype(self.font_path, 48)
            font_small = ImageFont.truetype(self.font_path, 36)
        except Exception:
            font_title = font_sub = font_small = ImageFont.load_default()

        # 背景グラデーション (上部が暗い)
        for y in range(360):
            alpha = int(y / 360 * 60)
            draw.line([(0, y), (1280, y)], fill=(0, 0, alpha))

        # メインテキスト (ハイライトタイプ)
        event_text = highlight.label
        draw.text((640, 280), event_text, fill=(255, 215, 0), font=font_title, anchor="mm",
                  stroke_width=4, stroke_fill=(0, 0, 0))

        # チャンピオン名
        if highlight.champion_name:
            draw.text((640, 390), highlight.champion_name, fill=(255, 255, 255),
                      font=font_sub, anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))

        # チャンピオンアイコン (あれば)
        if champion_icon_paths and highlight.champion_name in champion_icon_paths:
            icon_path = champion_icon_paths[highlight.champion_name]
            if icon_path.exists():
                try:
                    icon = Image.open(icon_path).resize((180, 180))
                    img.paste(icon, (550, 80))
                except Exception:
                    pass

        # 下部情報
        tier = match_info.get("tier", "Challenger")
        date = match_info.get("date", "")
        draw.text((640, 480), f"【JP Server】{tier}", fill=(180, 180, 180),
                  font=font_small, anchor="mm")
        if date:
            draw.text((640, 530), date, fill=(120, 120, 120), font=font_small, anchor="mm")

        # 枠線
        draw.rectangle([(10, 10), (1270, 710)], outline=(255, 215, 0), width=4)

        img.save(str(output_path), "JPEG", quality=95)
        logger.info(f"サムネイル生成: {output_path}")
        return True

    def compose_final_video(
        self,
        clip_paths: list[Path],
        highlights: list[Highlight],
        match_info: dict,
        game_id: int,
    ) -> Optional[Path]:
        """
        フルパイプライン: クリップ→結合→テキスト付き→イントロ追加

        Returns:
            最終動画のパス (失敗時はNone)
        """
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        base_name = f"lol_highlight_{game_id}_{date_str}"

        # Step 1: クリップ結合
        concat_path = self.output_dir / f"{base_name}_raw.mp4"
        if not self.concat_clips(clip_paths, concat_path):
            return None

        # Step 2: テキストラベル追加
        clip_durations = [13.0] * len(clip_paths)  # pre(8) + post(5)
        labeled_path = self.output_dir / f"{base_name}_labeled.mp4"
        if not self.add_highlight_labels(concat_path, labeled_path, highlights, clip_durations):
            labeled_path = concat_path

        # Step 3: イントロカード追加
        final_path = self.output_dir / f"{base_name}_final.mp4"
        if not self.add_intro_card(labeled_path, final_path, match_info):
            final_path = labeled_path

        # 中間ファイル削除
        if concat_path != final_path:
            concat_path.unlink(missing_ok=True)
        if labeled_path != final_path and labeled_path != concat_path:
            labeled_path.unlink(missing_ok=True)

        logger.info(f"最終動画生成完了: {final_path}")
        return final_path

    def get_video_duration(self, video_path: Path) -> Optional[float]:
        """動画の長さを取得 (秒)"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_streams", str(video_path)],
                capture_output=True, text=True, timeout=30
            )
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    return float(stream.get("duration", 0))
        except Exception:
            pass
        return None
