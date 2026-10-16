#!/usr/bin/env python3
"""AI Council: local server that sends prompts to Claude, ChatGPT (Codex) and Gemini
through their official CLIs, so each runs on your existing subscription.

Run:  python3 server.py   then open http://localhost:8765
No packages to install; uses only the Python standard library.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8765"))
TIMEOUT = int(os.environ.get("AI_TIMEOUT", "300"))
HERE = Path(__file__).resolve().parent

# The CLIs run in an empty folder so they can't read or touch your files.
WORKDIR = Path(tempfile.mkdtemp(prefix="ai-council-"))

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


def run(argv, prompt):
    """Run a CLI with the prompt on stdin; return (stdout, stderr, exit code)."""
    p = subprocess.run(
        argv, input=prompt, capture_output=True, text=True,
        cwd=WORKDIR, env=ENV, timeout=TIMEOUT,
    )
    return p.stdout, p.stderr, p.returncode


# ---- Adapters: one function per model. Each takes (prompt, model) and returns text. ----

def ask_claude(prompt, model):
    argv = [find("claude"), "-p", "--output-format", "text", "--tools", ""]
    if model:
        argv += ["--model", model]
    out, err, code = run(argv, prompt)
    if code != 0:
        raise RuntimeError(err.strip() or out.strip() or f"claude exited {code}")
    return out.strip()


def ask_chatgpt(prompt, model):
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        last_msg = f.name
    try:
        argv = [find("codex"), "exec", "--skip-git-repo-check",
                "--sandbox", "read-only", "--output-last-message", last_msg]
        if model:
            argv += ["--model", model]
        argv += ["-"]  # read the prompt from stdin
        out, err, code = run(argv, prompt)
        text = Path(last_msg).read_text().strip() if os.path.exists(last_msg) else ""
        if code != 0 and not text:
            raise RuntimeError(err.strip()[-2000:] or f"codex exited {code}")
        return text or out.strip()
    finally:
        try:
            os.remove(last_msg)
        except OSError:
            pass


def ask_gemini(prompt, model):
    # Gemini appends -p to whatever arrives on stdin.
    argv = [find("gemini"), "-p", "(Respond to the request above.)"]
    if model:
        argv += ["--model", model]
    out, err, code = run(argv, prompt)
    if code != 0:
        raise RuntimeError(err.strip()[-2000:] or out.strip() or f"gemini exited {code}")
    return out.strip()


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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
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
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/ask":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        provider = PROVIDERS.get(req.get("provider"))
        if not provider:
            return self.send_json({"error": "Unknown provider"}, 400)
        if not find(provider["cmd"]):
            return self.send_json({"error": f"{provider['cmd']} is not installed. Install: {provider['install']}"})
        start = time.time()
        try:
            text = provider["ask"](req.get("prompt", ""), (req.get("model") or "").strip())
            self.send_json({"text": text, "seconds": round(time.time() - start, 1)})
        except subprocess.TimeoutExpired:
            self.send_json({"error": f"Timed out after {TIMEOUT}s"})
        except Exception as e:  # show the CLI's own error in the UI
            self.send_json({"error": str(e)})


if __name__ == "__main__":
    # Bind to localhost only, so nobody else on your network can use your subscriptions.
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"AI Council running at http://localhost:{PORT}  (Ctrl+C to stop)")
    for key, p in PROVIDERS.items():
        print(f"  {p['label']:8} {'ready' if find(p['cmd']) else 'NOT INSTALLED -> ' + p['install']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
