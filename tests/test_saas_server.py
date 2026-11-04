import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import saas_server


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "test.db")
        self.path_patch = patch.object(saas_server, "DB_PATH", self.db_path)
        self.path_patch.start()
        saas_server.init_db()

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def test_signature_accepts_valid_payload_and_rejects_stale_or_tampered(self):
        raw = b'{"id":"evt_test"}'
        stamp = str(int(time.time()))
        secret = "whsec_test"
        signed = stamp.encode() + b"." + raw
        digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": secret}):
            self.assertTrue(saas_server.verify_signature(raw, "t=" + stamp + ",v1=" + digest))
            self.assertFalse(saas_server.verify_signature(raw + b"!", "t=" + stamp + ",v1=" + digest))
            old = str(int(time.time()) - 600)
            self.assertFalse(saas_server.verify_signature(raw, "t=" + old + ",v1=" + digest))

    def test_subscription_webhook_updates_status_and_deduplicates(self):
        event = {"id": "evt_subscription", "type": "customer.subscription.updated", "data": {"object": {
            "id": "sub_123", "customer": "cus_123", "status": "past_due"}}}
        saas_server.process_event(event)
        saas_server.process_event(event)
        import sqlite3
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT customer_id,subscription_id,status FROM subscriptions").fetchone()
            events = db.execute("SELECT COUNT(*) FROM stripe_events").fetchone()[0]
        self.assertEqual(row, ("cus_123", "sub_123", "past_due"))
        self.assertEqual(events, 1)

    def test_checkout_event_creates_subscription_record(self):
        event = {"id": "evt_checkout", "type": "checkout.session.completed", "data": {"object": {
            "customer": "cus_456", "subscription": "sub_456", "customer_details": {"email": "buyer@example.com"}}}}
        saas_server.process_event(event)
        import sqlite3
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT email,status FROM subscriptions WHERE customer_id='cus_456'").fetchone()
        self.assertEqual(row, ("buyer@example.com", "active"))


if __name__ == "__main__":
    unittest.main()
