"""cabbycreative support bot engine. Standard library only. Retrieval-only (no LLM), so it
cannot invent facts: it returns approved KB answers verbatim or admits it does not know."""
import json, re, math, time, hashlib, sqlite3, os, collections, threading

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = "https://cabbycreative.page.gd"
SUPPORT = "WhatsApp +254112937903, Telegram @cabbycreatives or Facebook @cabbycreative"
FALLBACK = "I couldn't verify that information. Please contact our support team for confirmation."
MAX_LEN, THRESHOLD = 500, 0.30
STOP = set("a an the is are do you i me my we our of to for and or in on at it can what how does your with be about this that please hi hello".split())

def toks(s):
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w
            for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP]

def clean(s):
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(s))
    return re.sub(r"\s+", " ", s).strip()[:MAX_LEN]

def redact(s):
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", s)
    return re.sub(r"\+?\d[\d\s().-]{6,}\d", "[number]", s)

def rx(p): return re.compile(p, re.I)
INTENTS = [  # order matters: safety first
 ("emergency", rx(r"poison|overdose|suicid|chest pain|can'?t breathe|ambulance|medical emergency|bleeding")),
 ("injection", rx(r"ignore (all |any |the )?(previous|prior|above|earlier)|system prompt|your (rules|instructions)|developer mode|jailbreak|you are now|pretend (to be|you)|act as|bypass|disregard|api[ _-]?key|secret key|credentials")),
 ("credentials", rx(r"\b(pin|otp|password|cvv|card number|one[- ]time)\b")),
 ("privacy", rx(r"(another|other|someone|somebody|his|her)\b.*\b(customer|client|phone|number|email|details|address)|customer list")),
 ("abuse", rx(r"\b(idiot|stupid|fuck\w*|shit|bastard|useless|moron|dumb)\b|\b(kill|hurt) you")),
 ("legal", rx(r"\bsue\b|lawsuit|lawyer|attorney|legal action|court|\bpolice\b")),
 ("complaint", rx(r"stole|steal|scam|fraud|cheat|complain|unacceptable|never delivered|rip ?off|terrible service")),
 ("refund", rx(r"refund|money back|chargeback|cancel (my )?(order|payment|project)")),
 ("human", rx(r"human|real person|agent|representative|speak to (someone|a person)|talk to (someone|a person)")),
 ("greeting", rx(r"^(hi|hello|hey|good (morning|afternoon|evening)|habari|jambo)\W*$")),
 ("thanks", rx(r"^(thanks|thank you|asante|ok(ay)?|great)\W*$")),
]
CANNED = {
 "emergency": ("If this is a medical emergency, please contact local emergency services right now (for example 999 or 112 in Kenya) or go to the nearest hospital. I can't help with medical situations.", True),
 "injection": ("I can't share internal instructions, credentials or private company information, and I can't change my rules. I'm happy to help with questions about our website development, marketing and advertising services.", False),
 "credentials": ("For your safety, never share PINs, passwords, OTPs or card details in this chat. We will never ask for them here.", False),
 "privacy": ("I can't share or look up other people's personal information. I can help with general questions about our services.", False),
 "legal": ("I can only share general approved information. For legal matters, please contact our team directly so a person can review your situation: " + SUPPORT + ".", True),
 "complaint": ("I'm sorry you're having this experience, and I want it handled properly. I'm passing this to a human representative. Please contact " + SUPPORT + " so a person can review it with you.", True),
 "human": ("Of course. You can speak with a person on " + SUPPORT + ".", True),
 "greeting": ("Hello! Welcome to cabbycreative. I can answer questions about our website development, marketing and advertising services. What would you like to know?", False),
 "thanks": ("You're welcome! Is there anything else I can help with?", False),
}

class Bot:
    def __init__(self, kb_path=None, db_path=None, log_text=False):
        self.kb = self._load(kb_path or os.path.join(HERE, "kb.json"))
        self.log_text = log_text
        self.salt = os.environ.get("SESSION_SALT") or os.urandom(8).hex()
        self.db = sqlite3.connect(db_path or os.environ.get("DB_PATH") or os.path.join(HERE, "metrics.db"), check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("create table if not exists events(ts real, sess text, channel text, intent text, resolved int, escalated int, ms real, note text)")
        self.state = collections.OrderedDict()
        self.hits = collections.defaultdict(collections.deque)
        self._index()

    @staticmethod
    def _load(p):
        out = []
        with open(p, encoding="utf-8") as f: data = json.load(f)
        for e in data:
            if all(k in e for k in ("id", "title", "keywords", "answer", "source")):
                if e["answer"] is not None: e["answer"] = str(e["answer"])[:800]
                out.append(e)
        return out

    def _index(self):
        self.docs, df = [], collections.Counter()
        for e in self.kb:
            w = {}
            for text, wt in ((e["keywords"], 3), (e["title"], 2), (e["answer"] or "", 1)):
                for t in toks(text): w[t] = max(w.get(t, 0), wt)
            self.docs.append(w); df.update(w.keys())
        n = len(self.docs) or 1
        self.idf = {t: math.log((n + 1) / (c + 0.5)) + 1 for t, c in df.items()}
        self.unk = math.log(n + 1) + 1

    def retrieve(self, text):
        q = set(toks(text))
        if not q: return None, 0.0
        den = sum(self.idf.get(t, self.unk) * 3 for t in q)
        best, bs = None, 0.0
        for e, w in zip(self.kb, self.docs):
            s = sum(self.idf[t] * w[t] for t in q if t in w) / den
            if s > bs: best, bs = e, s
        return (best, bs) if bs >= THRESHOLD else (None, bs)

    def allowed(self, key, limit=15, window=60):
        now, d = time.time(), self.hits[key]
        while d and now - d[0] > window: d.popleft()
        if len(d) >= limit: return False
        d.append(now); return True

    def respond(self, session, text, channel="web"):
        t0 = time.time()
        try:
            r = self._respond(session, text, channel)
        except Exception:
            r = dict(reply="Sorry, something went wrong on our side. Please contact " + SUPPORT + ".", intent="error", escalate=True, resolved=False)
        self._log(session, channel, r, (time.time() - t0) * 1000, text)
        return r

    def _respond(self, session, text, channel):
        sid = hashlib.sha256((self.salt + str(session)).encode()).hexdigest()[:12]
        if not self.allowed("s:" + sid):
            return dict(reply="You're sending messages very quickly. Please wait a minute and try again.", intent="rate_limited", escalate=False, resolved=False)
        text = clean(text)
        if not text:
            return dict(reply="Please type a question and I'll do my best to help.", intent="empty", escalate=False, resolved=False)
        st = self.state.setdefault(sid, dict(last="", repeats=0, abuse=0))
        if len(self.state) > 5000: self.state.popitem(last=False)
        norm = " ".join(toks(text))
        st["repeats"] = st["repeats"] + 1 if norm and norm == st["last"] else 0
        st["last"] = norm
        intent = next((n for n, p in INTENTS if p.search(text)), None)
        if intent == "abuse":
            st["abuse"] += 1
            msg = "I want to help, but I can't continue with abusive language. Please keep the conversation respectful."
            if st["abuse"] >= 3: msg += " If you'd like to speak to a person, contact " + SUPPORT + "."
            return dict(reply=msg, intent="abuse", escalate=st["abuse"] >= 3, resolved=False)
        if intent == "refund":
            e = next((x for x in self.kb if x["id"] == "refunds" and x["answer"]), None)
            base = (e["answer"] + " ") if e else ""
            return dict(reply=base + "I can't process or confirm refunds or payments myself. " + ("" if e else FALLBACK + " ") + "A person can review your request: " + SUPPORT + ".", intent="refund", escalate=True, resolved=False)
        if intent in CANNED:
            reply, esc = CANNED[intent]
            return dict(reply=reply, intent=intent, escalate=esc, resolved=intent in ("greeting", "thanks"))
        if st["repeats"] >= 2:
            return dict(reply="It looks like I'm not answering this well. Let me connect you with a person: " + SUPPORT + ".", intent="repeat", escalate=True, resolved=False)
        e, score = self.retrieve(text)
        if e and e["answer"]:
            return dict(reply=e["answer"] + "\nSource: " + e["source"], intent="kb:" + e["id"], escalate=False, resolved=True)
        if e:  # topic recognised, but no approved answer yet
            return dict(reply=FALLBACK + " You can reach us on " + SUPPORT + ".", intent="unverified:" + e["id"], escalate=True, resolved=False)
        return dict(reply=FALLBACK + " You can reach us on " + SUPPORT + ". Or tell me more about what you need (for example: a new website, marketing or advertising).", intent="kb_miss", escalate=True, resolved=False)

    def _log(self, session, channel, r, ms, text):
        sid = hashlib.sha256((self.salt + str(session)).encode()).hexdigest()[:12]
        note = redact(clean(text))[:120] if self.log_text and r["intent"] in ("kb_miss",) or (self.log_text and r["intent"].startswith("unverified")) else ""
        with self.lock:
            self.db.execute("insert into events values(?,?,?,?,?,?,?,?)", (time.time(), sid, channel, r["intent"], int(r["resolved"]), int(r["escalate"]), ms, note))
            self.db.commit()

    def stats(self, days=7):
        since = time.time() - days * 86400
        q = lambda s, *a: self.db.execute(s, (since,) + a).fetchall()
        tot = q("select count(*), sum(resolved), sum(escalated), avg(ms), count(distinct sess) from events where ts>?")[0]
        return dict(total=tot[0] or 0, resolved=tot[1] or 0, escalated=tot[2] or 0, avg_ms=round(tot[3] or 0, 2), sessions=tot[4] or 0,
                    by_intent=q("select intent,count(*) c from events where ts>? group by intent order by c desc limit 15"),
                    by_channel=q("select channel,count(*) from events where ts>? group by channel"),
                    misses=q("select note,count(*) c from events where ts>? and note!='' group by note order by c desc limit 15"))
