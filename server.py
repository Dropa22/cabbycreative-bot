"""HTTP server: chat page, /chat API, admin dashboard, optional Telegram webhook (UNTESTED until you configure it).
Secrets come ONLY from environment variables: ADMIN_USER, ADMIN_PASSWORD, SESSION_SALT, LOG_TEXT,
TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET."""
import json, os, hmac, base64, html, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from bot import Bot

BOT = Bot(log_text=os.environ.get("LOG_TEXT") == "1")
MAX_BODY = 4096
CSP = "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>cabbycreative support (demo)</title><style>body{font:16px system-ui;max-width:520px;margin:0 auto;padding:12px}
#log{height:60vh;overflow:auto;border:1px solid #ccc;border-radius:8px;padding:8px}.u{text-align:right;color:#06c}.b{margin:6px 0;white-space:pre-wrap}
input{width:75%;padding:8px}button{padding:8px}</style><h3>cabbycreative support <small>(simulated demo)</small></h3><div id=log></div>
<p><input id=m maxlength=500 placeholder="Type your question"><button id=s>Send</button></p><p><small>Never share passwords, PINs or OTPs here.</small></p>
<script>const sid=(crypto.randomUUID?crypto.randomUUID():String(Math.random()));const log=document.getElementById('log');
function add(t,c){const d=document.createElement('div');d.className='b '+c;d.textContent=t;log.appendChild(d);log.scrollTop=log.scrollHeight}
async function send(){const i=document.getElementById('m');const t=i.value.trim();if(!t)return;i.value='';add(t,'u');
try{const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:sid,message:t})});
const j=await r.json();add(j.reply,'b')}catch(e){add('Service unavailable. Please contact us on WhatsApp +254112937903.','b')}}
document.getElementById('s').onclick=send;document.getElementById('m').onkeydown=e=>{if(e.key==='Enter')send()};add('Hello! Ask me about our services.','b')</script>"""

class H(BaseHTTPRequestHandler):
    server_version = "cc"
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Content-Security-Policy", CSP); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return None if n > MAX_BODY else self.rfile.read(n)
    def _admin_ok(self):
        pw = os.environ.get("ADMIN_PASSWORD")
        if not pw: return False
        try: u, _, p = base64.b64decode(self.headers.get("Authorization", "")[6:]).decode().partition(":")
        except Exception: return False
        return hmac.compare_digest(u, os.environ.get("ADMIN_USER", "admin")) & hmac.compare_digest(p, pw)
    def do_GET(self):
        if self.path == "/health": return self._send(200, "ok", "text/plain")
        if self.path == "/": return self._send(200, PAGE)
        if self.path.startswith("/admin"):
            if not self._admin_ok(): return self._send(401, "Authentication required", "text/plain", {"WWW-Authenticate": 'Basic realm="admin"'})
            s, e = BOT.stats(), html.escape
            rows = lambda L: "".join("<tr><td>%s</td><td>%s</td></tr>" % (e(str(a)), e(str(b))) for a, b in L) or "<tr><td colspan=2>none</td></tr>"
            pct = lambda a: "%.0f%%" % (100 * a / s["total"]) if s["total"] else "n/a"
            return self._send(200, "<!doctype html><meta charset=utf-8><title>Admin</title><style>body{font:15px system-ui;max-width:700px;margin:auto;padding:12px}td{padding:2px 12px;border-bottom:1px solid #ddd}</style>"
              "<h2>Last 7 days</h2><p>Messages: %d | Sessions: %d | Resolved: %s | Escalated: %s | Avg response: %s ms</p>"
              "<h3>Intents</h3><table>%s</table><h3>Channels</h3><table>%s</table><h3>Unanswered questions (redacted, only if LOG_TEXT=1)</h3><table>%s</table>"
              % (s["total"], s["sessions"], pct(s["resolved"]), pct(s["escalated"]), s["avg_ms"], rows(s["by_intent"]), rows(s["by_channel"]), rows(s["misses"])))
        self._send(404, "Not found", "text/plain")
    def do_POST(self):
        raw = self._body()
        if raw is None: return self._send(413, "Too large", "text/plain")
        ip = self.client_address[0]
        if not BOT.allowed("ip:" + ip, limit=60):
            return self._send(429, json.dumps({"reply": "Too many requests. Please try again shortly."}), "application/json")
        if self.path == "/chat":
            try: d = json.loads(raw); r = BOT.respond(str(d.get("session", ip))[:64], d.get("message", ""), "web")
            except Exception: return self._send(400, json.dumps({"reply": "Bad request."}), "application/json")
            return self._send(200, json.dumps({"reply": r["reply"], "escalate": r["escalate"]}), "application/json")
        if self.path == "/webhook/telegram":  # NOT connected until you set the env vars AND run setWebhook, then test
            tok, sec = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_WEBHOOK_SECRET")
            if not (tok and sec): return self._send(404, "Not configured", "text/plain")
            if not hmac.compare_digest(self.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), sec): return self._send(403, "Forbidden", "text/plain")
            try:
                m = json.loads(raw).get("message") or {}
                if m.get("text") and m.get("chat", {}).get("id"):
                    r = BOT.respond("tg:%s" % m["chat"]["id"], m["text"], "telegram")
                    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % tok,
                        json.dumps({"chat_id": m["chat"]["id"], "text": r["reply"]}).encode(), {"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=10)
            except Exception: pass
            return self._send(200, "ok", "text/plain")
        self._send(404, "Not found", "text/plain")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print("Simulation running on port %d (no real channel is connected)" % port)
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), port), H).serve_forever()
