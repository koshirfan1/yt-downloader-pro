#!/usr/bin/env python3
"""
YT Downloader Pro  -  yt-dlp based desktop app (Tkinter)

Features
  * Full HD / 4K / 720p ... / MP3 quality selection
  * Channel ke naam ke folder mein auto-save   (Downloads/<Channel>/video.mp4)
  * Playlist ka alag folder (channel ke andar), numbering ke saath
  * Download queue (multiple URLs), progress, speed, ETA, cancel
  * Poori History (SQLite): search, open file/folder, re-download, delete
  * Duplicate skip (pehle download ki hui video dobara download nahi hogi)
  * Subtitles, thumbnail, metadata, speed limit, browser cookies option

Setup
  pip install -U yt-dlp
  ffmpeg install karein (merge ke liye):  winget install ffmpeg | brew install ffmpeg | sudo apt install ffmpeg
Run
  python yt_downloader_pro.py
"""

import datetime
import hmac
import json
import os
import queue
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from yt_dlp import YoutubeDL
except ImportError:
    sys.exit("yt-dlp install nahi hai. Chalayein: pip install -U yt-dlp")

try:
    from yt_dlp.utils import DownloadCancelled as _Base
except ImportError:  # purane versions
    _Base = Exception

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:  # core logic phir bhi import ho sakta hai
    tk = None

APP_DIR = Path.home() / ".yt_downloader_pro"
APP_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = APP_DIR / "settings.json"
HISTORY_DB = APP_DIR / "history.db"
ARCHIVE_FILE = APP_DIR / "archive.txt"

QUALITIES = {
    "Best available": None,
    "4K (2160p)": 2160,
    "1440p": 1440,
    "Full HD (1080p)": 1080,
    "HD (720p)": 720,
    "480p": 480,
    "360p": 360,
    "Audio only (MP3)": "audio",
}

DEFAULTS = {
    "folder": str(Path.home() / "Downloads" / "YT Downloads"),
    "quality": "Full HD (1080p)",
    "channel_folder": True,
    "playlist_folder": True,
    "playlist": False,
    "skip_dupes": True,
    "subtitles": False,
    "thumbnail": False,
    "limit_mb": 0,
    "cookies": "None",
    "cookies_file": "",
    "auto_retry": True,
    "last_fallback": "",
    "api_token": "",
}

# Bot-check aane par app ye tareeqe khud ek ek karke try karta hai
AUTO_FALLBACKS = [
    ("alternate client", {"extractor_args": {"youtube": {"player_client": ["tv", "web_safari"]}}}),
    ("Firefox cookies", {"cookiesfrombrowser": ("firefox",)}),
    ("Edge cookies", {"cookiesfrombrowser": ("edge",)}),
    ("Chrome cookies", {"cookiesfrombrowser": ("chrome",)}),
    ("Brave cookies", {"cookiesfrombrowser": ("brave",)}),
]


class Cancelled(_Base):
    pass


class QuietLogger:
    """yt-dlp ke messages console par nahi, last error yaad rakhta hai (GUI mein dikhane ke liye)."""

    def __init__(self):
        self.last_error = ""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        self.last_error = str(msg).replace("ERROR: ", "")


def cookie_opts(o):
    """cookies.txt file ko browser cookies par tarjeeh milti hai."""
    f = (o.get("cookies_file") or "").strip()
    if f and os.path.isfile(f):
        return {"cookiefile": f}
    if o.get("cookies") and o["cookies"] != "None":
        return {"cookiesfrombrowser": (o["cookies"],)}
    return {}


ERROR_LOG = APP_DIR / "error.log"
RUNTIME_BINARIES = {"deno": "deno", "node": "node", "bun": "bun", "quickjs": "qjs"}


def env_report():
    import importlib.util
    try:
        from yt_dlp.version import __version__ as ver
    except Exception:
        ver = "?"
    runtimes = {n: shutil.which(b) for n, b in RUNTIME_BINARIES.items() if shutil.which(b)}
    return {
        "version": ver,
        "runtimes": runtimes,
        "ejs": importlib.util.find_spec("yt_dlp_ejs") is not None,
        "ffmpeg": bool(shutil.which("ffmpeg")),
    }


ENV = env_report()


def log_error(text):
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


def check_cookie_file(path):
    """cookies.txt ki jaanch. Returns list of problems (khali = theek lagti hai)."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return [f"file read nahi hui: {e}"]
    problems = []
    if not lines or not ("Netscape" in lines[0] or "HTTP Cookie File" in lines[0]):
        problems.append("pehli line '# Netscape HTTP Cookie File' nahi hai (galat format - 'Get cookies.txt LOCALLY' se export karein)")
    rows = [l for l in lines if l.strip() and (not l.startswith("#") or l.startswith("#HttpOnly_"))]
    yt = [l.split("\t") for l in rows if "youtube.com" in l]
    if not yt:
        problems.append("is file mein youtube.com ki cookies hain hi nahi (galat site se export hui)")
    else:
        names = {c[5] for c in yt if len(c) >= 7}
        if not names & {"SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO"}:
            problems.append("login wali cookies nahi hain - export logout halat mein hua. Pehle YouTube par login karein, phir export")
    return problems


def hints_for(text):
    t = (text or "").lower()
    hints = []
    if any(k in t for k in ("n challenge", "signature solving", "js runtime", "ejs", "only images are available",
                            "requested format is not available", "challenge solving")):
        hints.append("Ye JavaScript runtime/EJS ka masla lagta hai: Deno install karein aur "
                     "pip install -U \"yt-dlp[default]\" chalayein.")
    if "sign in" in t or "bot" in t:
        hints.append("YouTube login/bot check maang raha hai: cookies fresh export karein (Private window wala tareeqa).")
    if "429" in t or "too many requests" in t:
        hints.append("Bohat zyada requests: thori der (kuch ghante) ruk kar try karein, VPN band rakhein.")
    if "403" in t:
        hints.append("HTTP 403: yt-dlp update karein aur cookies refresh karein.")
    if "private video" in t or "members-only" in t or "age" in t:
        hints.append("Video private/members/age-restricted ho sakti hai: sahi login wali cookies chahiye.")
    if "unavailable" in t:
        hints.append("Video unavailable hai (hati hui ya aap ke region mein blocked).")
    return hints


def diagnose_text(settings, last_error=""):
    env = env_report()  # taaza (install ke baad bhi sahi dikhaye)
    L = ["=== Environment ===",
         f"yt-dlp version : {env['version']}",
         f"ffmpeg         : {'OK' if env['ffmpeg'] else 'NAHI MILA'}",
         f"JS runtime     : {', '.join(env['runtimes']) if env['runtimes'] else 'NAHI MILA (Deno chahiye)'}",
         f"yt-dlp-ejs     : {'OK' if env['ejs'] else 'install nahi (app GitHub se khud lene ki koshish karti hai)'}",
         "", "=== Cookies ==="]
    cf = (settings.get("cookies_file") or "").strip()
    if cf:
        if not os.path.isfile(cf):
            L.append(f"cookies.txt file mili nahi: {cf}")
        else:
            probs = check_cookie_file(cf)
            L.append(f"cookies.txt: {cf}")
            L += [f"  - MASLA: {x}" for x in probs] or ["  - file theek lagti hai (format aur login cookies maujood)"]
    elif settings.get("cookies", "None") != "None":
        L.append(f"Browser cookies: {settings['cookies']}  (browser band karke try karein; Chrome/Edge aksar fail hote hain)")
    else:
        L.append("Koi cookies set nahi (sirf auto-retry chal raha hai)")

    L += ["", "=== Kya karna hai ==="]
    todo = []
    if not env["runtimes"]:
        todo.append("1) Deno install karein:  winget install DenoLand.Deno   (phir PowerShell/CMD band karke NAYA kholein, app dobara chalayein)")
    if not env["ejs"]:
        todo.append('2) Chalayein:  pip install -U "yt-dlp[default]"')
    todo.append("3) Chalayein:  pip install -U yt-dlp")
    todo.append("4) cookies.txt dobara export karein (Private window mein login -> youtube.com/robots.txt -> Export)")
    L += todo
    if last_error:
        L += ["", "=== Aakhri error ===", last_error]
        for h in hints_for(last_error):
            L.append(f"-> {h}")
    L += ["", f"(error log file: {ERROR_LOG})"]
    return "\n".join(L)


def is_bot_error(text):
    t = (text or "").lower()
    return "confirm you" in t and "bot" in t or "--cookies" in t


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def load_settings():
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


def save_settings(data):
    try:
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024


def open_path(path):
    path = str(path)
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def build_outtmpl(o, is_playlist=False):
    """Folder structure: <Channel>/<Playlist>/<NN - >Title [id].ext"""
    tmpl = ""
    if o["channel_folder"]:
        tmpl += "%(channel,uploader|Unknown Channel)s/"
    if o["playlist_folder"] and is_playlist:
        tmpl += "%(playlist_title|Playlist)s/"
        tmpl += "%(playlist_index)s - "
    tmpl += "%(title).150s [%(id)s].%(ext)s"
    return tmpl


def detect_playlist(url, o):
    """Quick metadata-only check: kya ye URL playlist/channel hai?"""
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                        "noplaylist": not o["playlist"], "skip_download": True,
                        "logger": QuietLogger(), **cookie_opts(o)}) as y:
            info = y.extract_info(url, download=False)
        return bool(info) and info.get("_type") == "playlist"
    except Exception:
        return False


def build_format(quality_label, has_ffmpeg):
    q = QUALITIES.get(quality_label)
    if q == "audio":
        return "ba/b"
    h = f"[height<={q}]" if q else ""
    if not has_ffmpeg:  # merge nahi ho sakta, single file format
        return f"b{h}/b"
    # mp4/m4a ko tarjeeh (har player par chalta hai), warna best
    return f"bv*{h}[ext=mp4]+ba[ext=m4a]/bv*{h}+ba/b{h}/b"


def build_ydl_opts(o, hook, has_ffmpeg, is_playlist=False, logger=None):
    q = QUALITIES.get(o["quality"])
    opts = {
        "paths": {"home": o["folder"]},
        "outtmpl": build_outtmpl(o, is_playlist),
        "format": build_format(o["quality"], has_ffmpeg),
        "noplaylist": not o["playlist"],
        "ignoreerrors": "only_download",
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "continuedl": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "windowsfilenames": True,
    }
    pps = []
    if q == "audio":
        pps.append({"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"})
    elif has_ffmpeg:
        opts["merge_output_format"] = "mp4"
    if has_ffmpeg:
        pps.append({"key": "FFmpegMetadata"})
    if pps:
        opts["postprocessors"] = pps
    if o["skip_dupes"]:
        opts["download_archive"] = str(ARCHIVE_FILE)
    if o["subtitles"]:
        opts.update({"writesubtitles": True, "writeautomaticsub": True,
                     "subtitleslangs": ["en", "ur", "hi"], "subtitlesformat": "srt/vtt/best"})
    if o["thumbnail"]:
        opts["writethumbnail"] = True
    if float(o["limit_mb"] or 0) > 0:
        opts["ratelimit"] = int(float(o["limit_mb"]) * 1024 * 1024)
    opts.update(cookie_opts(o))
    if ENV["runtimes"]:
        opts["js_runtimes"] = {name: {} for name in ENV["runtimes"]}
    if not ENV["ejs"]:
        opts["remote_components"] = ["ejs:github"]
    if logger is not None:
        opts["logger"] = logger
    return opts


class HistoryDB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS history(
                   id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, url TEXT, title TEXT,
                   channel TEXT, quality TEXT, filepath TEXT, size INTEGER)""")
            self.conn.commit()

    def add(self, url, title, channel, quality, filepath, size):
        with self.lock:
            self.conn.execute(
                "INSERT INTO history(date,url,title,channel,quality,filepath,size) VALUES(?,?,?,?,?,?,?)",
                (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), url, title, channel, quality, filepath, size))
            self.conn.commit()

    def search(self, term=""):
        like = f"%{term}%"
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM history WHERE title LIKE ? OR channel LIKE ? ORDER BY id DESC",
                (like, like)).fetchall()

    def get(self, row_id):
        with self.lock:
            return self.conn.execute("SELECT * FROM history WHERE id=?", (row_id,)).fetchone()

    def delete(self, row_id):
        with self.lock:
            self.conn.execute("DELETE FROM history WHERE id=?", (row_id,))
            self.conn.commit()

    def clear(self):
        with self.lock:
            self.conn.execute("DELETE FROM history")
            self.conn.commit()


# --------------------------------------------------------------------------
# Local API (Chrome extension ke liye) - sirf 127.0.0.1, token ke saath
# --------------------------------------------------------------------------
API_PORTS = range(8765, 8775)
MAX_BODY = 2_000_000
EXT_COOKIE_FILE = APP_DIR / "cookies_from_extension.txt"


def write_netscape_cookies(cookies, path):
    """Chrome cookies (JSON) ko yt-dlp wale Netscape cookies.txt format mein likhta hai."""
    clean = lambda x: str(x).replace("\t", " ").replace("\r", "").replace("\n", "")
    lines = ["# Netscape HTTP Cookie File", "# YT Downloader extension se banayi gayi", ""]
    count = 0
    for c in cookies:
        if not isinstance(c, dict):
            continue
        domain, name = c.get("domain"), c.get("name")
        if not domain or not name:
            continue
        try:
            exp = int(c.get("expirationDate") or 0)
        except (TypeError, ValueError):
            exp = 0
        lines.append("\t".join([
            ("#HttpOnly_" if c.get("httpOnly") else "") + clean(domain),
            "TRUE" if str(domain).startswith(".") else "FALSE",
            clean(c.get("path") or "/"),
            "TRUE" if c.get("secure") else "FALSE",
            str(exp), clean(name), clean(c.get("value", "")),
        ]))
        count += 1
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return count


def make_api_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _origin(self):
            return self.headers.get("Origin") or ""

        def _host_ok(self):  # DNS-rebinding se bachav
            return (self.headers.get("Host") or "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

        def _origin_ok(self):  # sirf extension (ya bina Origin wale tools)
            o = self._origin()
            return o == "" or o.startswith("chrome-extension://")

        def _cors(self):
            o = self._origin()
            if o.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", o)
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Vary", "Origin")

        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            if not (self._host_ok() and self._origin().startswith("chrome-extension://")):
                return self._json(403, {"error": "forbidden"})
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()

        def do_GET(self):
            if self.path == "/ping" and self._host_ok():
                return self._json(200, {"app": "yt-downloader-pro", "v": 1})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not (self._host_ok() and self._origin_ok()):
                return self._json(403, {"error": "forbidden"})
            token = app.settings.get("api_token", "")
            sent = self.headers.get("X-Token", "")
            if not token or not hmac.compare_digest(sent.encode(), token.encode()):
                return self._json(401, {"error": "token galat hai - app se copy karke extension mein paste karein"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = 0
            if n <= 0 or n > MAX_BODY:
                return self._json(413 if n > MAX_BODY else 400, {"error": "bad body size"})
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                return self._json(400, {"error": "bad json"})

            if self.path == "/add":
                urls = [u.strip() for u in data.get("urls", []) if isinstance(u, str)
                        and u.strip().lower().startswith(("http://", "https://"))][:50]
                if not urls:
                    return self._json(400, {"error": "koi sahi URL nahi mila"})
                app.ui(app.api_add, urls, data.get("quality"), data.get("playlist"))
                return self._json(200, {"ok": True, "queued": len(urls)})

            if self.path == "/cookies":
                cookies = data.get("cookies")
                if not isinstance(cookies, list) or not cookies:
                    return self._json(400, {"error": "cookies khali hain"})
                count = write_netscape_cookies(cookies, EXT_COOKIE_FILE)
                problems = check_cookie_file(str(EXT_COOKIE_FILE))
                app.ui(app.set_cookie_file, str(EXT_COOKIE_FILE))
                return self._json(200, {"ok": True, "count": count, "problems": problems})

            self._json(404, {"error": "not found"})

    return Handler


class ApiServer:
    def __init__(self, app):
        self.app, self.httpd, self.port = app, None, None

    def start(self):
        handler = make_api_handler(self.app)
        for port in API_PORTS:
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
                break
            except OSError:
                continue
        if not self.httpd:
            return None
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.port

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("YT Downloader Pro")
        root.geometry("1000x680")
        root.minsize(880, 580)
        try:
            ttk.Style().theme_use("clam")
        except Exception:
            pass

        self.settings = load_settings()
        self.db = HistoryDB(HISTORY_DB)
        self.has_ffmpeg = bool(shutil.which("ffmpeg"))
        self.ui_q = queue.Queue()
        self.jobs = queue.Queue()
        self.items = {}
        self.current = None
        self.cancel = threading.Event()
        self.pause_req = False                 # current download ko 'Paused' dikhana hai (Cancelled nahi)
        self.queue_hold = threading.Event()    # set ho to naya download shuru nahi hota
        self._last_ui = 0
        self.last_error = ""

        if not self.settings.get("api_token"):
            self.settings["api_token"] = secrets.token_urlsafe(16)
            save_settings(self.settings)

        self.build_ui()
        self.refresh_history()
        self.api = ApiServer(self)
        port = self.api.start()
        self.v_api.set(f"Extension server chal raha hai (port {port})" if port
                       else "Extension server start nahi hua (ports busy)")
        threading.Thread(target=self.work_loop, daemon=True).start()
        root.after(100, self.pump)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI building ----------
    def build_ui(self):
        s = self.settings
        top = ttk.LabelFrame(self.root, text="Video / Playlist / Channel URL (har line mein ek URL)")
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.url_text = tk.Text(top, height=3, wrap="none")
        self.url_text.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        btns = ttk.Frame(top)
        btns.pack(side="right", padx=6)
        ttk.Button(btns, text="Paste", command=self.paste).pack(fill="x", pady=2)
        ttk.Button(btns, text="Add to Queue", command=self.add_urls).pack(fill="x", pady=2)

        opt = ttk.LabelFrame(self.root, text="Settings")
        opt.pack(fill="x", padx=10, pady=4)

        self.v_quality = tk.StringVar(value=s["quality"])
        self.v_folder = tk.StringVar(value=s["folder"])
        self.v_channel = tk.BooleanVar(value=s["channel_folder"])
        self.v_plfolder = tk.BooleanVar(value=s["playlist_folder"])
        self.v_playlist = tk.BooleanVar(value=s["playlist"])
        self.v_dupes = tk.BooleanVar(value=s["skip_dupes"])
        self.v_subs = tk.BooleanVar(value=s["subtitles"])
        self.v_thumb = tk.BooleanVar(value=s["thumbnail"])
        self.v_limit = tk.StringVar(value=str(s["limit_mb"]))
        self.v_cookies = tk.StringVar(value=s["cookies"])
        self.v_cookiefile = tk.StringVar(value=s["cookies_file"])
        self.v_autoretry = tk.BooleanVar(value=s["auto_retry"])

        ttk.Label(opt, text="Quality:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(opt, textvariable=self.v_quality, values=list(QUALITIES), state="readonly",
                     width=18).grid(row=0, column=1, sticky="w")
        ttk.Label(opt, text="Save folder:").grid(row=0, column=2, sticky="e", padx=6)
        ttk.Entry(opt, textvariable=self.v_folder, width=48).grid(row=0, column=3, sticky="we")
        ttk.Button(opt, text="Browse", command=self.browse).grid(row=0, column=4, padx=6)
        opt.columnconfigure(3, weight=1)

        checks = ttk.Frame(opt)
        checks.grid(row=1, column=0, columnspan=5, sticky="w", padx=6, pady=2)
        for text, var in [
            ("Channel ke naam ka folder", self.v_channel),
            ("Playlist ka alag folder", self.v_plfolder),
            ("Poori playlist download karein", self.v_playlist),
            ("Duplicates skip karein", self.v_dupes),
            ("Subtitles", self.v_subs),
            ("Thumbnail", self.v_thumb),
        ]:
            ttk.Checkbutton(checks, text=text, variable=var).pack(side="left", padx=6)

        extra = ttk.Frame(opt)
        extra.grid(row=2, column=0, columnspan=5, sticky="w", padx=6, pady=(0, 6))
        ttk.Label(extra, text="Speed limit (MB/s, 0 = unlimited):").pack(side="left")
        ttk.Spinbox(extra, from_=0, to=1000, textvariable=self.v_limit, width=6).pack(side="left", padx=6)
        ttk.Label(extra, text="Browser cookies:").pack(side="left", padx=(16, 0))
        ttk.Combobox(extra, textvariable=self.v_cookies, state="readonly", width=10,
                     values=["None", "firefox", "edge", "chrome", "brave"]).pack(side="left", padx=6)
        ttk.Label(extra, text="ya cookies.txt:").pack(side="left", padx=(10, 0))
        ttk.Entry(extra, textvariable=self.v_cookiefile, width=26).pack(side="left", padx=4)
        ttk.Button(extra, text="Browse", command=self.browse_cookies).pack(side="left")

        ttk.Checkbutton(opt, text="Bot check aane par khud retry karein (alternate client, phir Firefox/Edge/Chrome cookies)",
                        variable=self.v_autoretry).grid(row=3, column=0, columnspan=5, sticky="w", padx=12, pady=(0, 6))

        api_row = ttk.Frame(opt)
        api_row.grid(row=4, column=0, columnspan=5, sticky="w", padx=12, pady=(0, 6))
        ttk.Label(api_row, text="Chrome extension token:").pack(side="left")
        self.v_token = tk.StringVar(value=self.settings["api_token"])
        ttk.Entry(api_row, textvariable=self.v_token, width=26, state="readonly").pack(side="left", padx=6)
        ttk.Button(api_row, text="Copy", command=self.copy_token).pack(side="left")
        self.v_api = tk.StringVar(value="")
        ttk.Label(api_row, textvariable=self.v_api).pack(side="left", padx=12)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=4)

        # Queue tab
        qf = ttk.Frame(nb)
        nb.add(qf, text="Queue")
        cols = ("title", "status", "progress", "speed", "eta")
        self.q_tree = ttk.Treeview(qf, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, (480, 150, 80, 100, 80)):
            self.q_tree.heading(c, text=c.capitalize())
            self.q_tree.column(c, width=w, anchor="w" if c == "title" else "center")
        sb = ttk.Scrollbar(qf, command=self.q_tree.yview)
        self.q_tree.configure(yscrollcommand=sb.set)
        self.q_tree.pack(side="top", fill="both", expand=True)
        qb = ttk.Frame(qf)
        qb.pack(fill="x", pady=4)
        ttk.Button(qb, text="Pause Current", command=self.pause_current).pack(side="left", padx=4)
        ttk.Button(qb, text="Pause All", command=self.pause_all).pack(side="left", padx=4)
        ttk.Button(qb, text="Resume", command=self.resume).pack(side="left", padx=4)
        ttk.Button(qb, text="Cancel Current", command=self.cancel_current).pack(side="left", padx=(16, 4))
        ttk.Button(qb, text="Remove Selected", command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(qb, text="Clear Finished", command=self.clear_finished).pack(side="left", padx=4)

        # History tab
        hf = ttk.Frame(nb)
        nb.add(hf, text="History")
        sf = ttk.Frame(hf)
        sf.pack(fill="x", pady=4)
        ttk.Label(sf, text="Search:").pack(side="left", padx=4)
        self.v_search = tk.StringVar()
        self.v_search.trace_add("write", lambda *_: self.refresh_history())
        ttk.Entry(sf, textvariable=self.v_search, width=40).pack(side="left")
        hcols = ("date", "channel", "title", "quality", "size")
        self.h_tree = ttk.Treeview(hf, columns=hcols, show="headings", selectmode="browse")
        for c, w in zip(hcols, (120, 170, 430, 120, 80)):
            self.h_tree.heading(c, text=c.capitalize())
            self.h_tree.column(c, width=w, anchor="w")
        self.h_tree.pack(fill="both", expand=True)
        self.h_tree.bind("<Double-1>", lambda e: self.open_file())
        hb = ttk.Frame(hf)
        hb.pack(fill="x", pady=4)
        for text, cmd in [("Open File", self.open_file), ("Open Folder", self.open_folder),
                          ("Re-download", self.redownload), ("Delete Entry", self.delete_entry),
                          ("Clear History", self.clear_history)]:
            ttk.Button(hb, text=text, command=cmd).pack(side="left", padx=4)

        msg = "Ready"
        if not self.has_ffmpeg:
            msg = "WARNING: ffmpeg nahi mila - Full HD merge aur MP3 kaam nahi karenge. ffmpeg install karein."
        elif not ENV["runtimes"]:
            msg = "WARNING: JavaScript runtime (Deno) nahi mila - YouTube downloads fail ho sakte hain. 'Diagnose' dabayein."
        self.status = tk.StringVar(value=msg)
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Button(bar, text="Diagnose / Last Error", command=self.show_diagnose).pack(side="right")
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(side="left", fill="x", expand=True)

    # ---------- settings ----------
    def collect_settings(self):
        try:
            limit = float(self.v_limit.get() or 0)
        except ValueError:
            limit = 0
        self.settings.update({
            "folder": self.v_folder.get().strip() or DEFAULTS["folder"],
            "quality": self.v_quality.get(),
            "channel_folder": self.v_channel.get(),
            "playlist_folder": self.v_plfolder.get(),
            "playlist": self.v_playlist.get(),
            "skip_dupes": self.v_dupes.get(),
            "subtitles": self.v_subs.get(),
            "thumbnail": self.v_thumb.get(),
            "limit_mb": limit,
            "cookies": self.v_cookies.get(),
            "cookies_file": self.v_cookiefile.get().strip(),
            "auto_retry": self.v_autoretry.get(),
        })
        save_settings(self.settings)

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.v_folder.get() or str(Path.home()))
        if d:
            self.v_folder.set(d)

    def browse_cookies(self):
        f = filedialog.askopenfilename(title="cookies.txt chunein",
                                       filetypes=[("Cookies", "*.txt"), ("All files", "*.*")])
        if f:
            self.v_cookiefile.set(f)

    def paste(self):
        try:
            self.url_text.insert("end", self.root.clipboard_get().strip() + "\n")
        except tk.TclError:
            pass

    # ---------- queue ----------
    def add_urls(self):
        urls = [u for u in self.url_text.get("1.0", "end").split() if u.startswith(("http://", "https://"))]
        if not urls:
            messagebox.showwarning("URL chahiye", "Kam az kam ek sahi URL (http/https) paste karein.")
            return
        self.collect_settings()
        self.enqueue(urls, dict(self.settings))
        self.url_text.delete("1.0", "end")

    def enqueue(self, urls, opts):
        for url in urls:
            iid = self.q_tree.insert("", "end", values=(url, "Queued", "", "", ""))
            self.items[iid] = {"url": url, "opts": dict(opts)}
            self.jobs.put(iid)

    def copy_token(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.settings["api_token"])
        self.status.set("Token copy ho gaya - extension ke popup mein paste karein")

    # ----- extension se aane wale kaam (Tk thread mein chalte hain) -----
    def api_add(self, urls, quality=None, playlist=None):
        self.collect_settings()
        opts = dict(self.settings)
        if quality in QUALITIES:
            opts["quality"] = quality
        if playlist is not None:
            opts["playlist"] = bool(playlist)
        self.enqueue(urls, opts)
        self.status.set(f"Extension se {len(urls)} URL queue mein add hue")

    def set_cookie_file(self, path):
        self.v_cookiefile.set(path)
        self.collect_settings()
        self.status.set("Extension se YouTube cookies mil gayin aur set ho gayin")

    def cancel_current(self):
        if self.current:
            self.pause_req = False
            self.cancel.set()

    def pause_current(self):
        """Sirf chalti hui download rokta hai; queue agli item par chali jati hai."""
        if self.current:
            self.pause_req = True
            self.cancel.set()
            self.status.set("Pause ho raha hai...")

    def pause_all(self):
        """Chalti download roko aur queue ko naya kaam shuru karne se roko."""
        self.queue_hold.set()
        self.pause_current()
        self.status.set("Sab pause. 'Resume' dabane par dobara shuru hoga.")

    def resume(self):
        """Selected paused items ko dobara chalata hai; kuch select na ho to sab paused + queue."""
        resumable = ("Paused", "Cancelled", "Error")
        sel = [i for i in self.q_tree.selection() if i in self.items]
        targets = [i for i in sel if self.q_tree.set(i, "status").startswith(resumable)]
        if not sel:
            self.queue_hold.clear()
            targets = [i for i in self.q_tree.get_children()
                       if i in self.items and self.q_tree.set(i, "status").startswith("Paused")]
        for iid in targets:
            self.upd(iid, status="Queued", progress="", speed="", eta="")
            self.jobs.put(iid)
        if targets or not sel:
            self.queue_hold.clear()
        self.status.set(f"Resume: {len(targets)} item(s) dobara queue mein")

    def remove_selected(self):
        for iid in self.q_tree.selection():
            if iid == self.current:
                continue
            self.items.pop(iid, None)
            self.q_tree.delete(iid)

    def clear_finished(self):
        for iid in self.q_tree.get_children():
            st = self.q_tree.set(iid, "status")
            if st.startswith(("Done", "Error", "Cancelled", "Skipped")):
                self.items.pop(iid, None)
                self.q_tree.delete(iid)

    # ---------- worker thread ----------
    def ui(self, fn, *a, **k):
        self.ui_q.put((fn, a, k))

    def pump(self):
        try:
            while True:
                fn, a, k = self.ui_q.get_nowait()
                fn(*a, **k)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def upd(self, iid, **cols):
        if self.q_tree.exists(iid):
            for c, v in cols.items():
                self.q_tree.set(iid, c, v)

    def work_loop(self):
        while True:
            iid = self.jobs.get()
            while self.queue_hold.is_set():
                time.sleep(0.3)
            if iid not in self.items:
                continue
            self.process(iid)

    def process(self, iid):
        item = self.items[iid]
        o, url = item["opts"], item["url"]
        self.current = iid
        self.pause_req = False
        self.cancel.clear()
        self.ui(self.upd, iid, status="Starting...")
        self.ui(self.status.set, f"Downloading: {url}")

        def hook(d):
            if self.cancel.is_set():
                raise Cancelled()
            if d["status"] == "downloading":
                now = time.time()
                if now - self._last_ui < 0.25:
                    return
                self._last_ui = now
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = f"{d.get('downloaded_bytes', 0) / total * 100:.1f}%" if total else ""
                sp = d.get("speed")
                eta = d.get("eta")
                title = (d.get("info_dict") or {}).get("title")
                cols = {"status": "Downloading", "progress": pct,
                        "speed": f"{human_size(sp)}/s" if sp else "",
                        "eta": f"{int(eta)}s" if eta else ""}
                if title:
                    cols["title"] = title
                self.ui(self.upd, iid, **cols)
            elif d["status"] == "finished":
                self.ui(self.upd, iid, status="Processing...", progress="100%", speed="", eta="")

        try:
            self.ui(self.upd, iid, status="Checking URL...")
            is_pl = detect_playlist(url, o)
            saved, err = self.do_download(o, url, is_pl, {}, hook)
            tried = []
            first_err = err
            if not saved and err and is_bot_error(err) and o.get("auto_retry", True) and not cookie_opts(o):
                order = sorted(AUTO_FALLBACKS, key=lambda f: f[0] != self.settings.get("last_fallback"))
                for label, extra in order:
                    tried.append(label)
                    self.ui(self.upd, iid, status=f"Bot check - {label} try ho raha hai...")
                    saved, err = self.do_download(o, url, is_pl, extra, hook)
                    if saved or not err:
                        if saved:
                            self.settings["last_fallback"] = label
                            save_settings(self.settings)
                        break
                if not saved and err:
                    err = first_err
            if saved:
                self.ui(self.upd, iid, status=f"Done ({saved})", progress="100%", speed="", eta="")
                self.ui(self.refresh_history)
                self.ui(self.status.set, "Download complete")
            elif err:
                self.ui(self.report_error, iid, err, tried)
            else:
                self.ui(self.upd, iid, status="Skipped (pehle se downloaded)", speed="", eta="")
                self.ui(self.status.set, "Kuch naya download nahi hua (shayad pehle se downloaded)")
        except Cancelled:
            if self.pause_req:
                self.ui(self.upd, iid, status="Paused", speed="", eta="")
                self.ui(self.status.set, "Paused - 'Resume' se wahin se dobara shuru hoga")
            else:
                self.ui(self.upd, iid, status="Cancelled", speed="", eta="")
                self.ui(self.status.set, "Cancelled")
            self.pause_req = False
        except Exception as e:  # noqa
            self.ui(self.report_error, iid, str(e), [])
        finally:
            self.current = None

    def do_download(self, o, url, is_pl, extra, hook):
        """Ek attempt. Returns (saved_count, error_text). Cancelled upar propagate hota hai."""
        logger = QuietLogger()
        opts = build_ydl_opts(o, hook, self.has_ffmpeg, is_pl, logger)
        opts.update(extra)
        saved = 0
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                entries = [e for e in ((info or {}).get("entries") or [info]) if e]
                for e in entries:
                    path = None
                    rd = e.get("requested_downloads")
                    if rd:
                        path = rd[0].get("filepath") or rd[0].get("_filename")
                    if not path or not os.path.exists(path):
                        continue
                    self.db.add(e.get("webpage_url") or url, e.get("title", "Unknown"),
                                e.get("channel") or e.get("uploader") or "Unknown",
                                o["quality"], path, os.path.getsize(path))
                    saved += 1
        except Cancelled:
            raise
        except Exception as e:  # noqa
            return saved, logger.last_error or str(e)
        return saved, ("" if saved else logger.last_error)

    def report_error(self, iid, text, tried=None):
        text = (text or "Unknown error").strip()
        self.last_error = text
        log_error(text)
        opts_used = self.items.get(iid, {}).get("opts", self.settings)
        if is_bot_error(text) and cookie_opts(opts_used):
            self.upd(iid, status="Error: cookies set hain, phir bhi bot check", speed="", eta="")
            self.status.set("Cookies kaam nahi kar rahi - Diagnose window dekhein")
            self.show_diagnose()
        elif is_bot_error(text):
            self.upd(iid, status="Error: cookies chahiye (bot check)", speed="", eta="")
            self.status.set("YouTube ne bot check mangi hai - cookies set karein (details popup mein)")
            tried_msg = ""
            if tried:
                tried_msg = "App ne khud ye try kiye, sab fail hue: " + ", ".join(tried) + ".\n\n"
            messagebox.showwarning(
                "YouTube bot check",
                "YouTube ne 'Sign in to confirm you're not a bot' mangi hai.\n\n" + tried_msg +
                "Hal (koi ek karein):\n"
                "1) Firefox mein YouTube par login karein, phir 'Browser cookies' mein firefox chunein.\n"
                "2) Ya browser extension 'Get cookies.txt LOCALLY' se youtube.com ki cookies.txt export karein "
                "aur 'ya cookies.txt' mein us file ko Browse karein.\n\n"
                "Note: Chrome/Edge ki cookies Windows par aksar read nahi hoti "
                "(Chrome band hona chahiye), Firefox ya cookies.txt zyada reliable hain.")
        else:
            self.upd(iid, status=f"Error: {text[:70]}", speed="", eta="")
            self.status.set("Error aaya - 'Diagnose / Last Error' button dabayein (hal likha hoga)")

    def show_diagnose(self):
        self.collect_settings()
        win = tk.Toplevel(self.root)
        win.title("Diagnose")
        win.geometry("820x560")
        txt = tk.Text(win, wrap="word", font=("Consolas", 10))
        txt.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        txt.insert("1.0", diagnose_text(self.settings, self.last_error))

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(txt.get("1.0", "end").strip())

        row = ttk.Frame(win)
        row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(row, text="Copy (mujhe bhejne ke liye)", command=copy).pack(side="left")
        ttk.Button(row, text="Close", command=win.destroy).pack(side="right")

    # ---------- history ----------
    def refresh_history(self):
        self.h_tree.delete(*self.h_tree.get_children())
        for r in self.db.search(self.v_search.get().strip()):
            self.h_tree.insert("", "end", iid=str(r["id"]),
                               values=(r["date"], r["channel"], r["title"], r["quality"], human_size(r["size"])))

    def selected_row(self):
        sel = self.h_tree.selection()
        return self.db.get(int(sel[0])) if sel else None

    def open_file(self):
        r = self.selected_row()
        if not r:
            return
        if os.path.exists(r["filepath"]):
            open_path(r["filepath"])
        else:
            messagebox.showinfo("File nahi mili", "Ye file move ya delete ho chuki hai.")

    def open_folder(self):
        r = self.selected_row()
        if r:
            folder = os.path.dirname(r["filepath"])
            if os.path.isdir(folder):
                open_path(folder)

    def redownload(self):
        r = self.selected_row()
        if r:
            self.url_text.insert("end", r["url"] + "\n")
            self.v_dupes.set(False)  # warna archive skip kar dega
            self.add_urls()

    def delete_entry(self):
        r = self.selected_row()
        if not r:
            return
        ans = messagebox.askyesnocancel("Delete", "File bhi disk se delete karein?\n\nYes = file + entry\nNo = sirf entry")
        if ans is None:
            return
        if ans and os.path.exists(r["filepath"]):
            try:
                os.remove(r["filepath"])
            except OSError as e:
                messagebox.showerror("Error", str(e))
        self.db.delete(r["id"])
        self.refresh_history()

    def clear_history(self):
        if messagebox.askyesno("Clear History", "Poori history clear karein? (files delete nahi hongi)"):
            self.db.clear()
            self.refresh_history()

    def on_close(self):
        self.collect_settings()
        self.cancel.set()
        self.root.destroy()


def main():
    if tk is None:
        sys.exit("tkinter install nahi hai. Linux: sudo apt install python3-tk")
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()