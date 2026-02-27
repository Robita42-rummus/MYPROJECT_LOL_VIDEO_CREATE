"""
YouTube Data API v3 を使って動画を自動アップロードする

セットアップ手順:
1. Google Cloud Console でプロジェクト作成
2. YouTube Data API v3 を有効化
3. OAuth 2.0 クライアントID を作成 (デスクトップアプリ)
4. クライアントシークレットJSONをダウンロード → credentials/youtube_client_secret.json
5. 初回実行時にブラウザで認証 → credentials/youtube_token.json に保存される
"""
from __future__ import annotations
import os
import json
import pickle
from pathlib import Path
from datetime import datetime
from typing import Optional

from loguru import logger

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    import google.auth.exceptions
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False
    logger.warning("google-api-python-client 未インストール: pip install google-api-python-client google-auth-oauthlib")


SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeUploader:
    """YouTube自動アップロードクラス"""

    def __init__(self, config: dict):
        self.config = config
        self.client_secrets = Path(config.get("client_secrets_file", "credentials/youtube_client_secret.json"))
        self.token_file = Path(config.get("token_file", "credentials/youtube_token.json"))
        self.privacy_status = config.get("privacy_status", "public")
        self.category_id = config.get("category_id", "20")
        self.title_template = config.get("title_template", "【LOL】{rank} {champion} {event} - {date}")
        self.tags = config.get("tags", ["LoL", "League of Legends", "ハイライト"])
        self._youtube = None

    def _authenticate(self):
        """OAuth2認証 (初回はブラウザ認証が必要)"""
        if not GOOGLE_API_AVAILABLE:
            raise RuntimeError("google-api-python-client をインストールしてください")

        creds = None

        # 保存済みトークンを読み込み
        if self.token_file.exists():
            with open(self.token_file, "rb") as f:
                creds = pickle.load(f)

        # トークンが無効または期限切れ
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except google.auth.exceptions.RefreshError:
                    creds = None

            if not creds:
                if not self.client_secrets.exists():
                    raise FileNotFoundError(
                        f"YouTube認証ファイルが見つかりません: {self.client_secrets}\n"
                        "Google Cloud Console でOAuth2クライアントIDを作成してください。"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.client_secrets), SCOPES
                )
                creds = flow.run_local_server(port=0)

            # トークンを保存
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_file, "wb") as f:
                pickle.dump(creds, f)

        self._youtube = build("youtube", "v3", credentials=creds)
        logger.info("YouTube API 認証成功")

    def _get_youtube(self):
        if self._youtube is None:
            self._authenticate()
        return self._youtube

    def build_title(self, match_info: dict, top_highlight_label: str) -> str:
        """タイトル文字列を生成"""
        now = datetime.now()
        return self.title_template.format(
            rank=match_info.get("tier", "Challenger"),
            champion=match_info.get("top_champion", ""),
            event=top_highlight_label,
            date=now.strftime("%Y/%m/%d"),
        )

    def build_description(self, match_info: dict, highlights) -> str:
        """動画説明文を生成"""
        lines = [
            f"【JP Server LOL ハイライト】",
            f"",
            f"ランク帯: {match_info.get('tier', 'Challenger')}",
            f"試合ID: {match_info.get('game_id', '')}",
            f"",
            "■ ハイライト一覧",
        ]
        for h in highlights:
            mins = int(h.game_time // 60)
            secs = int(h.game_time % 60)
            lines.append(f"  {mins:02d}:{secs:02d} - {h.label} ({h.champion_name})")

        lines += [
            "",
            "■ 参加チャンピオン",
            "  " + " / ".join(match_info.get("participants", [])),
            "",
            "#LeagueOfLegends #LoL #ハイライト #日本サーバー",
        ]
        return "\n".join(lines)

    def upload(
        self,
        video_path: Path,
        thumbnail_path: Optional[Path],
        match_info: dict,
        highlights: list,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[str]:
        """
        動画をYouTubeにアップロードする

        Args:
            video_path: アップロードする動画ファイル
            thumbnail_path: サムネイル画像 (Noneなら自動生成なし)
            match_info: 試合情報dict
            highlights: ハイライトリスト
            title: タイトル (省略時はbuild_titleで生成)
            description: 説明文 (省略時はbuild_descriptionで生成)

        Returns:
            YouTube動画URL (失敗時はNone)
        """
        if not video_path.exists():
            logger.error(f"動画ファイルが見つかりません: {video_path}")
            return None

        # タイトル・説明生成 (引数で渡された場合はそちらを優先)
        if title is None:
            top_event = highlights[0].label if highlights else "ハイライト"
            title = self.build_title(match_info, top_event)
        if description is None:
            description = self.build_description(match_info, highlights)

        # タグに動的情報を追加
        tags = list(self.tags)
        tags.extend(match_info.get("participants", []))

        body = {
            "snippet": {
                "title": title[:100],  # YouTube上限100文字
                "description": description[:5000],
                "tags": tags[:500],
                "categoryId": self.category_id,
                "defaultLanguage": "ja",
            },
            "status": {
                "privacyStatus": self.privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        logger.info(f"YouTube アップロード開始: {title}")
        logger.info(f"  ファイル: {video_path} ({video_path.stat().st_size // 1024 // 1024}MB)")

        # クォータ残量チェック
        try:
            from src.youtube.quota_tracker import can_upload, record_upload, log_status
            if not can_upload():
                log_status()
                return None
        except ImportError:
            pass

        try:
            youtube = self._get_youtube()
            media = MediaFileUpload(
                str(video_path),
                mimetype="video/mp4",
                resumable=True,
                chunksize=10 * 1024 * 1024,  # 10MB チャンク
            )

            request = youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media,
            )

            # チャンクアップロード (進捗表示)
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.info(f"  アップロード中... {progress}%")

            video_id = response["id"]
            url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info(f"アップロード完了: {url}")

            # クォータ消費を記録
            has_thumb = thumbnail_path is not None and thumbnail_path.exists()
            try:
                record_upload(with_thumbnail=has_thumb)
            except Exception:
                pass

            # サムネイル設定
            if has_thumb:
                self._set_thumbnail(youtube, video_id, thumbnail_path)

            return url

        except Exception as e:
            err = str(e)
            if "quotaExceeded" in err or "uploadLimitExceeded" in err:
                logger.warning(
                    f"YouTube クォータ上限に達しました。"
                    f"翌朝 9:00 JST 以降に再実行してください。"
                )
            else:
                logger.error(f"YouTube アップロード失敗: {e}")
            return None

    def _set_thumbnail(self, youtube, video_id: str, thumbnail_path: Path):
        """サムネイル画像を設定する"""
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg"),
            ).execute()
            logger.info("サムネイル設定完了")
        except Exception as e:
            logger.warning(f"サムネイル設定失敗 (動画は正常にアップロード済み): {e}")
