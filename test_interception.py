"""
Interceptionドライバ経由でキー「2」を注入するテスト。
LOLをアクティブにしたまま実行してください。
"""
import sys
import time
from interception.interception import ffi, lib

# スキャンコード
SCAN_2 = 0x03
SCAN_W = 0x11

def send_key(scan_code: int, device: int = 1):
    ctx = lib.interception_create_context()
    try:
        stroke = ffi.new("InterceptionKeyStroke *")
        stroke.code = scan_code
        stroke.information = 0

        stroke.state = 0  # KEY_DOWN
        lib.interception_send(ctx, device, ffi.cast("InterceptionStroke *", stroke), 1)
        time.sleep(0.05)

        stroke.state = 1  # KEY_UP
        lib.interception_send(ctx, device, ffi.cast("InterceptionStroke *", stroke), 1)
    finally:
        lib.interception_destroy_context(ctx)

if __name__ == "__main__":
    scan = SCAN_W if (len(sys.argv) > 1 and sys.argv[1] == "w") else SCAN_2
    key_name = "W" if scan == SCAN_W else "2"

    print(f"3秒後にキー「{key_name}」を2回注入します...")
    time.sleep(3)

    print(f"1回目")
    send_key(scan)
    time.sleep(0.3)

    print(f"2回目")
    send_key(scan)

    print("完了")
