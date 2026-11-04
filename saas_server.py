"""Minimal Stripe-powered monthly subscription checkout for the cabbycreative support bot.

Run with: python saas_server.py
Required environment: STRIPE_SECRET_KEY, STRIPE_PRICE_ID, STRIPE_WEBHOOK_SECRET
Optional: APP_BASE_URL, PORT, DB_PATH, PLAN_NAME, PLAN_PRICE_DISPLAY
"""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "subscriptions.db"))
BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8080").rstrip("/")
PLAN_NAME = os.environ.get("PLAN_NAME", "Managed website support bot")
PLAN_PRICE = os.environ.get("PLAN_PRICE_DISPLAY", "Monthly subscription")
MAX_BODY = 16_384
DB_LOCK = Lock()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Give customers quick answers to common questions with a managed support bot.">
<title>Cabbycreative — customer support, handled</title>
<style>
:root{color-scheme:light;--ink:#17231e;--muted:#5e6d65;--green:#176b4a;--pale:#edf5ef;--line:#dce7df}
*{box-sizing:border-box}body{margin:0;background:#f8faf8;color:var(--ink);font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:auto;padding:28px 22px 70px}.brand{font-weight:750;letter-spacing:.02em}.hero{padding:76px 0 52px;max-width:720px}.eyebrow{color:var(--green);font-weight:700;text-transform:uppercase;letter-spacing:.12em;font-size:.78rem}h1{font-size:clamp(2.5rem,7vw,4.7rem);line-height:1.04;letter-spacing:-.05em;margin:15px 0 20px}.lead{font-size:1.2rem;color:var(--muted);max-width:620px}.card{background:white;border:1px solid var(--line);border-radius:18px;padding:28px;max-width:560px;box-shadow:0 12px 38px #12331b0b}.card h2{margin:0 0 8px}.price{font-size:1.5rem;font-weight:750;margin:20px 0}.price small{font-size:.9rem;font-weight:400;color:var(--muted)}ul{padding-left:22px;color:var(--muted)}li{padding:5px 0}button{border:0;border-radius:9px;background:var(--green);color:white;padding:13px 20px;font:inherit;font-weight:700;cursor:pointer;width:100%}button:disabled{opacity:.65;cursor:wait}.fine{font-size:.85rem;color:var(--muted);margin:12px 0 0}.status{margin-top:14px;color:#a52a2a;min-height:1.5em}.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px;margin:42px 0}.step{background:var(--pale);border-radius:12px;padding:18px}.step strong{display:block;margin-bottom:5px}footer{border-top:1px solid var(--line);padding-top:20px;color:var(--muted);font-size:.9rem}
</style></head><body><main>
<div class="brand">cabbycreative</div>
<section class="hero"><div class="eyebrow">A calmer way to handle FAQs</div><h1>Stop answering the same customer questions.</h1><p class="lead">A managed support bot gives your customers quick answers about your services, while handing uncertain questions to your team. Start a monthly subscription below.</p></section>
<section class="card" aria-labelledby="plan-title"><h2 id="plan-title">Managed support bot</h2><p>Answers common questions using information approved by your business. If it doesn't know, it says so and points customers to your team.</p><div class="price">__PRICE__ <small>/ month, billed by Stripe</small></div><ul><li>Answers based on your approved FAQ content</li><li>Unanswered questions are escalated instead of guessed</li><li>Secure recurring billing; cancel through Stripe</li></ul><form id="checkout"><label for="email">Business email</label><p><input id="email" name="email" type="email" autocomplete="email" required maxlength="254" placeholder="you@example.com" style="width:100%;padding:12px;border:1px solid var(--line);border-radius:8px;font:inherit"></p><button id="submit" type="submit">Subscribe monthly</button><div class="status" id="status" role="status" aria-live="polite"></div></form><p class="fine">Your payment details are entered on Stripe's secure checkout page. Subscription pricing and terms are shown there before payment.</p></section>
<section class="steps"><div class="step"><strong>1. Subscribe securely</strong>Stripe handles checkout and recurring payments.</div><div class="step"><strong>2. Share your FAQs</strong>Our team helps prepare approved answers for your business.</div><div class="step"><strong>3. Help customers faster</strong>Common questions get quick answers; edge cases reach a person.</div></section>
<footer>Questions? Contact cabbycreative on WhatsApp at +254112937903 or visit <a href="https://cabbycreative.page.gd">our website</a>.</footer></main>
<script>
const form=document.getElementById('checkout'),button=document.getElementById('submit'),status=document.getElementById('status');
form.addEventListener('submit',async event=>{event.preventDefault();button.disabled=true;status.textContent='Opening secure checkout…';try{const response=await fetch('/create-checkout-session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('email').value})});const data=await response.json();if(!response.ok||!data.url)throw new Error(data.error||'Checkout is unavailable. Please try again.');location.assign(data.url)}catch(error){status.textContent=error.message;button.disabled=false}});
</script></body></html>"""


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
            customer_id TEXT PRIMARY KEY, email TEXT NOT NULL DEFAULT '',
            subscription_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
            updated_at INTEGER NOT NULL)""")
        db.execute("CREATE TABLE IF NOT EXISTS stripe_events (event_id TEXT PRIMARY KEY, processed_at INTEGER NOT NULL)")


def stripe_request(fields):
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key or not os.environ.get("STRIPE_PRICE_ID"):
        raise RuntimeError("Billing is not configured yet.")
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request("https://api.stripe.com/v1/checkout/sessions", data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read(65_536))
    except urllib.error.HTTPError as error:
        # Do not expose Stripe's response or secret-bearing request details to the client.
        raise RuntimeError("Stripe could not start checkout. Please try again later.") from error


def verify_signature(raw, header):
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret or not header:
        return False
    parts = {}
    for item in header.split(","):
        key, sep, value = item.partition("=")
        if sep:
            parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    if abs(int(time.time()) - timestamp) > 300:
        return False
    signed = str(timestamp).encode() + b"." + raw
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in parts.get("v1", []))


def process_event(event):
    event_id = event.get("id")
    obj = (event.get("data") or {}).get("object") or {}
    event_type = event.get("type", "")
    if not event_id:
        raise ValueError("Missing Stripe event id")
    with DB_LOCK, sqlite3.connect(DB_PATH) as db:
        if db.execute("SELECT 1 FROM stripe_events WHERE event_id=?", (event_id,)).fetchone():
            return
        if event_type == "checkout.session.completed":
            customer = obj.get("customer")
            subscription = obj.get("subscription")
            email = ((obj.get("customer_details") or {}).get("email") or obj.get("customer_email") or "")[:254]
            if customer and subscription:
                db.execute("""INSERT INTO subscriptions(customer_id,email,subscription_id,status,updated_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET email=excluded.email,
                    subscription_id=excluded.subscription_id,status=excluded.status,updated_at=excluded.updated_at""",
                    (customer, email, subscription, "active", int(time.time())))
        elif event_type.startswith("customer.subscription."):
            customer = obj.get("customer")
            subscription = obj.get("id", "")
            status = obj.get("status", "unknown")
            if customer and subscription:
                existing = db.execute("SELECT email FROM subscriptions WHERE customer_id=?", (customer,)).fetchone()
                email = existing[0] if existing else ""
                db.execute("""INSERT INTO subscriptions(customer_id,email,subscription_id,status,updated_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET email=excluded.email,
                    subscription_id=excluded.subscription_id,status=excluded.status,updated_at=excluded.updated_at""",
                    (customer, email, subscription, status, int(time.time())))
        db.execute("INSERT INTO stripe_events(event_id,processed_at) VALUES(?,?)", (event_id, int(time.time())))


class Handler(BaseHTTPRequestHandler):
    server_version = "CabbycreativeBilling"
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length < 1 or length > MAX_BODY:
            return None
        return self.rfile.read(length)

    def do_GET(self):
        if self.path in ("/", "/success", "/cancel"):
            page = PAGE
            if self.path == "/success":
                page = page.replace("<section class=\"hero\">", "<p role=\"status\" style=\"background:#edf5ef;padding:14px;border-radius:8px\">Thanks! Stripe is confirming your subscription. Our team will follow up to set up your FAQ bot.</p><section class=\"hero\">")
            elif self.path == "/cancel":
                page = page.replace("<section class=\"hero\">", "<p role=\"status\" style=\"background:#edf5ef;padding:14px;border-radius:8px\">Checkout was canceled; you have not been charged. You can subscribe whenever you are ready.</p><section class=\"hero\">")
            page = page.replace("__PRICE__", PLAN_PRICE)
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        raw = self.read_body()
        if raw is None:
            return self.send_json(413, {"error": "Invalid or oversized request body."})
        if self.path == "/create-checkout-session":
            try:
                data = json.loads(raw)
                email = data.get("email", "")
                if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                    return self.send_json(400, {"error": "Enter a valid business email address."})
                session = stripe_request({
                    "mode": "subscription", "line_items[0][price]": os.environ["STRIPE_PRICE_ID"],
                    "line_items[0][quantity]": "1", "customer_email": email,
                    "success_url": BASE_URL + "/success?session_id={CHECKOUT_SESSION_ID}",
                    "cancel_url": BASE_URL + "/cancel", "allow_promotion_codes": "true",
                })
                if not session.get("url"):
                    raise RuntimeError("Stripe returned no checkout URL.")
                return self.send_json(200, {"url": session["url"]})
            except (ValueError, json.JSONDecodeError):
                return self.send_json(400, {"error": "Invalid request."})
            except Exception as error:
                print("Checkout creation failed: %s" % type(error).__name__)
                return self.send_json(503, {"error": str(error) if isinstance(error, RuntimeError) else "Checkout is temporarily unavailable."})
        if self.path == "/webhook/stripe":
            if not verify_signature(raw, self.headers.get("Stripe-Signature", "")):
                return self.send_json(400, {"error": "Invalid webhook signature."})
            try:
                event = json.loads(raw)
                process_event(event)
            except (ValueError, json.JSONDecodeError, sqlite3.Error):
                return self.send_json(400, {"error": "Could not process webhook event."})
            return self.send_json(200, {"received": True})
        return self.send_json(404, {"error": "Not found"})

    def do_OPTIONS(self):
        self.send_json(405, {"error": "Method not allowed"})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    print("Cabbycreative subscription checkout listening on %s:%d" % (host, port))
    ThreadingHTTPServer((host, port), Handler).serve_forever()
