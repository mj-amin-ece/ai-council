#!/usr/bin/env python3
"""AI Council: local server that sends prompts to Claude, ChatGPT (Codex) and Gemini
through their official CLIs, so each runs on your existing subscription.

Run:  python3 server.py   then open http://localhost:8765
No packages to install; uses only the Python standard library.
"""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8765"))
TIMEOUT = int(os.environ.get("AI_TIMEOUT", "300"))
HERE = Path(__file__).resolve().parent
USAGE_FILE = HERE / "usage.json"  # your local token history (not uploaded to git)
# A short system prompt for Claude instead of Claude Code's long coding prompt:
# about 10x fewer input tokens per call, so your subscription limits last longer.
CLAUDE_SYSTEM_PROMPT = "You are a helpful, accurate and honest assistant. Answer in Markdown."

# The CLIs run in an empty folder so they can't read or touch your files.
WORKDIR = Path(tempfile.mkdtemp(prefix="ai-council-"))
# Same idea for Codex: short instructions instead of its coding-agent prompt (~35% fewer tokens).
CODEX_INSTRUCTIONS = WORKDIR.parent / f"{WORKDIR.name}-codex-instructions.md"
CODEX_INSTRUCTIONS.write_text(CLAUDE_SYSTEM_PROMPT)

EXTRA_PATHS = [
    str(Path.home() / ".local/bin"),
    str(Path.home() / ".npm-global/bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
]
ENV = dict(os.environ)
ENV["PATH"] = os.pathsep.join([ENV.get("PATH", "")] + EXTRA_PATHS)


def find(cmd):
    return shutil.which(cmd, path=ENV["PATH"])


RUNNING = set()  # CLI processes in flight, so the Stop button can end them


class Cancelled(Exception):
    pass


def kill(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)  # the CLI and any helpers it started
    except (ProcessLookupError, PermissionError):
        pass


def run(argv, prompt):
    """Run a CLI with the prompt on stdin; return (stdout, stderr, exit code)."""
    p = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=WORKDIR, env=ENV, start_new_session=True,
    )
    p.cancelled = False
    RUNNING.add(p)
    try:
        out, err = p.communicate(prompt, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        kill(p)
        p.communicate()
        raise
    finally:
        RUNNING.discard(p)
    if p.cancelled:
        raise Cancelled()
    return out, err, p.returncode


def cancel_all():
    for p in list(RUNNING):
        p.cancelled = True
        kill(p)


def json_lines(text):
    """Parse JSONL output, skipping lines that aren't JSON."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except ValueError:
                pass


def find_key(obj, key):
    """Return the last value stored under `key` anywhere inside nested JSON."""
    found = None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found = v
            sub = find_key(v, key)
            if sub is not None:
                found = sub
    elif isinstance(obj, list):
        for v in obj:
            sub = find_key(v, key)
            if sub is not None:
                found = sub
    return found


def estimate_tokens(text):
    """Rough estimate when a CLI doesn't report tokens: ~4 characters per token."""
    return max(1, round(len(text) / 4))


def result(text, prompt, model, input_tokens=None, output_tokens=None, cached=0, cost=None):
    """Standard reply from an adapter. Falls back to estimates if tokens are unknown."""
    estimated = input_tokens is None or output_tokens is None
    return {
        "text": text,
        "usage": {
            "model": model,
            "input": input_tokens if input_tokens is not None else estimate_tokens(prompt),
            "output": output_tokens if output_tokens is not None else estimate_tokens(text),
            "cached": cached or 0,
            "cost_usd": cost,
            "estimated": estimated,
        },
    }


# ---- Subscription limits reported by the CLIs (latest seen, per provider) ----

def window_label(name, minutes=None):
    names = {"five_hour": "5-hour limit", "seven_day": "Weekly limit",
             "seven_day_opus": "Weekly limit (Opus)", "seven_day_sonnet": "Weekly limit (Sonnet)",
             "primary": "Short-term limit", "secondary": "Weekly limit"}
    if minutes:
        hours = minutes / 60
        return f"{int(hours)}-hour limit" if hours < 48 else f"{round(hours / 24)}-day limit"
    return names.get(name, name.replace("_", " ").capitalize())


def claude_limits(event):
    windows = (event.get("rate_limit_info") or {}).get("unifiedWindows") or {}
    out = []
    for name, w in windows.items():
        if isinstance(w, dict) and "utilization" in w:
            out.append({"label": window_label(name), "used_percent": round(w["utilization"] * 100, 1),
                        "resets_at": w.get("resetsAt")})
    return out


def codex_limits(rate_limits):
    out = []
    for name in ("primary", "secondary"):
        w = (rate_limits or {}).get(name)
        if isinstance(w, dict) and "used_percent" in w:
            resets = w.get("resets_at")
            if resets is None and w.get("resets_in_seconds") is not None:
                resets = int(time.time() + w["resets_in_seconds"])
            out.append({"label": window_label(name, w.get("window_minutes")),
                        "used_percent": round(float(w["used_percent"]), 1), "resets_at": resets})
    return out


# ---- Adapters: one function per model. Each takes (prompt, model) and returns result(...). ----

def ask_claude(prompt, model):
    argv = [find("claude"), "-p", "--output-format", "stream-json", "--verbose",
            "--tools", "", "--strict-mcp-config", "--system-prompt", CLAUDE_SYSTEM_PROMPT]
    if model:
        argv += ["--model", model]
    out, err, code = run(argv, prompt)
    final, limits = None, None
    for ev in json_lines(out):
        if ev.get("type") == "rate_limit_event":
            limits = claude_limits(ev) or limits
        elif ev.get("type") == "result":
            final = ev
    if limits:
        save_limits("claude", limits)
    if not final or final.get("is_error"):
        msg = (final or {}).get("result") or err.strip() or out.strip()[-2000:]
        raise RuntimeError(msg or f"claude exited {code}")
    u = final.get("usage") or {}
    real_model = next(iter(final.get("modelUsage") or {}), model or "default")
    cached = (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
    return result(final.get("result", "").strip(), prompt, real_model,
                  input_tokens=(u.get("input_tokens") or 0) + cached,
                  output_tokens=u.get("output_tokens"), cached=cached,
                  cost=final.get("total_cost_usd"))


def ask_chatgpt(prompt, model):
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        last_msg = f.name
    try:
        argv = [find("codex"), "exec", "--json", "--skip-git-repo-check",
                "--sandbox", "read-only", "--output-last-message", last_msg,
                "-c", f"model_instructions_file={json.dumps(str(CODEX_INSTRUCTIONS))}",
                "-c", "features.shell_tool=false"]
        if model:
            argv += ["--model", model]
        argv += ["-"]  # read the prompt from stdin
        out, err, code = run(argv, prompt)
        text = Path(last_msg).read_text().strip() if os.path.exists(last_msg) else ""
        if code != 0 and not text:
            raise RuntimeError(err.strip()[-2000:] or out.strip()[-2000:] or f"codex exited {code}")
        events = list(json_lines(out))
        limits = codex_limits(find_key(events, "rate_limits"))
        if limits:
            save_limits("chatgpt", limits)
        # Newer CLIs report {"usage": {...}} on turn.completed; older ones "total_token_usage".
        u = find_key(events, "total_token_usage") or find_key(events, "usage") or {}
        real_model = find_key(events, "model")
        return result(text, prompt, real_model if isinstance(real_model, str) else (model or "default"),
                      input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"),
                      cached=u.get("cached_input_tokens", 0))
    finally:
        try:
            os.remove(last_msg)
        except OSError:
            pass


def ask_gemini(prompt, model):
    # Gemini appends -p to whatever arrives on stdin.
    argv = [find("gemini"), "-p", "(Respond to the request above.)", "--output-format", "json"]
    if model:
        argv += ["--model", model]
    out, err, code = run(argv, prompt)
    data = None
    for stream in (out, err):  # the result is on stdout; errors may arrive as JSON on stderr
        try:
            data = json.loads(stream[stream.index("{"):])
            break
        except ValueError:
            pass
    if data is None:  # older CLI without JSON output: plain text, estimated tokens
        if code != 0:
            raise RuntimeError(err.strip()[-2000:] or out.strip() or f"gemini exited {code}")
        return result(out.strip(), prompt, model or "default")
    if data.get("error"):
        e = data["error"]
        msg = e.get("message", json.dumps(e)) if isinstance(e, dict) else str(e)
        if "auth" in msg.lower():
            msg += "\n\nFix: run  gemini  once in Terminal and choose \"Login with Google\"."
        raise RuntimeError(msg)
    models = ((data.get("stats") or {}).get("models")) or {}
    inp = outp = cached = 0
    for tokens in (m.get("tokens") or {} for m in models.values()):
        inp += tokens.get("prompt", 0)
        outp += tokens.get("candidates", 0) + tokens.get("thoughts", 0)
        cached += tokens.get("cached", 0)
    real_model = ", ".join(models) or model or "default"
    return result((data.get("response") or "").strip(), prompt, real_model,
                  input_tokens=inp if models else None, output_tokens=outp if models else None,
                  cached=cached)


# Models shown in each dropdown. "" means the CLI's own default.
# Names change often: edit these lists to add or remove models
# (the page also has an "Other…" option to type any model name).
PROVIDERS = {
    "claude":  {"label": "Claude",  "cmd": "claude", "ask": ask_claude,
                "install": "curl -fsSL https://claude.ai/install.sh | bash",
                "models": [
                    ["", "Default"],
                    ["fable", "Fable (latest, most capable)"],
                    ["opus", "Opus (latest)"],
                    ["sonnet", "Sonnet (latest)"],
                    ["haiku", "Haiku (latest, fastest)"],
                ]},
    "chatgpt": {"label": "ChatGPT", "cmd": "codex",  "ask": ask_chatgpt,
                "install": "brew install codex   (then run: codex  and sign in with ChatGPT)",
                "models": [
                    ["", "Default"],
                    ["gpt-6.1-sol", "GPT-6.1 Sol"],
                    ["gpt-6-sol", "GPT-6 Sol"],
                    ["gpt-6-luna", "GPT-6 Luna (faster)"],
                ]},
    "gemini":  {"label": "Gemini",  "cmd": "gemini", "ask": ask_gemini,
                "install": "brew install gemini-cli   (then run: gemini  and sign in with Google)",
                "models": [
                    ["", "Default (auto)"],
                    ["gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)"],
                    ["gemini-3-pro-preview", "Gemini 3 Pro (preview)"],
                    ["gemini-3-flash-preview", "Gemini 3 Flash (preview)"],
                    ["gemini-2.5-pro", "Gemini 2.5 Pro"],
                    ["gemini-2.5-flash", "Gemini 2.5 Flash"],
                ]},
}


# ---- Usage history, stored locally in usage.json ----

LOCK = threading.Lock()


def load_usage():
    try:
        data = json.loads(USAGE_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("records", [])
    data.setdefault("limits", {})
    return data


def save_usage(data):
    tmp = USAGE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(USAGE_FILE)


def record_usage(provider, usage):
    with LOCK:
        data = load_usage()
        data["records"].append({"t": int(time.time()), "provider": provider, **usage})
        save_usage(data)


def save_limits(provider, limits):
    with LOCK:
        data = load_usage()
        data["limits"][provider] = {"windows": limits, "updated": int(time.time())}
        save_usage(data)


def usage_summary():
    """Totals per provider and model for today, the last 7 days and all time."""
    with LOCK:
        data = load_usage()
    now = time.time()
    start_of_today = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    week_ago = now - 7 * 86400
    out = {}
    for key in PROVIDERS:
        out[key] = {"limits": data["limits"].get(key), "models": {}}
    for r in data["records"]:
        prov = out.get(r.get("provider"))
        if prov is None:
            continue
        m = prov["models"].setdefault(r.get("model") or "default", {
            p: {"requests": 0, "input": 0, "output": 0, "cost_usd": 0.0, "estimated": False}
            for p in ("today", "week", "all")})
        periods = ["all"] + (["week"] if r["t"] >= week_ago else []) + (["today"] if r["t"] >= start_of_today else [])
        for p in periods:
            s = m[p]
            s["requests"] += 1
            s["input"] += r.get("input", 0)
            s["output"] += r.get("output", 0)
            s["cost_usd"] += r.get("cost_usd") or 0
            s["estimated"] = s["estimated"] or bool(r.get("estimated"))
    return out


ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def trusted(self):
        """Block other websites open in your browser from using your subscriptions
        (cross-site requests and DNS rebinding)."""
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin.split("//", 1)[-1] in ALLOWED_HOSTS

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.trusted():
            return self.send_error(403)
        if self.path in ("/", "/index.html"):
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            self.send_json({
                key: {"label": p["label"], "installed": bool(find(p["cmd"])),
                      "install": p["install"], "models": p["models"]}
                for key, p in PROVIDERS.items()
            })
        elif self.path == "/api/usage":
            self.send_json(usage_summary())
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.trusted() or not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.send_error(403)
        if self.path == "/api/cancel":
            cancel_all()
            return self.send_json({"ok": True})
        if self.path == "/api/usage/reset":
            with LOCK:
                data = load_usage()
                data["records"] = []
                save_usage(data)
            return self.send_json({"ok": True})
        if self.path != "/api/ask":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        key = req.get("provider")
        provider = PROVIDERS.get(key)
        if not provider:
            return self.send_json({"error": "Unknown provider"}, 400)
        if not find(provider["cmd"]):
            return self.send_json({"error": f"{provider['cmd']} is not installed. Install: {provider['install']}"})
        start = time.time()
        try:
            res = provider["ask"](req.get("prompt", ""), (req.get("model") or "").strip())
            record_usage(key, res["usage"])
            self.send_json({**res, "seconds": round(time.time() - start, 1)})
        except Cancelled:
            self.send_json({"error": "Stopped.", "stopped": True})
        except subprocess.TimeoutExpired:
            self.send_json({"error": f"Timed out after {TIMEOUT}s. Set AI_TIMEOUT to allow longer answers."})
        except Exception as e:  # show the CLI's own error in the UI
            self.send_json({"error": str(e)})


if __name__ == "__main__":
    # Bind to localhost only, so nobody else on your network can use your subscriptions.
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        raise SystemExit(f"Port {PORT} is already in use. Is AI Council already running? "
                         f"Or start on another port:  PORT=8766 python3 server.py")
    print(f"AI Council running at http://localhost:{PORT}  (Ctrl+C to stop)")
    for key, p in PROVIDERS.items():
        print(f"  {p['label']:8} {'ready' if find(p['cmd']) else 'NOT INSTALLED -> ' + p['install']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        cancel_all()
