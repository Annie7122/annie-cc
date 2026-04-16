#!/usr/bin/env python3
"""Server for the Content Ideas app — works locally and on Render."""
import http.server
import json
import os
import re
import threading
import webbrowser
from urllib.parse import urlparse

# On Render, PORT is set automatically. Locally defaults to 7842.
PORT = int(os.environ.get("PORT", 7842))
IS_CLOUD = "RENDER" in os.environ

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
HTML_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


def get_api_key():
    """Cloud: read from env var. Local: read from config.json."""
    if IS_CLOUD:
        return os.environ.get("ANTHROPIC_API_KEY", "")
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    return cfg.get("api_key", "")


def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args)

    def _send(self, code, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            with open(HTML_FILE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")

        elif path == "/config":
            key = get_api_key()
            self._send(200, json.dumps({"has_key": bool(key), "is_cloud": IS_CLOUD}))

        elif path == "/test-key":
            key = get_api_key()
            if not key:
                self._send(200, json.dumps({"ok": False, "msg": "No API key found."}))
                return
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=key)
                client.messages.create(
                    model="claude-haiku-4-5-20251001", max_tokens=10,
                    messages=[{"role": "user", "content": "hi"}]
                )
                self._send(200, json.dumps({"ok": True, "msg": "API key works!"}))
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "msg": str(e)}))
        else:
            self._send(404, b"not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        path = urlparse(self.path).path

        if path == "/config":
            if IS_CLOUD:
                self._send(200, json.dumps({"ok": True}))  # no-op on cloud
            else:
                save_config({"api_key": body.get("api_key", "")})
                self._send(200, json.dumps({"ok": True}))

        elif path == "/generate":
            api_key = get_api_key()
            if not api_key:
                self._send(401, json.dumps({"error": "No API key configured."}))
                return
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)

                custom = body.get("custom", "").strip()
                custom_line = f"\nExtra context from creator: {custom}" if custom else ""

                prompt = f"""You are a social media strategist specialising in content for women in their 20s.

Generate 6 highly specific, trend-aware content ideas for the following brief:
- Platform: {body.get('platform', 'Instagram')}
- Niche: {body.get('niche', 'Lifestyle')}
- Format: {body.get('format', 'Any format')}
- Vibe / tone: {body.get('vibe', 'Any vibe')}{custom_line}

Return ONLY a valid JSON array with exactly 6 objects. No markdown, no explanation, just the raw JSON array.
Each object must have these exact keys:
{{
  "title": "short bold video title (5-8 words)",
  "hook": "one punchy opening line spoken to camera — irresistible, conversational, scroll-stopping (max 20 words)",
  "script": [
    "Point 1 — what to say or show",
    "Point 2 — what to say or show",
    "Point 3 — what to say or show",
    "Point 4 — what to say or show",
    "Point 5 — closing line or CTA"
  ],
  "caption": "ready-to-post caption — if platform format is long-form YouTube video, write 4-5 sentences describing the video with energy and personality; otherwise write 1-2 sentences. Casual and fun tone.",
  "hashtags": ["#tag1", "#tag2", "#tag3"]
}}

Rules:
- script points must be brief cues, not paragraphs
- hook must be the very first thing said on camera
- hashtags should be specific and currently popular
- keep everything fresh and relevant to 2025-2026 trends
- NEVER use em dashes (—) anywhere in any field, use commas or full stops instead
- respond with ONLY the JSON array, no other text"""

                response = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2400,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = response.content[0].text.strip()
                print(f"[DEBUG] Claude raw response (first 500 chars):\n{text[:500]}\n---")

                # strip markdown code fences if present
                text = re.sub(r'^```[a-z]*\s*', '', text)
                text = re.sub(r'\s*```$', '', text).strip()

                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as je:
                    self._send(500, json.dumps({"error": f"JSON parse failed: {je}. Got: {text[:300]}"}))
                    return

                # accept bare array or {"ideas": [...]}
                if isinstance(parsed, list):
                    ideas = parsed
                elif isinstance(parsed, dict):
                    ideas = (parsed.get("ideas") or parsed.get("content")
                             or next(iter(parsed.values()), None))
                else:
                    ideas = None

                if not isinstance(ideas, list) or len(ideas) == 0:
                    self._send(500, json.dumps({"error": f"Unexpected response shape: {text[:300]}"}))
                    return

                self._send(200, json.dumps({"ideas": ideas}))

            except Exception as e:
                print(f"[ERROR] {e}")
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, b"not found")


def main():
    host = "0.0.0.0" if IS_CLOUD else "127.0.0.1"
    server = http.server.HTTPServer((host, PORT), Handler)
    print(f"Starting on {host}:{PORT}  (cloud={IS_CLOUD})")

    if not IS_CLOUD:
        def open_browser():
            import time; time.sleep(0.7)
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
