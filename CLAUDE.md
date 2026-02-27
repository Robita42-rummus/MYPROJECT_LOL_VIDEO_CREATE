# 1. CLAUDE.md

このファイルはClaudeCodeがリポジトリで作業する際のガイダンスを提供します。

## 1.1. コマンド

```bash
# ===== 全工程一括実行 =====
python main.py                  # 録画 → サムネイル生成 → YouTube アップロード (並行)
python main.py --record-only    # 録画のみ (アップロードしない)
python main.py --upload-only    # 未アップロードの録画済み試合をまとめてアップロード
python main.py --max 3          # 最大3試合まで処理
python main.py --cleanup        # アップロード済み古いファイルを削除 (config: cleanup_after_days)
python main.py --cleanup --dry-run  # 削除対象の表示のみ (実際には削除しない)

# ===== 個別実行 =====
# リプレイ全体を録画（OBS使用）
python record_matches.py

# サムネイル一括生成（output/videos/ 内の全試合）
python create_thumbnails.py

# 特定のゲームIDのサムネイルを生成
python create_thumbnails.py 567080016 567080017

# 既存サムネイルを強制再生成
python create_thumbnails.py --force

# 最新録画をYouTubeにアップロード
python upload_video.py

# 指定game_idをアップロード
python upload_video.py 567287391

# 試合リストを再構築して表示
python match_list.py

# 試合リストを表示のみ（再構築なし）
python match_list.py --show

# アップロード済み古いファイルを削除 (直接実行)
python cleanup.py
python cleanup.py --days 14     # 14日以上経過したものを削除
python cleanup.py --dry-run     # 削除対象の表示のみ
```

## 1.2. アーキテクチャ

パイプラインは `record_matches.py` → `create_thumbnails.py` → `upload_video.py` の3ステップ構成 (録画とアップロードは並行実行):

1. **試合発見** — `src/lol/replay_downloader.py` の `find_downloadable_matches` がChallenger上位100人を取得し、`PlayerTracker` でロール判定（キャッシュ活用）。**JUNGLEロールの選手**が24時間以内に勝利した試合を収集。条件: 現パッチ・ランクソロ(queue=420)。候補一覧は `cache/match_candidates.json` に試合日時順で保存。

2. **プレイヤーロールキャッシュ** — `src/lol/player_tracker.py` が `cache/player_roles.json` に選手ごとのロールを保存。直近10試合の `teamPosition` 最頻値がロール。7日間有効、新規・期限切れのみ再判定。

3. **リプレイダウンロード** — `src/lol/replay_downloader.py` がLCU APIで `.rofl` ファイルをダウンロード。現パッチのリプレイのみ取得可能。

4. **録画** — **OBSのみ使用** (`record_matches.py`)。OBS WebSocketでゲーム開始後〜終了まで録画（ローディング画面は録画しない）。AHKスクリプトでカメラをジャングラーに固定。MKV→MP4リマックス後、**YouTubeタイトルと同じファイル名にリネーム** (例: `[JP Challenger JG] Evelynn 7-2-3 - senzawa#JP1.mp4`)。メタデータは `{game_id}.json` のまま。録画完了時に `output/match_list.csv` へ自動登録 (`video_filename` カラムにリネーム後の名前を記録)。

5. **サムネイル生成** — `create_thumbnails.py` が `output/videos/{game_id}.json` のメタデータから1280×720 JPEGを生成。`output/thumbnails/{game_id}.jpg`。

6. **YouTube投稿** — `upload_video.py` / `src/youtube/uploader.py` がYouTube Data API v3 + OAuth2で動画をアップロード。`youtube_url` が登録済みの場合は重複アップロードをスキップ。アップロード完了後に `output/match_list.csv` へYouTube URLを記録。

7. **試合リスト** — `output/match_list.csv` に全試合を記録。`match_list.py` で表示・再構築。

8. **ファイル削除** — `cleanup.py` がアップロード済み + N日以上経過した試合の mp4/json/サムネイルを削除。`python main.py --cleanup` または `python cleanup.py` で実行。

## 1.3. 重要ファイル

| ファイル | 役割 |
|---------|------|
| `config.yaml` | 全設定: APIキー、パス、録画パラメータ、クリーンアップ設定 |
| `output/videos/{タイトル}.mp4` | 録画済み動画 (YouTubeタイトルと同じファイル名) |
| `output/videos/{game_id}.json` | 試合メタデータ (サムネイル・アップロードに使用) |
| `output/match_list.csv` | 全録画試合の一覧 (champion/KDA/patch/video_filename/YouTube URL等) |
| `cache/match_candidates.json` | 直近の録画候補リスト (試合日時昇順) |
| `cache/player_roles.json` | プレイヤーロールキャッシュ (7日間有効) |
| `src/db/match_registry.py` | match_list.csv の読み書きモジュール |
| `src/lol/player_tracker.py` | プレイヤーロール判定・キャッシュ管理 |
| `cleanup.py` | アップロード済み動画の自動削除スクリプト |
| `credentials/youtube_client_secret.json` | YouTube OAuth2クライアントシークレット (要配置) |
| `credentials/youtube_token.json` | YouTube OAuth2トークン (初回認証後に自動生成) |
| `press_key2.ahk` | AHK: ブルーチームジャングラーにカメラ固定 (キー「2」×2) |
| `press_keyw.ahk` | AHK: レッドチームジャングラーにカメラ固定 (キー「W」×2) |

### match_list.csv のカラム構成

| カラム | 内容 |
|-------|------|
| `game_id` | ゲームID |
| `match_id` | マッチID (JP1_xxx) |
| `recorded_at` | 録画日時 |
| `champion` 〜 `gold` | チャンピオン・KDA・スタッツ |
| `patch` | パッチ番号 |
| `game_date` | 試合日時 (JST) |
| `video_mb` | 動画サイズ (MB) |
| `video_filename` | リネーム後のmp4ファイル名 (YouTubeタイトルベース) |
| `youtube_url` | アップロード済みYouTube URL |

## 1.4. メタデータJSON フィールド (output/videos/{game_id}.json)

| フィールド | 内容 |
|-----------|------|
| `game_id` | ゲームID (数値) |
| `match_id` | マッチID (例: JP1_567214221) |
| `title` | YouTube タイトル文字列 |
| `champion` | チャンピオン名 |
| `player` | プレイヤー名 (GameName#Tag) |
| `rank` | ランク文字列 (例: Challenger 1500 LP) |
| `kills` / `deaths` / `assists` | KDA |
| `cs` / `vision` / `gold` | スタッツ |
| `items` | アイテムID 7個 (item0-6) |
| `summoner_spells` | サモナースペルID 2個 |
| `rune_keystone` / `rune_primary_style` / `rune_secondary_style` | ルーン |
| `game_version` | パッチ番号 (例: `16.4`) |
| `game_start_ms` | 試合開始時刻 (Unix ms) |

## 1.5. 重要な制約事項

**カメラ操作**: AHKが唯一の確実な方法。`SendInput`/VKコードはDirectXゲームでは効かない。`cameraMode=focus/tps` は**ゲームクラッシュ**するため使用禁止。

**音声録音**: pyaudiowpatch WASAPIループバックのみ動作。`soundcard` ライブラリはexit=127でクラッシュするため使用不可。

**LCU認証**: 起動時に `C:/Riot Games/League of Legends/lockfile` からポート番号とトークンを読み取る。

**Replay API**: `C:/Riot Games/League of Legends/Config/game.cfg` の `[General]` セクションに `EnableReplayApi=1` が必要。

**Replay API フレーム上限**: 約26,000フレームで自動停止。RECORD_FPS=15 で約29分まで録画可能。30分超のゲームは RECORD_FPS=10 が必要。

## 1.6. サムネイルレイアウト (create_thumbnails.py)

```
┌──────────────────────────┬──────────────────────────────┐
│ 左パネル (580px)          │ 右パネル (700px)              │
│                          │ ローディング画面アート          │
│ Row1: チャンピオンアイコン │ (左パネル背景にもぼかして使用) │
│        + ジャングルアイコン│                              │
│ Row2: プレイヤー名帯       │                              │
│        + ランクエンブレム  │                              │
│ Row3: K / D / A サークル  │                              │
│ Row4: アイテム2×3 + トリン│                              │
│        + スペル2個         │                              │
│        + キーストーンルーン│                              │
│ 下部: Patch XX.X (巨大)   │                              │
│       試合日時 (小・下端)  │                              │
└──────────────────────────┴──────────────────────────────┘
```

フォント:
- プレイヤー名: `meiryob.ttc` (メイリオ Bold) — 日本語対応
- KDA数字: `impact.ttf`
- パッチバージョン: `impact.ttf` 最大130px (巨大・中央・ゴールド)
- 日時: `arial.ttf` 18px (最下端・グレー)

ローカルアセット優先:
- `assets/emblems/{Tier}.png` — ランクエンブレム (Challenger / Diamond / Emerald / Grandmaster / Master)
  - 参照: https://wiki.leagueoflegends.com/en-us/Rank
- `assets/icons/Jungle_icon.png` — ジャングルポジションアイコン
  - 参照: https://wiki.leagueoflegends.com/en-us/Category:Role_icons

## 1.7. config.yaml 設定項目

```yaml
crawler:
  top_n_players: 100          # Challenger上位何人を対象にするか
  role_refresh_days: 7        # プレイヤーロールキャッシュ有効期間 (日)

cleanup:
  cleanup_after_days: 30      # アップロード済み動画を何日後に削除するか
```

## 1.8. 動画ファイル名の仕様

- 録画完了後、`{game_id}.mp4` → `safe_filename(title).mp4` にリネーム
- `safe_filename()` の変換ルール:
  - `/` → `-` (KDAの区切り文字)
  - Windows禁止文字 `\ : * ? " < > |` を除去
  - 200文字以内に切り詰め
- 例: `[JP Challenger JG] Evelynn 7-2-3 - senzawa#JP1.mp4`
- `{game_id}.json` と `{game_id}.jpg` はリネームしない (game_idで管理)
- `video_filename` カラムが空の古いレコードは `{game_id}.mp4` にフォールバック

## 1.9. Python バージョン注意

一部スクリプトはPython 3.8を使用。`list[dict]` 等のモダンな型ヒント構文を使う場合は必ずファイル先頭に `from __future__ import annotations` を記述する。
