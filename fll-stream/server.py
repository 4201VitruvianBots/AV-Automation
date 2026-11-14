"""FLL four-table stream controller.

Run:  python server.py [port]      (default port 8090)

Pages:
  /control   tablet page: pick a match, start/abort the timer
  /overlay   OBS browser source (16:9, transparent where the cameras go);
             also plays the start / 30-second / end sounds (?sound=0 to mute)
  /scores    enter match scores and show them on the stream
  /setup     header text, match length, schedule/team import
"""
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
DATA_FILE = os.path.join(ROOT, "data.json")
PAGES = {"/": "control.html", "/control": "control.html",
         "/overlay": "overlay.html", "/setup": "setup.html", "/scores": "scores.html"}

SOUNDS = {"start": ["charge", "mario_kart", ""],
          "warning": ["laser", "church_bell", ""],
          "end": ["buzzer", "four_ding", ""]}

# Things that can press a Bitfocus Companion button (set the button locations in /setup).
EVENTS = ["match_start", "warning", "match_end", "match_abort", "scores_shown", "scores_hidden"]
companion_log = {"text": ""}

lock = threading.Lock()
data = {}


def now_ms():
    return int(time.time() * 1000)


def parse_schedule(text):
    """Parse the robot game table copied from the FLL Nexus schedule page."""
    matches, practice_n, match_n = [], 0, 0
    for line in text.splitlines():
        m = re.match(r"\s*(\d{1,2}:\d{2}\s*[AP]M)\s*(\(practice\))?", line, re.I)
        if not m:
            continue
        if "\t" in line:  # keep empty cells so tables stay in their columns
            teams = [c.strip() for c in line.split("\t")[1:5]]
        else:
            teams = re.findall(r"\d+", line[m.end():])[:4]
        teams += [""] * (4 - len(teams))
        if m.group(2):
            practice_n += 1
            label = f"Practice {practice_n}"
        else:
            match_n += 1
            label = f"Match {match_n}"
        matches.append({"time": m.group(1).upper(), "label": label,
                        "practice": bool(m.group(2)), "teams": teams})
    return matches


def parse_teams(text):
    """Parse 'number / name' pairs (alternating lines, or 'number name' lines)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    teams, i = {}, 0
    while i < len(lines):
        line = lines[i]
        if re.fullmatch(r"\d+", line):
            if i + 1 < len(lines) and not re.fullmatch(r"\d+", lines[i + 1]):
                teams[line] = lines[i + 1]
                i += 1
        else:
            m = re.match(r"(\d+)[\s,:\-]+(.+)", line)
            if m:
                teams[m.group(1)] = m.group(2).strip()
        i += 1
    return teams


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def load():
    global data
    if os.path.exists(DATA_FILE):
        data = json.loads(read("data.json"))
    else:
        data = {
            "settings": {"left": "2026 - 2027 FLL BIOGLOW",
                         "right": "Da Vinci Qualifying Tournament",
                         "duration": 150},
            "matches": parse_schedule(read("seed_schedule.txt")),
            "teams": parse_teams(read("seed_teams.txt")),
            "index": 0, "phase": "idle", "startedAt": 0, "rev": 1,
        }
        save()
    data["settings"].setdefault("sounds", {k: v[0] for k, v in SOUNDS.items()})
    data["settings"].setdefault("swap", False)   # tables 3/4 on the left, 1/2 on the right
    data.setdefault("posted", None)              # index of the match whose scores are on screen
    data.setdefault("lastPlayed", None)
    data["settings"].setdefault("companion", {"host": "", "port": 8000,
                                              "buttons": {e: "" for e in EVENTS}})
    data.setdefault("warned", False)             # 30-second trigger already sent this match
    data["settings"].setdefault("noise", -50)  # dBFS of the constant test noise; 0 = off


def save():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, DATA_FILE)


def describe(i):
    if not 0 <= i < len(data["matches"]):
        return None
    m = data["matches"][i]
    return {"label": m["label"], "time": m["time"], "practice": m["practice"],
            "scores": m.get("scores") or [None] * 4,
            "teams": [{"number": t, "name": data["teams"].get(t, "")} for t in m["teams"]]}


def fire(event):
    """Press the Companion button configured for this event (in the background)."""
    c = data["settings"]["companion"]
    loc = c["buttons"].get(event, "")
    if not c["host"] or not re.fullmatch(r"\d+/\d+/\d+", loc):
        return
    base = f"http://{c['host']}:{c['port']}/api"
    url = f"{base}/location/{loc}/press"
    # Tell Companion which match this is (custom variable "matchName") before the start button runs.
    name = data["matches"][data["index"]]["label"] if event == "match_start" else None

    def go():
        if name:
            try:
                var = f"{base}/custom-variable/matchName/value?value={urllib.parse.quote(name)}"
                req = urllib.request.Request(var, data=name.encode(), method="POST",
                                             headers={"Content-Type": "text/plain"})
                urllib.request.urlopen(req, timeout=2).close()
            except Exception as e:
                print("Companion matchName not set:", e)
        try:
            req = urllib.request.Request(url, data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=2) as r:
                msg = f"{event}: pressed {loc} (HTTP {r.status})"
        except Exception as e:
            msg = f"{event}: FAILED to press {loc} ({e})"
        companion_log["text"] = time.strftime("%H:%M:%S ") + msg
        print("Companion", msg)
    threading.Thread(target=go, daemon=True).start()


def tick():
    """Advance the clock: 30-second warning and end of match. Called 10x a second."""
    if data["phase"] != "running":
        return
    dur = data["settings"]["duration"]
    left = dur - (now_ms() - data["startedAt"]) / 1000
    if left <= 0:
        data["phase"] = "done"
        save()
        fire("match_end")
    elif left <= 30 < dur and not data["warned"]:
        data["warned"] = True
        save()
        fire("warning")


def ticker():
    while True:
        time.sleep(0.1)
        with lock:
            tick()


def state():
    dur = data["settings"]["duration"]
    tick()
    return {"now": now_ms(), "phase": data["phase"], "startedAt": data["startedAt"],
            "duration": dur, "index": data["index"], "count": len(data["matches"]),
            "match": describe(data["index"]), "next": describe(data["index"] + 1),
            "left": data["settings"]["left"], "right": data["settings"]["right"],
            "sounds": data["settings"]["sounds"], "noise": data["settings"]["noise"], "swap": data["settings"]["swap"],
            "posted": describe(data["posted"]) if data["posted"] is not None else None,
            "lastPlayed": data["lastPlayed"], "companionLog": companion_log["text"], "rev": data["rev"]}


def action(body):
    state()  # settle running -> done first
    a = body.get("action")
    running = data["phase"] == "running"
    n = len(data["matches"])
    if a == "start" and not running and n:
        if data["posted"] is not None:
            fire("scores_hidden")
        data["phase"], data["startedAt"], data["warned"] = "running", now_ms(), False
        data["posted"], data["lastPlayed"] = None, data["index"]
        fire("match_start")
    elif a in ("abort", "reset"):
        data["phase"] = "idle"
        if running:
            fire("match_abort")
    elif a in ("next", "prev", "goto") and not running:
        i = {"next": data["index"] + 1, "prev": data["index"] - 1}.get(a, body.get("index"))
        if isinstance(i, int) and 0 <= i < n:
            data["index"], data["phase"] = i, "idle"
    elif a == "scores":
        i = body.get("index")
        if not (isinstance(i, int) and 0 <= i < n):
            raise ValueError("No such match")
        raw = (list(body.get("scores") or []) + [None] * 4)[:4]
        data["matches"][i]["scores"] = [None if v in (None, "") else int(v) for v in raw]
        if body.get("post"):
            data["posted"] = i
            fire("scores_shown")
    elif a == "post_last":
        if data["lastPlayed"] is None:
            raise ValueError("No match has been played yet")
        data["posted"] = data["lastPlayed"]
        fire("scores_shown")
    elif a == "unpost":
        if data["posted"] is not None:
            fire("scores_hidden")
        data["posted"] = None
    elif a == "companion_test":
        fire(str(body.get("event")))
    elif a == "settings":
        s = data["settings"]
        s["left"] = str(body.get("left", s["left"]))
        s["right"] = str(body.get("right", s["right"]))
        for k, v in (body.get("sounds") or {}).items():
            if k in SOUNDS and v in SOUNDS[k]:
                s["sounds"][k] = v
        c = body.get("companion")
        if isinstance(c, dict):
            sc = s["companion"]
            sc["host"] = str(c.get("host", sc["host"])).strip()
            sc["port"] = int(c.get("port") or 8000)
            for e in EVENTS:
                if e in (c.get("buttons") or {}):
                    sc["buttons"][e] = str(c["buttons"][e]).strip()
        if "swap" in body:
            s["swap"] = bool(body["swap"])
        if "noise" in body:
            n = int(body["noise"])
            s["noise"] = 0 if n >= 0 else max(-80, min(-20, n))
        if not running:
            s["duration"] = max(1, min(3600, int(body.get("duration", s["duration"]))))
    elif a == "import" and not running:
        result = {}
        if body.get("schedule", "").strip():
            matches = parse_schedule(body["schedule"])
            if not matches:
                raise ValueError("No matches found in the schedule text")
            data["matches"], data["index"], data["phase"] = matches, 0, "idle"
            data["posted"] = data["lastPlayed"] = None
            result["matches"] = len(matches)
        if body.get("teams", "").strip():
            teams = parse_teams(body["teams"])
            if not teams:
                raise ValueError("No teams found in the team text")
            data["teams"] = teams
            result["teams"] = len(teams)
        data["rev"] += 1
        save()
        return result
    else:
        raise ValueError("Not allowed right now" if running else "Unknown action")
    data["rev"] += 1 if a == "settings" else 0
    save()
    return {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/api/state":
            with lock:
                return self.send(200, state())
        if path == "/api/schedule":
            with lock:
                return self.send(200, {"matches": data["matches"], "teams": data["teams"],
                                       "settings": data["settings"]})
        if path in PAGES:
            with open(os.path.join(WEB, PAGES[path]), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        m = re.fullmatch(r"/sounds/(\w+)\.mp3", path)
        f = m and os.path.join(WEB, "sounds", m.group(1) + ".mp3")
        if f and os.path.isfile(f):
            with open(f, "rb") as fh:
                return self.send(200, fh.read(), "audio/mpeg")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        # /api/do/<action> needs no request body, which is easiest to call from Companion
        m = re.fullmatch(r"/api/do/(start|abort|next|prev|unpost|post_last)", self.path)
        if self.path != "/api/action" and not m:
            return self.send(404, {"error": "not found"})
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = {"action": m.group(1)} if m else json.loads(raw)
            with lock:
                result = action(body)
                result["state"] = state()
            self.send(200, result)
        except Exception as e:
            self.send(400, {"error": str(e)})


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    load()
    threading.Thread(target=ticker, daemon=True).start()
    print(f"Loaded {len(data['matches'])} matches, {len(data['teams'])} teams")
    print(f"OBS overlay : http://localhost:{port}/overlay")
    print(f"Tablet      : http://{lan_ip()}:{port}/control")
    print(f"Setup       : http://{lan_ip()}:{port}/setup")
    print("Ctrl+C to stop")
    # Windows lets two programs share a port when address reuse is on; turn it off so a
    # second copy of this server fails loudly instead of silently splitting the state.
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError:
        sys.exit(f"Port {port} is already in use - is the server already running?")
    server.serve_forever()
