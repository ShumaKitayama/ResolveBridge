#!/usr/bin/env python3
"""Client for claude_fusion_bridge.lua v2.x (file-in / Resolve-log-out).

  bridge_client.py status
  bridge_client.py exec  'return comp:GetAttrs().COMPS_Name'
  bridge_client.py file  path/to/script.lua [--timeout 600]
  bridge_client.py stop
  bridge_client.py token        create runtime/token.lua if missing (then restart the bridge in Resolve)
"""
import base64
import fcntl
import json
import os
import re
import secrets
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the clone (override: RESOLVE_BRIDGE_HOME)
BASE = os.path.join(os.path.expanduser(os.environ.get("RESOLVE_BRIDGE_HOME", ROOT)), "runtime")
LOG = os.path.expanduser(
    "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/logs/davinci_resolve.log")
STATE = os.path.join(BASE, "client_state.json")
START_RE = re.compile(r"CLAUDE_BR_START (\d+) v(\S+)(?: auth=(on|off))?")
TOKEN_FILE = os.path.join(BASE, "token.lua")
STOP_RE = re.compile(r"CLAUDE_BR_STOP (\d+)")


LOG1 = LOG + ".1"  # Resolve rotates davinci_resolve.log at ~2 MB


def ensure_runtime():
    """runtime dir (mode 700) + shared-secret token file (mode 600); returns the token"""
    for d in (BASE, os.path.join(BASE, "inbox"), os.path.join(BASE, "outbox")):
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o700)
    if not os.path.exists(TOKEN_FILE):
        tok = secrets.token_hex(24)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write('return "%s"\n' % tok)
    return read_token()


def read_token():
    try:
        m = re.search(r'"([0-9a-f]{32,})"', open(TOKEN_FILE).read())
        return m.group(1) if m else None
    except OSError:
        return None


def lua_literal(v):
    """Python value -> safe Lua literal (strings are fully escaped; never emitted as code)"""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("non-finite number")
        return repr(float(v)) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        out = []
        for ch in v:
            o = ord(ch)
            if ch in '"\\':
                out.append("\\" + ch)
            elif o < 32 or o == 127:
                out.append("\\%03d" % o)
            else:
                out.append(ch)
        return '"' + "".join(out) + '"'
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua_literal(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ", ".join("[%s] = %s" % (lua_literal(str(k)), lua_literal(x)) for k, x in v.items()) + "}"
    raise TypeError("unsupported type: %s" % type(v).__name__)


def _read(path, start=0):
    try:
        with open(path, "rb") as f:
            f.seek(start)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _read_log_tail():
    return _read(LOG1) + _read(LOG)


def session():
    """Latest bridge session that started and has not stopped (None if none)."""
    text = _read_log_tail()
    starts = list(START_RE.finditer(text))
    if starts:
        m = starts[-1]
        sid, ver, pos, auth = m.group(1), m.group(2), m.end(), m.group(3) or "off"
    else:  # start marker rotated away: fall back to the cached session
        st = _load_state()
        if not st.get("session"):
            return None
        sid, ver, pos, auth = st["session"], st.get("version", "?"), 0, st.get("auth", "off")
    if any(s.group(1) == sid for s in STOP_RE.finditer(text, pos)):
        return None
    return {"session": sid, "version": ver, "auth": auth}


def _load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _collect(sid, n, since, ino):
    pat = re.compile(r"CLAUDE_BR %s %d (\d+)/(\d+) (\S*)" % (sid, n))
    try:
        rotated = os.stat(LOG).st_ino != ino
    except OSError:
        rotated = True
    text = (_read(LOG1, since) + _read(LOG)) if rotated else _read(LOG, since)
    parts, total = {}, None
    for m in pat.finditer(text):
        parts[int(m.group(1))] = m.group(3)
        total = int(m.group(2))
    if total and len(parts) == total:
        return json.loads(base64.b64decode("".join(parts[i] for i in range(1, total + 1))).decode("utf-8"))
    return None


def _scrub(obj, token):
    """never hand the shared secret back to the caller (it appears in Lua chunk names inside error messages)"""
    if not token:
        return obj
    if isinstance(obj, str):
        return obj.replace(token, "<token>")
    if isinstance(obj, list):
        return [_scrub(x, token) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub(v, token) for k, v in obj.items()}
    return obj


def run(code, timeout=120.0):
    return _scrub(_run(code, timeout), read_token())


def _run(code, timeout=120.0):
    token = ensure_runtime()
    s = session()
    if not s:
        return {"ok": False, "error": "bridge not running: Fusion page > Workspace > Scripts > claude_fusion_bridge"}
    sid = s["session"]
    suffix = ""
    if s["auth"] == "on":
        if not token:
            return {"ok": False, "error": "bridge requires a token but runtime/token.lua is missing"}
        suffix = "_" + token
    with open(os.path.join(BASE, ".client.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        st = _load_state()
        n = st.get("next", 1) if st.get("session") == sid else 1
        with open(STATE, "w") as f:
            json.dump({"session": sid, "version": s["version"], "auth": s["auth"], "next": n + 1}, f)
        st_log = os.stat(LOG)
        since, ino = st_log.st_size, st_log.st_ino
        src = os.path.join(BASE, "inbox", "cmd_%s_%d%s.lua" % (sid, n, suffix))
        fd = os.open(src + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(code)
        os.rename(src + ".tmp", src)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            res = _collect(sid, n, since, ino)
            if res is not None:
                return res
            time.sleep(0.1)
        return {"ok": False, "error": "timeout waiting for result (cmd %d, session %s)" % (n, sid)}
    finally:
        if os.path.exists(src):
            os.remove(src)


def stop():
    s = session()
    if not s:
        return {"ok": True, "note": "no running session"}
    open(os.path.join(BASE, "stop_" + s["session"]), "w").close()
    return {"ok": True, "stopped": s["session"]}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        s = session()
        res = {"alive": False} if not s else dict(s, **run("return 'pong'", 10))
        if s and s["auth"] == "off":
            res["warning"] = "bridge is running WITHOUT auth; run `bridge_client.py token` and restart the bridge"
    elif cmd == "token":
        ensure_runtime()
        res = {"ok": True, "token_file": TOKEN_FILE, "note": "restart claude_fusion_bridge in Resolve to enable auth"}
    elif cmd == "stop":
        res = stop()
    else:
        timeout = 120.0
        if "--timeout" in argv:
            i = argv.index("--timeout")
            timeout = float(argv[i + 1])
            del argv[i:i + 2]
        code = argv[2] if cmd == "exec" else open(argv[2]).read()
        res = run(code, timeout)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res.get("ok", res.get("alive")) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
