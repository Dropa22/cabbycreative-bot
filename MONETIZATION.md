# Monthly subscription MVP

This repository's initial product is a **managed FAQ support bot for small businesses**: customers get fast answers to common questions, and questions without an approved answer are routed to the business's human support channels. `bot.py` contains the retrieval-only answer engine. `saas_server.py` adds a public subscription landing page and Stripe Checkout for recurring monthly billing.

## Configure Stripe

1. In Stripe, create a **recurring monthly Price** for the managed support bot. Copy its `price_...` ID.
2. Set the environment variables below. Keep secret keys out of source control.
3. Configure a Stripe webhook endpoint at `https://YOUR_DOMAIN/webhook/stripe` for `checkout.session.completed` and `customer.subscription.created`, `customer.subscription.updated`, and `customer.subscription.deleted`. Set `STRIPE_WEBHOOK_SECRET` to the endpoint's signing secret.
4. Deploy behind HTTPS, use a persistent disk for the SQLite database, then run `python saas_server.py`.
5. Test checkout and subscription cancellation in Stripe test mode before switching to live keys.

```sh
export STRIPE_SECRET_KEY=sk_test_...
export STRIPE_PRICE_ID=price_...
export STRIPE_WEBHOOK_SECRET=whsec_...
export APP_BASE_URL=https://YOUR_DOMAIN
export PLAN_PRICE_DISPLAY='$29'
python saas_server.py
```

`APP_BASE_URL` must be the public HTTPS origin in production. `PLAN_PRICE_DISPLAY` is only display text; Stripe's recurring Price controls the real amount and billing interval. `DB_PATH` can point to a persistent SQLite file. The webhook records subscription status and deduplicates event delivery; subscriptions are in the `subscriptions` table. Card data is handled by Stripe, never this server.

## Run locally

```sh
python -m unittest discover -s tests
python saas_server.py
```

Open `http://localhost:8080`. Without Stripe credentials the landing page and `/health` still work, while checkout returns a configuration error. Use Stripe CLI forwarding to test webhooks locally. The success page is informational; fulfillment should be driven by verified webhook events, not by the browser redirect.

## MVP operations

This first cut automates recurring checkout and tracks subscription lifecycle. It does **not** yet provide customer login, self-service billing portal, or automatic tenant-specific bot setup; the team follows up after a successful checkout to configure approved FAQs and channels. Add those before promising instant, self-serve onboarding. Run on a production WSGI/serverless platform or behind a TLS reverse proxy, configure backups for the SQLite database, and monitor Stripe webhook delivery.
