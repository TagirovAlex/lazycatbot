#!/usr/bin/env python3
"""Tiny local dashboard for a Lichess bot.

Shows:
  * live games (local SVG board, clocks, SAN moves);
  * the most recent game when nothing is running;
  * recent results and rating changes.

The bot token is read from lichess-bot/config.yml and stays server-side.
Bind address defaults to 127.0.0.1 only.

Usage:
  python tools/bot_dashboard.py
  python tools/bot_dashboard.py --port 8765 --user LazyCatBot
"""
from __future__ import annotations

import argparse
import html
import io
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess
import chess.pgn
import chess.svg
import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://lichess.org"
DEFAULT_CONFIG = r"C:\LichessBot\lichess-bot\config.yml"
DASHBOARD_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.js")


def find_config(path: str) -> str:
    candidates = [
        path,
        os.path.join(ROOT, "lichess-bot", "config.yml"),
        os.path.join(ROOT, "lichess", "config.yml"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def load_token(path: str) -> str:
    cfg = find_config(path)
    if not cfg:
        return ""
    try:
        with open(cfg, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return str(data.get("token", "") or "")
    except Exception:
        return ""


class BotApi:
    def __init__(self, username: str, token: str):
        self.username = username
        self.token = token
        self.headers = {"Accept": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.lock = threading.Lock()
        self.moves_cache: dict[str, tuple[float, list[str]]] = {}
        self.pgn_cache: dict[str, tuple[float, str]] = {}
        self.cache = {
            "playing": (0.0, []),
            "recent": (0.0, []),
            "profile": (0.0, {}),
            "status": (0.0, None),
        }
        self.last_error = ""

    def _get(self, path: str, params: dict | None = None,
             accept: str | None = None, auth: bool = True,
             timeout: float = 8.0):
        headers = dict(self.headers)
        if accept:
            headers["Accept"] = accept
        if not auth:
            headers.pop("Authorization", None)
        r = requests.get(API + path, params=params, headers=headers, timeout=timeout)
        if r.status_code == 429:
            raise RuntimeError("Lichess rate limit (429), showing cached data")
        r.raise_for_status()
        return r

    def _cached(self, key: str, ttl: float, fetch):
        now = time.time()
        with self.lock:
            stamp, value = self.cache[key]
        if now - stamp < ttl:
            return value
        try:
            value = fetch()
            with self.lock:
                self.cache[key] = (now, value)
                self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            with self.lock:
                value = self.cache[key][1]
        return value

    def playing(self) -> list[dict]:
        def fetch():
            if not self.token:
                return []
            data = self._get("/api/account/playing").json()
            out = []
            for g in data.get("nowPlaying", []):
                out.append({
                    "gameId": g.get("gameId", ""),
                    "color": g.get("color", ""),
                    "opponent": (g.get("opponent") or {}).get("username", "?"),
                    "oppRating": (g.get("opponent") or {}).get("rating"),
                    "speed": g.get("speed", ""),
                    "perf": g.get("perf", ""),
                    "rated": bool(g.get("rated")),
                    "secondsLeft": g.get("secondsLeft"),
                    "isMyTurn": bool(g.get("isMyTurn")),
                    "lastMove": g.get("lastMove", ""),
                    "fen": g.get("fen", ""),
                })
            return out
        return self._cached("playing", 2.0, fetch)

    def recent(self) -> list[dict]:
        def fetch():
            r = self._get(
                f"/api/games/user/{urllib.parse.quote(self.username)}",
                params={"max": 12, "tags": "false", "clocks": "false",
                        "evals": "false", "opening": "false"},
                accept="application/x-ndjson",
                auth=True, timeout=12.0,
            )
            games = []
            for line in r.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                g = json.loads(line)
                white = ((g.get("players") or {}).get("white") or {})
                black = ((g.get("players") or {}).get("black") or {})
                wname = (white.get("user") or {}).get("name", "")
                bname = (black.get("user") or {}).get("name", "")
                me_color = "white" if wname.lower() == self.username.lower() else "black"
                me = white if me_color == "white" else black
                opp = black if me_color == "white" else white
                winner = g.get("winner")
                if not winner:
                    result = "½–½"
                    outcome = "draw"
                elif winner == me_color:
                    result = "1–0" if me_color == "white" else "0–1"
                    outcome = "win"
                else:
                    result = "0–1" if me_color == "white" else "1–0"
                    outcome = "loss"
                clock = g.get("clock") or {}
                games.append({
                    "id": g.get("id", ""),
                    "opponent": (opp.get("user") or {}).get("name", "?"),
                    "oppRating": opp.get("rating"),
                    "color": me_color,
                    "result": result,
                    "outcome": outcome,
                    "status": g.get("status", ""),
                    "speed": g.get("speed", ""),
                    "rated": bool(g.get("rated")),
                    "ratingDiff": me.get("ratingDiff"),
                    "createdAt": g.get("createdAt"),
                    "clock": f"{clock.get('initial', '?')}+{clock.get('increment', 0)}"
                             if clock else "",
                })
            return games
        return self._cached("recent", 30.0, fetch)

    def profile(self) -> dict:
        def fetch():
            return self._get(
                f"/api/user/{urllib.parse.quote(self.username)}",
                auth=False, timeout=8.0,
            ).json()
        return self._cached("profile", 15.0, fetch)

    def online(self):
        def fetch():
            data = self._get("/api/users/status",
                             params={"ids": self.username},
                             auth=False, timeout=5.0).json()
            return bool(data and data[0].get("online")) if isinstance(data, list) else None
        return self._cached("status", 10.0, fetch)

    def pgn_text(self, game_id: str) -> str:
        now = time.time()
        with self.lock:
            stamp, text = self.pgn_cache.get(game_id, (0.0, ""))
        if now - stamp < 3.0:
            return text
        try:
            r = self._get(
                f"/game/export/{urllib.parse.quote(game_id)}",
                params={"tags": "false", "clocks": "false", "evals": "false"},
                auth=False, timeout=12.0,
            )
            text = r.text
            with self.lock:
                self.pgn_cache[game_id] = (now, text)
            return text
        except Exception:
            return text

    def moves(self, game_id: str) -> list[str]:
        """Flat SAN move list for a live or finished game (cached briefly)."""
        now = time.time()
        with self.lock:
            stamp, cached = self.moves_cache.get(game_id, (0.0, []))
        if now - stamp < 2.0:
            return cached
        try:
            pgn = chess.pgn.read_game(io.StringIO(self.pgn_text(game_id)))
            out: list[str] = []
            if pgn:
                board = pgn.board()
                for move in pgn.mainline_moves():
                    out.append(board.san(move))
                    board.push(move)
            with self.lock:
                self.moves_cache[game_id] = (now, out)
            return out
        except Exception:
            with self.lock:
                return self.moves_cache.get(game_id, (0.0, []))[1]

    def board_svg(self, game_id: str, ply: int | None = None) -> str:
        """Render the live position, or any ply of a game, as SVG."""
        game = next((g for g in self.playing() if g.get("gameId") == game_id), None)
        if ply is None and game and game.get("fen"):
            board = chess.Board(game["fen"])
            last = None
            if game.get("lastMove"):
                try:
                    last = chess.Move.from_uci(game["lastMove"])
                except ValueError:
                    pass
            orientation = chess.BLACK if game.get("color") == "black" else chess.WHITE
            return chess.svg.board(
                board, lastmove=last, orientation=orientation, size=520,
                colors={
                    "square light": "#f0d9b5",
                    "square dark": "#b58863",
                    "square light lastmove": "#cdd26a",
                    "square dark lastmove": "#aaa23a",
                },
            )

        pgn = chess.pgn.read_game(io.StringIO(self.pgn_text(game_id)))
        if not pgn:
            raise RuntimeError("empty PGN")
        board = pgn.board()
        moves = list(pgn.mainline_moves())
        if ply is None:
            ply = len(moves)
        ply = max(0, min(ply, len(moves)))
        for i in range(ply):
            board.push(moves[i])
        last = moves[ply - 1] if ply > 0 else None

        if game:
            orientation = chess.BLACK if game.get("color") == "black" else chess.WHITE
        else:
            black = pgn.headers.get("Black", "").lower()
            orientation = chess.BLACK if black == self.username.lower() else chess.WHITE

        return chess.svg.board(
            board, lastmove=last, orientation=orientation, size=520,
            colors={
                "square light": "#f0d9b5",
                "square dark": "#b58863",
                "square light lastmove": "#cdd26a",
                "square dark lastmove": "#aaa23a",
            },
        )

    def snapshot(self) -> dict:
        p = self.profile() or {}
        perfs = p.get("perfs") or {}
        ratings = {}
        for key in ("bullet", "blitz", "rapid", "classical"):
            if key in perfs and perfs[key].get("rating"):
                ratings[key] = {
                    "rating": perfs[key].get("rating"),
                    "rd": perfs[key].get("rd"),
                    "prog": perfs[key].get("prog"),
                }
        return {
            "username": p.get("username", self.username),
            "title": p.get("title"),
            "playing": self.playing(),
            "recent": self.recent(),
            "ratings": ratings,
            "online": self.online(),
            "error": self.last_error,
            "ts": time.time(),
        }


def page_html(username: str) -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(username)} — games</title>
<style>
:root {{
  --bg: #161512; --panel: #262421; --panel2: #302e2b;
  --text: #d6d3cf; --dim: #9b9894; --green: #629924; --red: #c33;
  --yellow: #d8a52a; --line: #3b3936;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; }}
header {{ display: flex; flex-wrap: wrap; gap: 14px; align-items: center;
  padding: 14px 20px; background: var(--panel); border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 5; }}
.brand {{ font-size: 20px; font-weight: 700; letter-spacing: .3px; }}
.brand .pawn {{ color: var(--green); margin-right: 6px; }}
.badge {{ font-size: 11px; font-weight: 700; background: var(--green); color: #fff;
  padding: 2px 6px; border-radius: 3px; vertical-align: middle; }}
.ratings {{ display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; }}
.rating {{ background: var(--panel2); border: 1px solid var(--line); border-radius: 4px;
  padding: 3px 8px; font-size: 12px; }}
.rating b {{ color: #fff; }}
#status {{ font-size: 12px; color: var(--dim); }}
main {{ max-width: 1180px; margin: 0 auto; padding: 18px 20px 40px; }}
h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .8px;
  color: var(--dim); margin: 24px 0 10px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }}
.card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
  overflow: hidden; }}
.card-head {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline;
  padding: 10px 12px; border-bottom: 1px solid var(--line); }}
.card-head .who {{ font-weight: 700; font-size: 15px; }}
.card-head .meta {{ color: var(--dim); font-size: 12px; }}
.card-head .turn {{ margin-left: auto; font-size: 12px; font-weight: 700; }}
.turn.me {{ color: var(--green); }} .turn.opp {{ color: var(--yellow); }}
.board {{ width: 100%; height: auto; aspect-ratio: 1 / 1; border: 0; display: block;
  background: #111; object-fit: contain; }}
.moves {{ padding: 6px 12px; border-top: 1px solid var(--line);
  font: 12px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace;
  color: var(--dim); max-height: 120px; overflow: auto; }}
.moves b {{ color: var(--text); }}
.nav {{ display: flex; align-items: center; gap: 6px; padding: 6px 10px;
  border-top: 1px solid var(--line); }}
.nav button {{ background: #3b3936; color: var(--text); border: 1px solid #4a4844;
  border-radius: 4px; padding: 2px 8px; cursor: pointer; font-size: 13px; }}
.nav button:hover {{ background: #4a4844; }}
.plylabel {{ margin: 0 auto; color: var(--dim); font-size: 12px;
  min-width: 70px; text-align: center; }}
.mv {{ cursor: pointer; padding: 1px 2px; border-radius: 2px; }}
.mv:hover {{ color: #fff; background: #3b3936; }}
.mv.cur {{ color: #fff; background: #4a4844; font-weight: 700; }}
.empty {{ background: var(--panel); border: 1px dashed var(--line); border-radius: 6px;
  padding: 30px; text-align: center; color: var(--dim); }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: left; padding: 7px 9px; border-bottom: 1px solid var(--line);
  font-size: 13px; white-space: nowrap; }}
th {{ color: var(--dim); font-weight: 600; font-size: 11px; text-transform: uppercase; }}
tr:hover td {{ background: var(--panel2); }}
a {{ color: var(--text); text-decoration: none; }}
a:hover {{ color: #fff; text-decoration: underline; }}
.win {{ color: var(--green); font-weight: 700; }}
.loss {{ color: var(--red); font-weight: 700; }}
.draw {{ color: var(--yellow); font-weight: 700; }}
.dim {{ color: var(--dim); }}
.err {{ margin: 10px 0; padding: 8px 10px; border: 1px solid #6b3a2a;
  background: #2e1f18; color: #e0a080; border-radius: 4px; font-size: 12px; }}
@media (max-width: 640px) {{
  .ratings {{ margin-left: 0; }}
  th:nth-child(1), td:nth-child(1) {{ display: none; }}
}}
</style>
</head>
<body>
<header>
  <div class="brand"><span class="pawn">♟</span>{html.escape(username)}
    <span class="badge" id="title" style="display:none">BOT</span></div>
  <div class="ratings" id="ratings"></div>
  <div id="status">загрузка…</div>
</header>
<main>
  <div id="error"></div>
  <section>
    <h2 id="live-title">Сейчас играет</h2>
    <div id="live" class="grid"></div>
  </section>
  <section>
    <h2>Последние партии</h2>
    <div class="card"><table>
      <thead><tr><th>Когда</th><th>Соперник</th><th>Цвет</th><th>Результат</th>
      <th>Контроль</th><th>Рейтинг</th></tr></thead>
      <tbody id="recent"><tr><td colspan="6" class="dim">загрузка…</td></tr></tbody>
    </table></div>
  </section>
</main>
<script src="/dashboard.js" defer></script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    api: BotApi
    username: str

    def log_message(self, fmt, *args):
        # Keep the console quiet; errors still surface in the UI.
        return

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urllib.parse.urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, page_html(self.username).encode("utf-8"),
                       "text/html; charset=utf-8")
        elif route == "/dashboard.js":
            try:
                with open(DASHBOARD_JS, "rb") as f:
                    body = f.read()
                self._send(200, body, "application/javascript; charset=utf-8")
            except OSError:
                self._send(404, b"dashboard.js not found", "text/plain")
        elif route.startswith("/board"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            game_id = (query.get("game") or [""])[0]
            ply_raw = (query.get("ply") or [""])[0]
            try:
                ply = int(ply_raw) if ply_raw not in ("", None) else None
                svg = self.api.board_svg(game_id, ply)
                self._send(200, svg.encode("utf-8"), "image/svg+xml; charset=utf-8")
            except Exception as exc:
                self._send(404, f"board error: {exc}".encode("utf-8"),
                           "text/plain; charset=utf-8")
        elif route.startswith("/api/data"):
            try:
                data = json.dumps(self.api.snapshot(), ensure_ascii=False).encode("utf-8")
                self._send(200, data, "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}).encode("utf-8"),
                           "application/json; charset=utf-8")
        elif route.startswith("/api/moves"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            game_id = (query.get("game") or [""])[0]
            try:
                data = json.dumps({"moves": self.api.moves(game_id)},
                                  ensure_ascii=False).encode("utf-8")
                self._send(200, data, "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}).encode("utf-8"),
                           "application/json; charset=utf-8")
        elif route == "/health":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--user", default="LazyCatBot")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args()

    token = load_token(args.config)
    api = BotApi(args.user, token)
    print(f"dashboard: http://{args.host}:{args.port}/")
    print(f"user: {args.user}; token: {'loaded' if token else 'missing'}")

    handler = type("BoundHandler", (Handler,), {"api": api, "username": args.user})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
