# LOL JP Server Highlight Auto Generator

JPサーバーのチャレンジャー試合を自動検出し、リプレイからハイライト動画を生成してYouTubeにアップロードする。

## 全体フロー

```
[Riot API] チャレンジャー/GMの試合を監視
    ↓ 試合開始を検知
[LOLクライアント] スペクテイターモードで観戦
    ↓ 試合終了後、.roflが自動保存
[Riot Match Timeline API] ハイライト時刻を特定
    (ペンタキル, バロン, チームファイト等)
    ↓
[LOL Replay API] リプレイを再生し内蔵録画機能でクリップ録画
    (127.0.0.1:2999 - OBS不要)
    ↓
[FFmpeg] クリップ結合 + テキストオーバーレイ + イントロカード
    ↓
[YouTube API] 自動アップロード
```

## セットアップ

```bash
# セットアップガイドを表示
python main.py --setup
```

## 必要環境

- Windows 10/11
- Python 3.11+
- League of Legends クライアント (ログイン済み)
- FFmpeg (PATHに追加)
- Riot API キー (https://developer.riotgames.com/)
- YouTube API 認証情報

## インストール

```bash
pip install -r requirements.txt
```

## 設定

```bash
cp config.yaml.example config.yaml
# config.yaml を編集してAPIキーなどを設定
```

## 実行

```bash
python main.py           # 通常実行 (無限ループ)
python main.py --once    # 1試合のみ処理
python main.py --game-id JP1_1234567890  # 特定試合を処理
```

## ファイル構成

```
src/
  riot/
    api_client.py       - Riot API (試合データ・タイムライン取得)
    spectator.py        - ライブゲーム監視・クロール
  lol/
    lcu_client.py       - LCU API (LoLクライアント操作)
    replay_api.py       - Replay API (録画制御)
  analysis/
    highlight_detector.py - タイムラインからハイライト検出
  video/
    video_composer.py   - FFmpegによる動画合成
  youtube/
    uploader.py         - YouTube自動アップロード
main.py                 - メインオーケストレーター
```

## 注意事項

- Riot API 開発用キーは1日100リクエスト制限あり。本格運用にはProduction Key申請が必要
- LoLクライアントは起動・ログイン済み状態にしておくこと
- スペクテイター中はクライアントを手動操作しないこと
- YouTube 1チャンネルの1日アップロード上限: 通常6本/日
