"""Production-ready monthly subscription service for the Cabbycreative support bot.

This service is designed to be used by customers:
- public landing page
- secure Stripe Checkout enrollment
- subscription lifecycle storage
- admin dashboard for active customers
- safe webhook verification
- health and monitoring endpoints

Environment variables:
  STRIPE_SECRET_KEY       Stripe secret key (required in production)
  STRIPE_PRICE_ID         Stripe recurring price ID (required)
  STRIPE_WEBHOOK_SECRET   Stripe webhook signing secret (required)
  APP_BASE_URL            Public HTTPS URL for redirects, default http://localhost:8080
  PORT                    HTTP port, default 8080
  HOST                    Bind host, default 0.0.0.0
  DB_PATH                 SQLite file path, default ./subscriptions.db
  ADMIN_USER              Admin username for /admin (optional but recommended)
  ADMIN_PASSWORD          Admin password for /admin (optional but recommended)
  PLAN_NAME               Display name for the product (optional)
  PLAN_PRICE_DISPLAY      Display price label (optional)
"""
import base64
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
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "subscriptions.db"))
BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8080").rstrip("/")
PLAN_NAME = os.environ.get("PLAN_NAME", "Managed support bot")
PLAN_PRICE = os.environ.get("PLAN_PRICE_DISPLAY", "$29")
MAX_BODY = 16_384
DB_LOCK = Lock()

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Cabbycreative's managed customer support bot — subscribe monthly and let customers get quick answers from your approved FAQ content.">
<title>Cabbycreative | Managed support bot</title>
<style>
:root{color-scheme:light; --bg:#f5faf6; --ink:#172a25; --muted:#596f67; --green:#176b4a; --green-dark:#0d4f39; --line:#dfeae3; --card:#ffffff; --soft:#edf7f0; --danger:#914747; --shadow:0 18px 50px rgba(17,39,32,.08)}
*{box-sizing:border-box}body{margin:0;font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--ink)}
a{color:var(--green-dark);text-decoration:none}a:hover{text-decoration:underline}main{max-width:980px;margin:0 auto;padding:28px 20px 65px}.brand{font-weight:800;letter-spacing:.04em;margin-bottom:10px}.hero{padding:34px 0 32px;max-width:680px}.eyebrow{color:var(--green);font-weight:700;text-transform:uppercase;letter-spacing:.14em;font-size:.72rem}.h1{font-size:clamp(2.5rem,6vw,4.6rem);line-height:1.04;letter-spacing:-.06em;margin:16px 0 18px}.lead{font-size:1.28rem;color:var(--muted);max-width:620px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:28px;max-width:560px;box-shadow:var(--shadow)}.card h2{margin:0 0 12px}.price{font-size:1.7rem;font-weight:800;margin:14px 0 18px}.price small{font-size:.9rem;font-weight:400;color:var(--muted)}ul{padding-left:22px;margin:0 0 18px;color:var(--muted)}li{padding:5px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px;margin:42px 0}.step{background:var(--soft);border:1px solid var(--line);border-radius:12px;padding:18px}.step strong{display:block;margin-bottom:6px}.label{display:block;margin:8px 0 6px;font-size:.92rem;font-weight:700}.input{width:100%;padding:12px 13px;border:1px solid var(--line);border-radius:10px;font:inherit;background:#fff;color:var(--ink)}.actions{display:flex;gap:12px;align-items:center}.btn{display:inline-block;border:0;border-radius:10px;background:var(--green);color:#fff;padding:13px 18px;font:inherit;font-weight:700;cursor:pointer;width:100%}.btn:disabled{opacity:.72;cursor:wait}.status{min-height:1.4em; margin-top:12px; color:var(--danger); font-size:.94rem}.fine{font-size:.85rem;color:var(--muted);margin-top:12px}.footer{padding-top:18px;border-top:1px solid var(--line);margin-top:24px;color:var(--muted);font-size:.94rem}.notice{background:var(--soft);border:1px solid var(--line);padding:14px;border-radius:12px;margin-bottom:18px;color:var(--ink)}.muted{color:var(--muted)}@media(max-width:640px){.card{padding:22px}} </style>
</head>
<body>
<main>
<div class="brand">cabbycreative</div>
<section class="hero">
<div class="eyebrow">A calmer way to handle FAQs</div>
<h1 class="h1">Give customers instant answers — without hiring extra support staff.</h1>
<p class="lead">A managed support bot answers common questions from your website, marketing pages and service details, and routes uncertain cases to your team. Simple monthly billing, no extra complexity.</p>
</section>

<section class="card" aria-labelledby="plan-title">
<h2 id="plan-title">Managed support bot</h2>
<p class="muted">For small businesses that want faster customer service and fewer repeated questions.</p>
<div class="price">__PRICE__ <small>/ month</small></div>
<ul>
<li>Answers approved questions from your business FAQ</li>
<li>Escalates unclear questions instead of guessing</li>
<li>Secure recurring billing through Stripe</li>
</ul>
<form id="checkout">
<label class="label" for="company_name">Business name</label>
<input id="company_name" name="company_name" class="input" type="text" maxlength="120" placeholder="Your business name" required>

<label class="label" for="email">Business email</label>
<input id="email" name="email" class="input" type="email" autocomplete="email" maxlength="254" placeholder="you@example.com" required>

<label class="label" for="website">Website URL</label>
<input id="website" name="website" class="input" type="url" maxlength="255" placeholder="https://yourwebsite.com">

<div class="actions" style="margin-top:16px">
<button id="submit" type="submit" class="btn">Start subscription</button>
</div>
<div id="status" class="status" role="status" aria-live="polite"></div>
</form>
<p class="fine">Secure payment is handled by Stripe. No card details are stored on this server.</p>
</section>

<section class="grid">
<div class="step"><strong>1. Subscribe securely</strong>Stripe handles payment and recurring billing.</div>
<div class="step"><strong>2. Prepare FAQ content</strong>We help you define the approved answers and support channels.</div>
<div class="step"><strong>3. Serve customers faster</strong>Common questions get quick responses; edge cases are escalated to a person.</div>
</section>

<div class="footer">Need help? Contact cabbycreative on WhatsApp at +254112937903 or visit <a href="https://cabbycreative.page.gd">cabbycreative.page.gd</a>.</div>
</main>
<script>
const form = document.getElementById('checkout');
const button = document.getElementById('submit');
const status = document.getElementById('status');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  button.disabled = true;
  status.textContent = 'Opening secure checkout...';
  try {
    const payload = {
      company_name: document.getElementById('company_name').value.trim(),
      email: document.getElementById('email').value.trim(),
      website: document.getElementById('website').value.trim()
    };
    const response = await fetch('/create-checkout-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok || !data.url) {
      throw new Error(data.error || 'Checkout is unavailable. Please try again.');
    }
    window.location.assign(data.url);
  } catch (error) {
    status.textContent = error.message;
    button.disabled = false;
  }
});
</script>
</body>
</html>
"""


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
            customer_id TEXT PRIMARY KEY,
            email TEXT NOT NULL DEFAULT '',
            company_name TEXT NOT NULL DEFAULT '',
            website_url TEXT NOT NULL DEFAULT '',
            subscription_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL)""")
        db.execute("CREATE TABLE IF NOT EXISTS stripe_events (event_id TEXT PRIMARY KEY, processed_at INTEGER NOT NULL)")


def _validate_email(value):
    return isinstance(value, str) and len(value) <= 254 and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value.strip()))


def _validate_url(value):
    if not value:
        return ""
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Website URL must start with http:// or https:// and include a domain name.")
    return value[:255]


def stripe_request(fields):
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key or not os.environ.get("STRIPE_PRICE_ID"):
        raise RuntimeError("Billing has not been configured yet. Set STRIPE_SECRET_KEY and STRIPE_PRICE_ID before launching payments.")

    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        "https://api.stripe.com/v1/checkout/sessions",
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read(65_536))
    except urllib.error.HTTPError as error:
        raise RuntimeError("Stripe could not start checkout. Please try again later.") from error


def verify_signature(raw, header):
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret or not header:
        return False

    parts = {}
    for item in header.split(","):
        key, sep, value = item.partition("=")
        if sep:
            parts.setdefault(key.strip(), []).append(value.strip())

    try:
        timestamp = int(parts["t"][0])
    except (KeyError, ValueError, IndexError):
        return False

    if abs(int(time.time()) - timestamp) > 300:
        return False

    signed = str(timestamp).encode() + b"." + raw
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in parts.get("v1", []))


def _upsert_subscription(db, customer_id, email, company_name, website_url, subscription_id, status):
    timestamp = int(time.time())
    db.execute(
        """INSERT INTO subscriptions(customer_id,email,company_name,website_url,subscription_id,status,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(customer_id) DO UPDATE SET
            email=excluded.email,
            company_name=excluded.company_name,
            website_url=excluded.website_url,
            subscription_id=excluded.subscription_id,
            status=excluded.status,
            updated_at=excluded.updated_at""",
        (customer_id, email, company_name, website_url, subscription_id, status, timestamp, timestamp),
    )


def process_event(event):
    event_id = event.get("id")
    event_type = (event.get("type") or "").strip()
    obj = (event.get("data") or {}).get("object") or {}
    if not event_id:
        raise ValueError("Missing Stripe event id")

    with DB_LOCK, sqlite3.connect(DB_PATH) as db:
        if db.execute("SELECT 1 FROM stripe_events WHERE event_id=?", (event_id,)).fetchone():
            return

        metadata = obj.get("metadata") or {}
        customer_id = obj.get("customer") or metadata.get("customer_id") or ""
        subscription_id = obj.get("subscription") or obj.get("id") or ""
        email = ((obj.get("customer_details") or {}).get("email") or obj.get("customer_email") or metadata.get("email") or "")[:254]
        company_name = (metadata.get("company_name") or "")[:120]
        website_url = (metadata.get("website_url") or "")[:255]

        if event_type == "checkout.session.completed":
            if customer_id and subscription_id:
                _upsert_subscription(db, customer_id, email, company_name, website_url, subscription_id, "active")
        elif event_type.startswith("customer.subscription."):
            status = (obj.get("status") or "unknown")
            if customer_id and subscription_id:
                existing = db.execute("SELECT email, company_name, website_url FROM subscriptions WHERE customer_id=?", (customer_id,)).fetchone()
                email = email or (existing[0] if existing else "")
                company_name = company_name or (existing[1] if existing else "")
                website_url = website_url or (existing[2] if existing else "")
                _upsert_subscription(db, customer_id, email, company_name, website_url, subscription_id, status)

        db.execute("INSERT INTO stripe_events(event_id,processed_at) VALUES(?,?)", (event_id, int(time.time())))


def _admin_ok(headers):
    user = os.environ.get("ADMIN_USER", "admin")
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        return False
    auth = headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
    except Exception:
        return False
    username, sep, provided_password = decoded.partition(":")
    return sep and hmac.compare_digest(username, user) and hmac.compare_digest(provided_password, password)


def _admin_html():
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT customer_id, email, company_name, website_url, subscription_id, status, updated_at FROM subscriptions ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()

    html_lines = [
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\"><title>Cabbycreative Admin</title>",
        "<style>body{font:16px system-ui,sans-serif;max-width:1080px;margin:auto;padding:30px;}table{border-collapse:collapse;width:100%;margin-top:20px}th,td{padding:10px 12px;border-bottom:1px solid #dfeae3;text-align:left;vertical-align:top}th{background:#edf7f0}small{color:#596f67}.badge{padding:4px 8px;border-radius:999px;font-size:12px;font-weight:700;background:#edf7f0;color:#176b4a}body{color:#172a25}</style>",
        "</head><body>",
        "<h1>Cabbycreative customer subscriptions</h1>",
        "<p><small>Only active subscriptions are considered live customer accounts.</small></p>",
    ]
    if not rows:
        html_lines.append("<p>No subscriptions yet.</p>")
    else:
        html_lines.append("<table><thead><tr><th>Customer</th><th>Email</th><th>Business</th><th>Website</th><th>Subscription</th><th>Status</th><th>Updated</th></tr></thead><tbody>")
        for customer_id, email, company_name, website_url, subscription_id, status, updated_at in rows:
            updated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(updated_at))
            html_lines.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><span class='badge'>%s</span></td><td>%s</td></tr>" % (
                    customer_id,
                    email,
                    company_name,
                    website_url,
                    subscription_id,
                    status,
                    updated,
                )
            )
        html_lines.append("</tbody></table>")
    html_lines.append("</body></html>")
    return "".join(html_lines)


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

    def send_html(self, status, html_content):
        body = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Cache-Control", "no-store")
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
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
            return

        if self.path == "/admin":
            if not _admin_ok(self.headers):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="admin"')
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Authentication required")
                return
            self.send_html(200, _admin_html())
            return

        if self.path in ("/", "/success", "/cancel"):
            page = PAGE.replace("__PRICE__", PLAN_PRICE)
            if self.path == "/success":
                page = page.replace(
                    "<section class=\"card\" aria-labelledby=\"plan-title\">",
                    "<div class=\"notice\">Your subscription is being processed. Stripe has confirmed the checkout and we will finalise your onboarding as soon as the payment is verified.</div><section class=\"card\" aria-labelledby=\"plan-title\">",
                    1,
                )
            elif self.path == "/cancel":
                page = page.replace(
                    "<section class=\"card\" aria-labelledby=\"plan-title\">",
                    "<div class=\"notice\">Subscription canceled. No charge was made, and you can subscribe at any time.</div><section class=\"card\" aria-labelledby=\"plan-title\">",
                    1,
                )
            self.send_html(200, page)
            return

        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        raw = self.read_body()
        if raw is None:
            return self.send_json(413, {"error": "Invalid or oversized request body."})

        if self.path == "/create-checkout-session":
            try:
                data = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                return self.send_json(400, {"error": "Invalid request."})

            email = (data.get("email") or "").strip()
            company_name = (data.get("company_name") or "").strip()[:120]
            website = (data.get("website") or "").strip()

            if not _validate_email(email):
                return self.send_json(400, {"error": "Enter a valid business email address."})
            if not company_name:
                return self.send_json(400, {"error": "Please enter your business name."})
            try:
                website = _validate_url(website)
            except ValueError as exc:
                return self.send_json(400, {"error": str(exc)})

            try:
                session = stripe_request({
                    "mode": "subscription",
                    "line_items[0][price]": os.environ["STRIPE_PRICE_ID"],
                    "line_items[0][quantity]": "1",
                    "customer_email": email,
                    "success_url": BASE_URL + "/success?session_id={CHECKOUT_SESSION_ID}",
                    "cancel_url": BASE_URL + "/cancel",
                    "allow_promotion_codes": "true",
                    "metadata[company_name]": company_name,
                    "metadata[website_url]": website,
                    "metadata[email]": email,
                })
            except Exception as exc:
                print("Checkout creation failed: %s" % type(exc).__name__)
                return self.send_json(503, {"error": str(exc) if isinstance(exc, RuntimeError) else "Checkout is temporarily unavailable."})

            if not session.get("url"):
                return self.send_json(503, {"error": "Stripe returned no checkout URL."})

            return self.send_json(200, {"url": session["url"]})

        if self.path == "/webhook/stripe":
            header = self.headers.get("Stripe-Signature", "")
            if not verify_signature(raw, header):
                return self.send_json(400, {"error": "Invalid webhook signature."})
            try:
                event = json.loads(raw)
                process_event(event)
            except (TypeError, ValueError, json.JSONDecodeError, sqlite3.Error):
                return self.send_json(400, {"error": "Could not process webhook event."})
            return self.send_json(200, {"received": True})

        return self.send_json(404, {"error": "Not found"})

    def do_OPTIONS(self):
        self.send_json(405, {"error": "Method not allowed"})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    print("Cabbycreative subscription checkout is live on %s:%d" % (host, port))
    ThreadingHTTPServer((host, port), Handler).serve_forever()
