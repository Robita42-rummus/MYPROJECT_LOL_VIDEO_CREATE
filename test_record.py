"""
録画スクリプト
- 映像: Replay API (/replay/recording) でゲーム内蔵録画
- 音声: pyaudiowpatch WASAPI ループバック
- カメラ: AHK でキー「2」を2回押して Rek'Sai 追従
- 自動終了: 録画完了後にLoLゲームを終了
使い方: python test_record.py
"""
from __future__ import annotations
import ctypes
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pyaudiowpatch as pyaudio
import requests
import urllib3
import yaml
from loguru import logger
from src.lol.lcu_client import LCUClient

urllib3.disable_warnings()
logger.remove()
logger.add(sys.stdout, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

# -----------------------------------------------
# 設定
# -----------------------------------------------
GAME_ID        = 566837282       # JP1-566837282
SEEK_TIME      = 120.0           # 録画開始シーク位置 (秒)
RECORD_SECONDS = 20
OUTPUT_PATH    = Path("output/test_clip.mp4")
TMP_VIDEO      = Path("output/tmp_video.mp4")
TMP_AUDIO      = Path("output/tmp_audio.wav")
LIVECLIENT_URL = "https://127.0.0.1:2999/liveclientdata/gamestats"
LOL_WINDOW     = "League of Legends (TM) Client"
CHUNK          = 512


# -----------------------------------------------
# LoL 状態管理
# -----------------------------------------------

def get_game_time() -> float | None:
    try:
        resp = requests.get(LIVECLIENT_URL, timeout=3, verify=False)
        if resp.status_code == 200:
            return resp.json().get("gameTime", None)
    except Exception:
        pass
    return None


def wait_for_game_start(timeout: int = 300) -> bool:
    logger.info("ゲーム起動待機中... (最大5分)")
    for i in range(timeout):
        t = get_game_time()
        if t is not None:
            logger.info(f"ゲーム起動確認! ゲーム内時刻: {t:.1f}秒")
            return True
        if i % 10 == 9:
            logger.info(f"  待機中... {i+1}秒経過")
        time.sleep(1)
    return False


def kill_lol_game():
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", "League of Legends.exe"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            logger.info("LoLゲームクライアント終了完了")
        else:
            logger.warning(f"taskkill RC={result.returncode}")
    except Exception as e:
        logger.warning(f"ゲーム終了失敗: {e}")


# -----------------------------------------------
# ウィンドウ位置検出
# -----------------------------------------------

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def get_lol_window_rect() -> tuple[int, int, int, int] | None:
    """LoLウィンドウの (x, y, width, height) を返す"""
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, LOL_WINDOW)
    if not hwnd:
        return None
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    x = rect.left
    y = rect.top
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    # 偶数に丸める (libx264 の要件)
    w = w - (w % 2)
    h = h - (h % 2)
    return (x, y, w, h)


# -----------------------------------------------
# リプレイ視点設定 (Replay API)
# -----------------------------------------------

REPLAY_BASE = "https://127.0.0.1:2999"


# SendInput 用構造体

AHK_SCRIPT = Path(__file__).parent / "press_key2.ahk"


def _press_key2_via_ahk():
    """AHK でキー「2」を2回押す (LoL をアクティブ化してから)"""
    try:
        result = subprocess.run(
            ["AutoHotkey.exe", str(AHK_SCRIPT)],
            timeout=10, capture_output=True
        )
        if result.returncode != 0:
            logger.warning(f"AHK 実行失敗 RC={result.returncode}")
    except FileNotFoundError:
        # フルパスで再試行
        for ahk_path in [
            r"C:\Program Files\AutoHotkey\AutoHotkey.exe",
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
            r"C:\Program Files (x86)\AutoHotkey\AutoHotkey.exe",
        ]:
            if Path(ahk_path).exists():
                subprocess.run([ahk_path, str(AHK_SCRIPT)], timeout=10, capture_output=True)
                return
        logger.warning("AutoHotkey.exe が見つからない")
    except Exception as e:
        logger.warning(f"AHK エラー: {e}")


def _camera_follow_loop(stop_event: threading.Event, interval: float = 5.0):
    """録画中、定期的にAHKでキー2を押してカメラをRek'Saiに追従させる"""
    while not stop_event.is_set():
        _press_key2_via_ahk()
        stop_event.wait(interval)


def setup_replay_view() -> bool:
    """初回カメラ設定: AHK でキー「2」を2回押す"""
    logger.info("カメラ設定: AHK でキー「2」を2回押す...")
    _press_key2_via_ahk()
    time.sleep(0.5)
    return True


# -----------------------------------------------
# 映像録画 (Replay API 内蔵録画)
# -----------------------------------------------

def get_playback_time() -> float | None:
    """Replay APIから現在の再生時刻を取得 (liveclientdata の代替)"""
    try:
        resp = requests.get(f"{REPLAY_BASE}/replay/playback", timeout=3, verify=False)
        if resp.status_code == 200:
            return resp.json().get("time", None)
    except Exception:
        pass
    return None


def record_video(output_path: Path, duration: int) -> bool:
    """Replay API の /replay/recording でゲーム映像のみを録画する (webm → mp4変換)"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    webm_path = output_path.with_suffix(".webm")

    # /replay/playback の time を使う (liveclientdata は起動直後に不安定)
    t = None
    for _ in range(5):
        t = get_playback_time()
        if t is not None:
            break
        time.sleep(1)
    if t is None:
        logger.error("ゲーム時刻取得失敗 (5回リトライ後)")
        return False
    logger.info(f"録画開始時刻: {t:.1f}s")

    # Windows パス (バックスラッシュ)
    abs_webm = str(webm_path.resolve())

    payload = {
        "recording": True,
        "codec": "webm",
        "path": abs_webm,
        "startTime": t,
        "endTime": t + duration,
        "framesPerSecond": 30,
    }
    r = requests.post(f"{REPLAY_BASE}/replay/recording", json=payload, timeout=5, verify=False)
    if r.status_code != 200:
        logger.error(f"録画開始失敗: {r.status_code} {r.text[:200]}")
        return False

    logger.info(f"Replay API 録画開始: {t:.1f}s ~ {t+duration:.1f}s → {webm_path.name}")

    # 録画完了待機
    for _ in range(duration + 60):
        time.sleep(1)
        try:
            status = requests.get(f"{REPLAY_BASE}/replay/recording", timeout=5, verify=False).json()
            if not status.get("recording", True):
                logger.info("録画完了 (recording=false)")
                break
        except Exception:
            pass

    if not webm_path.exists() or webm_path.stat().st_size == 0:
        logger.error(f"webmファイルが見つからない: {webm_path}")
        return False

    logger.info(f"webm サイズ: {webm_path.stat().st_size // 1024}KB → mp4変換中...")

    # webm → mp4 (映像のみ、音声は後でmerge)
    cmd = [
        "ffmpeg", "-y", "-i", str(webm_path),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-an", str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    webm_path.unlink(missing_ok=True)

    if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        logger.info(f"映像変換完了: {output_path.stat().st_size // 1024}KB")
        return True
    else:
        logger.error(f"mp4変換失敗 RC={result.returncode}: {result.stderr[-300:]}")
        return False


# -----------------------------------------------
# 音声録音 (pyaudiowpatch WASAPI loopback)
# -----------------------------------------------

def record_audio(output_path: Path, duration: float):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        pa = pyaudio.PyAudio()
        wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_out_idx = wasapi_info["defaultOutputDevice"]
        default_out = pa.get_device_info_by_index(default_out_idx)

        # ループバックデバイスを探す
        loopback = None
        for i in range(pa.get_device_count()):
            dev = pa.get_device_info_by_index(i)
            if dev.get("isLoopbackDevice", False) and default_out["name"] in dev["name"]:
                loopback = dev
                loopback["_idx"] = i
                break

        if not loopback:
            logger.error("WASAPIループバックデバイスが見つからない")
            pa.terminate()
            return

        samplerate = int(loopback["defaultSampleRate"])
        channels = loopback["maxInputChannels"]
        logger.info(f"音声デバイス: {loopback['name']} ({samplerate}Hz, {channels}ch)")

        frames = []
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=samplerate,
            input=True,
            input_device_index=loopback["_idx"],
            frames_per_buffer=CHUNK,
        )

        logger.info(f"音声録音開始: {duration}秒")
        n_chunks = int(samplerate / CHUNK * duration)
        for _ in range(n_chunks):
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)

        stream.stop_stream()
        stream.close()
        pa.terminate()

        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(samplerate)
            wf.writeframes(b"".join(frames))

        logger.info(f"音声録音完了: {output_path.name} ({output_path.stat().st_size // 1024}KB)")

    except Exception as e:
        logger.error(f"音声録音エラー: {type(e).__name__}: {e}")


# -----------------------------------------------
# 音声+映像合成
# -----------------------------------------------

def merge_audio_video(video: Path, audio: Path, output: Path) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-i", str(audio),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(output)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and output.exists():
            logger.info(f"合成完了: {output.name} ({output.stat().st_size // 1024}KB)")
            return True
        else:
            logger.error(f"合成失敗: {result.stderr[-300:]}")
            return False
    except Exception as e:
        logger.error(f"合成エラー: {e}")
        return False


# -----------------------------------------------
# メイン
# -----------------------------------------------

def main():
    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    lcu = LCUClient(config["lol"]["install_path"])

    # Step 1: リプレイ起動
    if get_game_time() is not None:
        t = get_game_time()
        logger.info(f"リプレイ既に再生中 (ゲーム内時刻: {t:.1f}秒)")
    else:
        logger.info(f"リプレイ起動: JP1-{GAME_ID}")
        if not lcu.launch_replay(GAME_ID):
            logger.error("リプレイ起動失敗")
            sys.exit(1)
        if not wait_for_game_start(timeout=300):
            logger.error("ゲーム起動タイムアウト")
            sys.exit(1)

    # ゲーム中盤にシーク → Rek'Sai がジャングルにいる時間帯
    logger.info(f"t={SEEK_TIME:.0f}秒にシーク中...")
    try:
        requests.post(f"{REPLAY_BASE}/replay/playback",
                      json={"paused": True, "time": SEEK_TIME},
                      timeout=5, verify=False)
        time.sleep(2)
        requests.post(f"{REPLAY_BASE}/replay/playback",
                      json={"paused": False, "speed": 1.0},
                      timeout=5, verify=False)
        time.sleep(1)
    except Exception as e:
        logger.warning(f"シーク失敗: {e}")

    t = get_playback_time() or get_game_time()
    logger.info(f"録画開始時点のゲーム内時刻: {t:.1f}秒" if t else "ゲーム時刻不明")

    # Step 2: 視点設定 (キー操作で Rek'Sai 追従)
    setup_replay_view()
    time.sleep(1)

    # Step 3: 音声・映像・カメラフォローを同時実行
    cam_stop = threading.Event()
    cam_thread = threading.Thread(
        target=_camera_follow_loop, args=(cam_stop, 5.0), daemon=True
    )
    cam_thread.start()
    logger.info("カメラフォローループ開始 (5秒ごとにキー再押下)")

    audio_thread = threading.Thread(
        target=record_audio, args=(TMP_AUDIO, RECORD_SECONDS), daemon=True
    )
    audio_thread.start()
    video_ok = record_video(TMP_VIDEO, RECORD_SECONDS)

    cam_stop.set()
    audio_thread.join(timeout=RECORD_SECONDS + 10)

    # Step 4: LoLゲーム自動終了
    logger.info("LoLゲームクライアントを終了します...")
    kill_lol_game()

    # Step 5: 合成
    if video_ok and TMP_AUDIO.exists() and TMP_AUDIO.stat().st_size > 1000:
        success = merge_audio_video(TMP_VIDEO, TMP_AUDIO, OUTPUT_PATH)
    elif video_ok:
        logger.warning("音声なし → 映像のみを出力")
        import shutil
        shutil.copy(TMP_VIDEO, OUTPUT_PATH)
        success = True
    else:
        success = False

    if success:
        logger.info("=" * 40)
        logger.info(f"成功! {OUTPUT_PATH.resolve()}")
        logger.info(f"サイズ: {OUTPUT_PATH.stat().st_size // 1024}KB")
        logger.info("=" * 40)
    else:
        logger.error("録画失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
