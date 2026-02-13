"""判定APIのテストスクリプト"""
import json
import http.client
from PIL import Image
import numpy as np

# テスト用暖色画像を生成
arr = np.random.randint(150, 255, (200, 300, 3), dtype="uint8")
arr[:, :, 1] = arr[:, :, 1] // 3
arr[:, :, 2] = arr[:, :, 2] // 4
Image.fromarray(arr).save("test_warm.png")

# テスト用寒色画像を生成
arr2 = np.random.randint(50, 150, (200, 300, 3), dtype="uint8")
arr2[:, :, 0] = arr2[:, :, 0] // 4
arr2[:, :, 1] = arr2[:, :, 1] // 3
Image.fromarray(arr2).save("test_cool.png")

boundary = "----TestBoundary12345"

def judge_file(filename):
    with open(filename, "rb") as f:
        img_data = f.read()

    body = b""
    body += ("--" + boundary + "\r\n").encode()
    body += ('Content-Disposition: form-data; name="image"; filename="' + filename + '"\r\n').encode()
    body += b"Content-Type: image/png\r\n\r\n"
    body += img_data
    body += ("\r\n--" + boundary + "--\r\n").encode()

    conn = http.client.HTTPConnection("localhost", 5000)
    conn.request("POST", "/api/judge", body=body, headers={
        "Content-Type": "multipart/form-data; boundary=" + boundary
    })
    res = conn.getresponse()
    return json.loads(res.read())

# テスト実行
print("=== JUDGE (暖色画像 → OKが期待される) ===")
r1 = judge_file("test_warm.png")
print(json.dumps(r1, ensure_ascii=False, indent=2))

print("\n=== JUDGE (寒色画像 → NOが期待される) ===")
r2 = judge_file("test_cool.png")
print(json.dumps(r2, ensure_ascii=False, indent=2))

# 結果検証
ok1 = r1.get("success") and r1.get("result", {}).get("verdict") == "OK"
ok2 = r2.get("success") and r2.get("result", {}).get("verdict") == "NO"
print(f"\n✅ 暖色→OK: {'PASS' if ok1 else 'FAIL'}")
print(f"✅ 寒色→NO: {'PASS' if ok2 else 'FAIL'}")
