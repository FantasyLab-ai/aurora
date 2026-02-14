# /mnt/c/Users/bgrut/Desktop/FantasyAI/scripts/cdp_doctor.py
import os, sys, socket, json, urllib.request

host = os.getenv("CHROME_CDP_HOST", "127.0.0.1")
port = int(os.getenv("CHROME_CDP_PORT", "9222"))
base = f"http://{host}:{port}"

print(f"[doctor] trying TCP connect to {host}:{port} …", end=" ")
sock = socket.socket()
sock.settimeout(2.0)
try:
    sock.connect((host, port))
    print("OK")
except Exception as e:
    print("FAIL", e)
    sys.exit(1)
finally:
    try: sock.close()
    except: pass

print(f"[doctor] GET {base}/json/version …")
try:
    with urllib.request.urlopen(base + "/json/version", timeout=2) as r:
        body = r.read().decode("utf-8", "ignore")
    print("[doctor] OK, Browser =", json.loads(body).get("Browser"))
except Exception as e:
    print("[doctor] FAIL", e)
    sys.exit(2)
