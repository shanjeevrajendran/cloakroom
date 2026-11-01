"""`cloakroom proxy` (1.0, off by default): a loopback-only HTTP server for tools that let you set their API address.

The tool sends its request here instead of to the AI company. Cloakroom masks the text in the JSON body the way file
reads are masked, then forwards the request to the real address with the same method, path, query and headers. The
reply comes back byte for byte, chunk by chunk, so streaming works. Replies are not changed: a placeholder the model
sends back is restored by the hooks when it is written to a local file or command (the proxy's placeholders live in
handoff.py's proxy store, which every session's vault loads).

Headers are forwarded untouched, never logged or scanned (Authorization is your own key). Only the framing headers
(Content-Length, Content-Encoding, Transfer-Encoding) are read, to get the body. Bodies and values are never logged:
the ledger gets the host and the data kinds, like the hooks' where-it-went rows.

shortcut: one JSON walk over every string except structural keys (no per-vendor parser), so a new body shape fails
toward masking; an unreadable body (not JSON, brotli, bad zstd, over proxy_max_mb once decompressed) is refused with
a 503 and logged as denied, never forwarded, so nothing leaves unchecked. Upgrade when a vendor needs a field the walk
can't tell from text. A WebSocket upgrade gets a 426, so the tool falls back to plain HTTPS at once.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import agents, client, config, egress, events, handoff

SESSION = handoff.PROXY_SESSION
UPSTREAMS = {"/anthropic": "https://api.anthropic.com", "/openai": "https://api.openai.com",
             "/gemini": "https://generativelanguage.googleapis.com",
             # Codex signed in with ChatGPT. The tool-side address must not end in /backend-api/codex: Codex then
             # turns on workspace routing, which can swap the address for another and go around the proxy.
             "/chatgpt": "https://chatgpt.com/backend-api/codex"}
# What each tool is told to use. Codex's own default already ends in /v1; the other two add their paths themselves.
ADDRESS = {"claude": "/anthropic", "codex": "/openai/v1", "gemini": "/gemini"}
NAMES = {"claude": "Claude Code", "codex": "Codex CLI", "gemini": "Gemini CLI"}
TAG = "# cloakroom proxy"
ENV_KEY = {"claude": "ANTHROPIC_BASE_URL", "gemini": "GOOGLE_GEMINI_BASE_URL"}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer",
       "transfer-encoding", "upgrade", "expect"}
# Strings under these keys are structure or already-opaque data, not text: names, ids, types, signatures, base64.
SKIP_KEYS = frozenset({
    "model", "role", "type", "id", "tool_use_id", "tool_call_id", "call_id", "media_type", "mime_type", "mimeType",
    "signature", "thoughtSignature", "thought_signature", "encrypted_content", "cache_control", "metadata", "user",
    "data", "stop", "stop_sequences", "tool_choice", "response_format", "generationConfig", "generation_config",
    "service_tier", "reasoning_effort", "effort", "object", "finish_reason", "stop_reason", "status"})
SIGNED = frozenset({"thinking", "redacted_thinking", "reasoning"})  # signed blocks: any change makes the API refuse them
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}")
CACHE_SECONDS = 3600
_CACHE: dict[str, tuple[float, str, list]] = {}  # sha256(text) -> (when, masked text, [(placeholder, kind)])
_CACHE_LOCK = threading.Lock()
HARD_LIMIT = 256 * 1024 * 1024


class Unchecked(Exception):
    def __init__(self, reason: str, **numbers):
        self.reason, self.numbers = reason, numbers


# --- finding and masking the text ---

def _walk(node, fn, key: str = "", only_description: bool = False, tool_call: bool = False):
    """A copy of the JSON with fn applied to its text. Dict keys are never touched. Under "tools" only the
    descriptions are text (names and schemas must stay as they are)."""
    if isinstance(node, str):
        if key in SKIP_KEYS or node.startswith("data:") or (only_description and key != "description"):
            return node
        if key == "name" and tool_call and _IDENT.fullmatch(node):  # a tool's name must match the tools list
            return node
        return fn(node)
    if isinstance(node, list):
        return [_walk(x, fn, key, only_description, tool_call) for x in node]
    if isinstance(node, dict):
        if isinstance(node.get("type"), str) and node["type"] in SIGNED:
            return node
        call = bool({"input", "arguments", "args", "response"} & node.keys())
        return {k: _walk(v, fn, k, only_description or k == "tools", call) for k, v in node.items()}
    return node


class Masker:
    """Masks one string at a time through the service (or in process in rules mode), remembering what it found.
    The same text is asked about once an hour: a conversation sends its whole history again on every turn."""

    def __init__(self):
        cfg = config.load()
        self.stamp = hashlib.sha256(json.dumps([str(config.HOME), cfg["mode"], cfg["allow"], cfg["always"]], default=str).encode()).hexdigest()[:12]
        self.found: list[tuple[str, str]] = []

    def __call__(self, text: str) -> str:
        if len(text) < 3:
            return text
        key = self.stamp + hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
        now = time.monotonic()
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            self.found += hit[2]
            return hit[1]
        r = client.mask(text, SESSION)
        found = [(m[0], m[1]) for m in r["masked"]]
        with _CACHE_LOCK:
            if len(_CACHE) > 20000:
                _CACHE.clear()
            _CACHE[key] = (now, r["text"], found)
        self.found += found
        return r["text"]


def _zstd():
    """compression.zstd (3.14+) or its backport; None when neither imports, and zstd bodies are then refused."""
    try:
        import compression.zstd as zstd
    except ImportError:
        try:
            import backports.zstd as zstd
        except ImportError:
            return None
    return zstd


def _decode(raw: bytes, encoding: str, limit: int = HARD_LIMIT) -> bytes:
    """The body decompressed, never more than limit bytes of it (a small bomb can't fill memory)."""
    enc = encoding.strip().lower()
    if enc in ("", "identity"):
        return raw
    if enc == "zstd":
        zstd = _zstd()
        if zstd is None:
            raise Unchecked("no-zstd")
        try:
            # window_log_max 24: a frame can't make the decoder reserve more than 16 MB up front
            d = zstd.ZstdDecompressor(options={zstd.DecompressionParameter.window_log_max: 24})
            out = d.decompress(raw, limit)
        except zstd.ZstdError as e:
            raise Unchecked("encoding") from e
        if not d.eof and not d.needs_input:
            raise Unchecked("too-large")
        if not d.eof or d.unused_data:  # cut short, or a second frame
            raise Unchecked("encoding")
        return out
    if enc not in ("gzip", "x-gzip", "deflate"):
        raise Unchecked("encoding")
    try:
        d = zlib.decompressobj(47 if enc != "deflate" else 15)  # 47: gzip or zlib header, whichever it is
        out = d.decompress(raw, limit)
        if d.unconsumed_tail:
            raise Unchecked("too-large")
        return out
    except zlib.error as e:
        raise Unchecked("encoding") from e


def mask_body(raw: bytes, encoding: str = "", limit: int = 16 * 1024 * 1024) -> tuple[bytes, bool, list, str]:
    """(body to send, whether it changed, [(placeholder, kind)] found, the masked text for counting placeholders).
    A body that can't be read raises Unchecked; the caller refuses the request. Unchanged means the original bytes
    (and its Content-Encoding) go out exactly as they came."""
    if len(raw) > limit:
        raise Unchecked("too-large", size=len(raw), limit=limit)
    data = _decode(raw, encoding, limit)
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise Unchecked("not-json") from e
    if not isinstance(obj, (dict, list)):
        raise Unchecked("not-json")
    masker = Masker()
    new = _walk(obj, masker)
    if not masker.found:
        return raw, False, [], data.decode("utf-8", "replace")
    out = json.dumps(new, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return out, True, masker.found, out.decode("utf-8", "replace")


# --- upstreams ---

def upstreams() -> dict[str, tuple[str, str]]:
    """{prefix: (host, base URL)} from the built-ins and config's proxy_upstreams. https only, except a loopback
    address (for a local model or a test)."""
    found = {}
    for prefix, url in {**UPSTREAMS, **config.load()["proxy_upstreams"]}.items():
        u = urlsplit(str(url))
        host = egress.host_of(u.netloc) if u.netloc else ""
        if not re.fullmatch(r"/[A-Za-z0-9_-]+", str(prefix)) or not host or \
                not (u.scheme == "https" or u.scheme == "http" and host in egress.LOOPBACK):
            continue
        found[prefix] = (host, url.rstrip("/"))
    return found


def _route(path: str, table: dict) -> tuple[str, str] | None:
    """(prefix, the rest of the path with its query) or None."""
    for prefix in table:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return prefix, path[len(prefix):]
    return None


# --- the server ---

WHY = {"encoding": "is compressed in a way Cloakroom cannot read (for example brotli, or damaged zstd)",
       "no-zstd": "is compressed with zstd and this Python has no zstd library (install it: pip install backports.zstd)",
       "not-json": "is not JSON, so Cloakroom cannot check it",
       "too-large": "is larger than proxy_max_mb, so Cloakroom did not check it"}


def _error(kind: str, message: str) -> bytes:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _body(self) -> bytes | None:
        """The request body, or None when there is none. Chunked bodies are read to the end."""
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            out = bytearray()
            while True:
                size = int((self.rfile.readline(1024).split(b";")[0].strip() or b"0"), 16)
                if size == 0:
                    while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                        pass  # trailers
                    return bytes(out)
                out += self.rfile.read(size)
                self.rfile.readline(8)
                if len(out) > HARD_LIMIT:
                    raise ValueError("body too large")
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else None

    def _log(self, host: str, prefix: str, action: str, found, ms: float, body: str = "", extra: dict | None = None):
        data = {"tool_name": "proxy", "session_id": "proxy"}
        extra = {"dest": [host], "carries": egress.carried(body, found), **(extra or {})}
        try:
            extra["via"] = prefix
            events.record("proxy", data, action, found, ms, "", extra)
        except OSError as e:  # a full disk must not stop the call; the next row is written when it clears
            print(f"cloakroom proxy: could not write the ledger ({e.__class__.__name__})", file=sys.stderr)

    def do_request(self):
        if (self.headers.get("Host") or "").rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
            return self._reply(403, _error("permission_error", "Cloakroom proxy answers on this machine only."))
        if "websocket" in (self.headers.get("Upgrade") or "").lower():  # Codex reads 426 as "use HTTPS now"
            return self._reply(426, _error("invalid_request_error", "Cloakroom proxy takes HTTP requests only, not "
                                           "WebSockets."))
        table = upstreams()
        if self.path == "/health":
            return self._reply(200, json.dumps({"ok": True, "service": "cloakroom-proxy", "routes": sorted(table)}).encode())
        hit = _route(self.path, table)
        if not hit:
            return self._reply(404, _error("not_found_error", "Unknown address. This proxy serves: " + ", ".join(sorted(table))))
        prefix, rest = hit
        host, base = table[prefix]
        cfg = config.load()
        if cfg["egress"] == "allowlist" and host not in egress.LOOPBACK and not egress.listed(host, cfg["allowed_hosts"]):
            self._log(host, prefix, "held", [], 0.0, extra={"hold": "approved-sites"})
            return self._reply(403, _error("permission_error", f"Cloakroom held this request: {host} is not on your "
                                           f"approved sites list. To approve it, run: cloakroom allow-host {host}"))
        cap = int(cfg["proxy_max_mb"] * 1048576)
        try:
            declared = 0 if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower() else int(self.headers.get("Content-Length") or 0)
        except ValueError:
            declared = 0  # _body reports it
        if declared > cap:  # refuse before reading it into memory
            self._log(host, prefix, "deny", [], 0.0, extra={"reason": "too-large", "check": "proxy request", "size": declared, "limit": cap})
            return self._reply(503, _error("api_error", f"Cloakroom did not send this request: its body {WHY['too-large']}. "
                                           "See docs-LIMITS.md, \"Cloakroom proxy\"."))
        try:
            raw = self._body()
        except (ValueError, OSError):
            return self._reply(400, _error("invalid_request_error", "Cloakroom proxy could not read the request body."))
        body, found, text, t0 = raw, [], "", time.perf_counter()
        changed = False
        if raw:
            try:
                body, changed, found, text = mask_body(raw, self.headers.get("Content-Encoding") or "", cap)
            except Unchecked as u:  # fail closed: a body Cloakroom can't read never reaches the upstream
                self._log(host, prefix, "deny", [], (time.perf_counter() - t0) * 1000,
                          extra={"reason": u.reason, "check": "proxy request", **u.numbers})
                return self._reply(503, _error("api_error", f"Cloakroom did not send this request: its body "
                                               f"{WHY.get(u.reason, 'cannot be read')}. See docs-LIMITS.md, "
                                               "\"Cloakroom proxy\"."))
            except Exception as e:  # anything at all
                # fail closed, like the hooks: nothing leaves when it can't be checked
                self._log(host, prefix, "deny", [], (time.perf_counter() - t0) * 1000, extra={"why": e.__class__.__name__})
                return self._reply(503, _error("api_error", "Cloakroom is not working, so this request was not sent. "
                                               "Run: cloakroom doctor"))
        ms = (time.perf_counter() - t0) * 1000
        self._log(host, prefix, "masked" if found else "pass", found, ms, text)
        self._forward(base, rest, body, changed, raw is not None)

    def _forward(self, base: str, rest: str, body: bytes | None, changed: bool, has_body: bool):
        u = urlsplit(base)
        make = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        kw = {"context": ssl.create_default_context()} if u.scheme == "https" else {}
        conn = make(u.hostname, u.port, timeout=30, **kw)
        headers = {}
        for k, v in self.headers.items():
            low = k.lower()
            if low in HOP or low in ("host", "content-length") or (changed and low == "content-encoding"):
                continue
            headers[k] = v
        if has_body:
            headers["Content-Length"] = str(len(body))
        try:
            conn.connect()
            conn.sock.settimeout(900)  # a stream can sit quiet a long while
            conn.request(self.command, u.path + rest, body=body, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            return self._reply(502, _error("api_error", f"Cloakroom proxy could not reach {u.hostname} "
                                           f"({e.__class__.__name__})."))
        try:
            self._relay(resp)
        finally:
            conn.close()

    def _relay(self, resp: http.client.HTTPResponse):
        """The reply as it came: status and headers (minus connection framing), then the body chunk by chunk."""
        self.send_response_only(resp.status, resp.reason)
        length = resp.getheader("Content-Length")
        for k, v in resp.getheaders():
            if k.lower() not in HOP and k.lower() != "content-length":
                self.send_header(k, v)
        no_body = self.command == "HEAD" or resp.status in (204, 304) or resp.status < 200
        chunked = length is None and not no_body and self.request_version != "HTTP/1.0"
        if length is not None:
            self.send_header("Content-Length", length)
        elif chunked:
            self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if no_body:
            return
        try:
            while True:
                data = resp.read1(16384)
                if not data:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data) if chunked else data)
                self.wfile.flush()
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # the tool stopped listening

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_request


def make_server(port: int = 0) -> ThreadingHTTPServer:
    """Bound to 127.0.0.1 only, the literal address (not "localhost", which can mean ::1)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


# --- port, state, agents ---

def _state_path():
    return config.private_dir() / "proxy.json"


def port() -> int:
    """The proxy's port: config's proxy_port, else the one picked on first use and kept in proxy.json."""
    cfg = config.load()["proxy_port"]
    if cfg:
        return int(cfg)
    try:
        return int(json.loads(_state_path().read_text())["port"])
    except (OSError, ValueError, KeyError):
        pass
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        chosen = s.getsockname()[1]
    _state_path().write_text(json.dumps({"port": chosen}))
    return chosen


def running(p: int | None = None) -> bool:
    try:
        with http.client.HTTPConnection("127.0.0.1", p or port(), timeout=2) as c:
            c.request("GET", "/health")
            return json.loads(c.getresponse().read()).get("service") == "cloakroom-proxy"
    except Exception:
        return False


def address(agent: str, p: int | None = None) -> str:
    path = "/chatgpt" if agent == "codex" and codex_chatgpt() else ADDRESS[agent]
    return f"http://127.0.0.1:{p or port()}{path}"


def _path(agent: str) -> str:
    if agent == "claude":
        return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude"),
                            "settings.json")
    if agent == "codex":
        return os.path.join(agents.codex_home(), "config.toml")
    return os.path.join(agents.gemini_home(), ".env")


def _line(agent: str, addr: str) -> str:
    return f'openai_base_url = "{addr}"' if agent == "codex" else f"{ENV_KEY[agent]}={addr}"


def _backup() -> dict:
    try:
        return json.loads((config.HOME / "proxy-backup.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_backup(data: dict):
    path = config.private_dir() / "proxy-backup.json"
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def _key_line(agent: str, line: str) -> bool:
    key = r"openai_base_url\s*=" if agent == "codex" else rf"(?:export\s+)?{ENV_KEY[agent]}\s*="
    return bool(re.match(rf"\s*{key}", line))


def _write_text(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".cloakroom-tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError:
        return None


def codex_chatgpt() -> bool:
    """Whether Codex's auth.json says it is signed in with ChatGPT. Reads only the auth_mode field; no other value from
    the file is kept or printed."""
    try:
        with open(os.path.join(agents.codex_home(), "auth.json"), encoding="utf-8") as f:
            mode = json.load(f).get("auth_mode")
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(mode, str) and mode.lower().replace("-", "").replace("_", "") != "apikey"


NO_ZSTD = ("this Python has no zstd library, so the proxy refuses Codex's ChatGPT requests (503). Install it: "
           "pip install backports.zstd")


def codex_signin_warning() -> str | None:
    if not codex_chatgpt():
        return None
    if _zstd() is None:
        return f"cloakroom: warning: Codex is signed in with ChatGPT, and {NO_ZSTD}"
    return ("cloakroom: Codex is signed in with ChatGPT, so the proxy sends its requests on to chatgpt.com. If you "
            "switch Codex's sign-in, run cloakroom proxy install codex again.")


def install(agent: str, p: int | None = None, show: bool = False) -> str:
    """Point the agent's own config at the proxy. Returns the message. show: print what it would write, change nothing."""
    addr = address(agent, p)
    where = _path(agent)
    if show:
        return {"claude": f'In {where}, under "env": "ANTHROPIC_BASE_URL": "{addr}"\n(or, for one run: ANTHROPIC_BASE_URL={addr} claude)',
                "codex": f"In {where}, at the top: {_line(agent, addr)}",
                "gemini": f"In {where}: {_line(agent, addr)}\n(or, for one run: GOOGLE_GEMINI_BASE_URL={addr} gemini)"}[agent]
    backup = _backup()
    mine = backup.get(agent) or {}
    if agent == "claude":
        data = agents._load(where)
        env = data.setdefault("env", {})
        if not isinstance(env, dict):
            raise ValueError(f'{where}: "env" is not a table this version understands; left as it is')
        if not mine:
            mine = {"existed": os.path.exists(where), "old": env.get(ENV_KEY[agent])}
        env[ENV_KEY[agent]] = addr
        agents._write(where, data)
    else:
        text = _read_text(where)
        eol = "\r\n" if text and "\r\n" in text else "\n"
        lines = (text or "").splitlines(keepends=True)
        pair = [TAG + eol, _line(agent, addr) + eol]
        at = next((i for i, ln in enumerate(lines) if ln.strip() == TAG), None)
        if at is not None:  # already ours: update the address
            lines[at:at + 2] = pair
        else:
            hit = None
            for i, ln in enumerate(lines):
                if agent == "codex" and ln.lstrip().startswith("["):
                    break  # top-level keys end at the first table
                if _key_line(agent, ln):
                    hit = i
                    break
            if not mine:
                mine = {"existed": text is not None, "old": lines[hit] if hit is not None else None,
                        "noeol": bool(text) and not text.endswith(("\n", "\r"))}
            if hit is not None:
                lines[hit:hit + 1] = pair
            elif agent == "codex":
                lines[0:0] = pair
            else:
                if lines and not lines[-1].endswith(("\n", "\r")):
                    lines[-1] += eol
                lines += pair
        _write_text(where, "".join(lines))
    backup[agent] = mine
    _save_backup(backup)
    return where


def uninstall(agent: str) -> str:
    """Take the proxy address out of the agent's config and put back what was there before. Returns the path."""
    where = _path(agent)
    backup = _backup()
    mine = backup.pop(agent, {})
    if agent == "claude":
        data = agents._load(where) if os.path.exists(where) else {}
        env = data.get("env") if isinstance(data.get("env"), dict) else {}
        now = str(env.get(ENV_KEY[agent], ""))
        if re.fullmatch(r"http://127\.0\.0\.1:\d+/anthropic", now):  # only an address we wrote
            if mine.get("old") is not None:
                env[ENV_KEY[agent]] = mine["old"]
            else:
                env.pop(ENV_KEY[agent], None)
            if not env:
                data.pop("env", None)
            if not data and not mine.get("existed", True):
                os.remove(where)
            else:
                agents._write(where, data)
    else:
        text = _read_text(where)
        if text is not None:
            lines = text.splitlines(keepends=True)
            at = next((i for i, ln in enumerate(lines) if ln.strip() == TAG), None)
            if at is not None:
                eol = "\r\n" if "\r\n" in text else "\n"
                lines[at:at + 2] = [mine["old"]] if mine.get("old") else []
                out = "".join(lines)
                if mine.get("noeol") and out.endswith(eol):  # the file had no final newline; we added one
                    out = out[:-len(eol)]
                if not out.strip() and not mine.get("existed", True):
                    os.remove(where)
                else:
                    _write_text(where, out)
    _save_backup(backup)
    return where


def pointing() -> dict[str, str]:
    """{agent: address} for each agent whose config points at a proxy on this machine."""
    out = {}
    for agent in ADDRESS:
        try:
            if agent == "claude":
                got = str((agents._load(_path(agent)).get("env") or {}).get(ENV_KEY[agent], ""))
            else:
                text = _read_text(_path(agent)) or ""
                lines = text.splitlines()
                got = next((ln.split("=", 1)[1].strip().strip("\"'") for i, ln in enumerate(lines)
                            if i and lines[i - 1].strip() == TAG), "")
        except (ValueError, OSError, AttributeError):
            continue
        if re.fullmatch(r"http://127\.0\.0\.1:\d+/\S+", got):
            out[agent] = got
    return out


def run(a) -> int:
    """cloakroom proxy [install|uninstall AGENT [--print]] [--port N] [--background]"""
    if a.action:
        if a.agent not in ADDRESS:
            print("cloakroom: say which tool: claude, codex or gemini", file=sys.stderr)
            return 2
        name = NAMES[a.agent]
        try:
            warn = codex_signin_warning() if a.action == "install" and a.agent == "codex" else None
            if a.action == "install" and a.print:
                print(install(a.agent, a.port, show=True))
                if warn:
                    print(warn, file=sys.stderr)
                return 0
            if a.action == "uninstall":
                print(f"cloakroom: {name} no longer points at the proxy ({uninstall(a.agent)})")
                return 0
            where = install(a.agent, a.port)
        except (ValueError, OSError) as e:
            print(f"cloakroom: {e}", file=sys.stderr)
            return 1
        print(f"cloakroom: {name} now sends its requests to {address(a.agent, a.port)} (written to {where}).\n"
              "Start the proxy with: cloakroom proxy   (while it isn't running, the tool's requests fail rather than "
              "go around it)\n"
              f"To undo: cloakroom proxy uninstall {a.agent}")
        if warn:
            print(warn, file=sys.stderr)
        return 0
    p = a.port or port()
    if a.background:
        if running(p):
            print(f"cloakroom proxy: already running on port {p}")
            return 0
        client.start_background(f"proxy --port {p}")
        print(f"cloakroom proxy: starting on http://127.0.0.1:{p} (output: ~/.cloakroom/serve.log)")
        return 0
    try:
        server = make_server(p)
    except OSError:
        print(f"cloakroom proxy: port {p} is taken. Set \"proxy_port\" in ~/.cloakroom/config.json, or run it "
              "with --port N", file=sys.stderr)
        return 1
    print(f"cloakroom proxy: listening on http://127.0.0.1:{p} (this machine only). Routes: "
          + ", ".join(f"{k} -> {v[1]}" for k, v in sorted(upstreams().items()))
          + "\nPoint a tool at it: cloakroom proxy install claude|codex|gemini   (--print shows the change). Stop: Ctrl+C",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
