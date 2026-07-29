# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Korean-language FastAPI chatbot for **현대자동화/현대기전사** (a FA/industrial-automation parts reseller on Naver Smartstore). It answers customer questions about discontinued-product replacements, specs, alarm codes, stock, and store location, and pushes low-stock alerts to the admin. It's deployed as a single small service (Render), not a multi-service architecture — keep changes proportional to that.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env         # fill in CLOVA_API_KEY at minimum
uvicorn app.main:app --reload --port 8000
# API docs: http://localhost:8000/docs
# Built-in web chat UI: http://localhost:8000/chat
```

There is no build step, linter, or formatter configured (no `pyproject.toml`/`ruff`/`black`), and **no test framework** — files named `test_*.py` at the repo root are standalone scripts (not pytest), run directly and exercised against whatever `DATABASE_URL` is active:

```bash
python test_inventory.py           # exercises get_inventory_status() against real/dev DB
python test_stock_notify_flow.py
python test_commerce_api.py
```

When adding verification for a change, follow this existing pattern (a small `asyncio.run(main())` script that calls the service function directly) rather than introducing pytest.

## Configuration (`app/config.py`)

Settings load from `.env` via `pydantic-settings` with `extra="ignore"` (unknown `.env` keys are silently ignored rather than erroring — intentional, since `.env` accumulates keys for features that are toggled off). There are **two entirely separate Naver credential pairs** — do not conflate them:
- `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET` — Naver **Search** OpenAPI, used for web-search fallback (`app/services/web_search.py`) and competitor pricing lookups.
- `NAVER_COMMERCE_CLIENT_ID`/`NAVER_COMMERCE_CLIENT_SECRET` — Naver **Commerce** API (OAuth2 + bcrypt-signed), used for live stock (`app/services/naver_commerce.py`). Gated by `NAVER_COMMERCE_ENABLED`.

`DATABASE_URL` determines runtime mode: a `sqlite+aiosqlite://` URL means local/dev; anything else (MariaDB via `asyncmy` in production) means Render mode. `app/main.py` checks this (`IS_LOCAL`) to decide whether to mount the manual-PDF-upload router at all — it's disabled in Render mode to save memory, so manuals are processed locally and the resulting DB rows/vector data are what actually ships.

## Request flow / architecture

**Two channel entrypoints funnel into one router.** `app/api/chatbot.py` (`/api/chat/`, used by the built-in webchat UI and direct API callers) and `app/api/talktalk.py` (`/api/talktalk/webhook`, Naver TalkTalk webhook) both end up calling the same `_route(intent_result, message, db)` function defined in `chatbot.py` — `talktalk.py` imports it lazily inside a function to avoid a circular import. **If you change intent routing, change it once in `chatbot._route`** and both channels pick it up.

**Admin commands bypass intent classification entirely.** A message starting with `대체품등록[` is recognized by `app/core/admin_commands.is_admin_command()` before `classify_intent()` ever runs, checked against `ADMIN_COMMAND_KEY` from `.env`, and handled separately in both `chatbot.py` and `talktalk.py`. This is the mechanism non-technical admins use to register replacement-product mappings by typing a command into the chat itself.

**Intent classification (`app/core/intent.py`) is regex/keyword-based, not ML**, and the check order matters: exact `IG5A_ALARM_CODES` list match (highest priority, manual-verified) → generic alarm regexes → spec-search trigger (voltage+kW) → servo-capacity trigger (watts) → keyword scoring → bare model-name-only fallback → general. When adding a new product family's alarm codes or model patterns, add to the relevant list/regex here rather than adding new branching logic elsewhere.

**The DB → web-search → LLM-knowledge fallback chain is the core pattern**, repeated across replacement/specs/alarm intents in `chatbot._route`: try the DB-backed service function first; if its reply contains the literal string `"찾지 못했습니다"`, fall back to `_web_fallback()`, which tries `web_search.search_and_answer()` (Naver Search API + CLOVA to synthesize) and if that also fails, falls back to CLOVA's raw model knowledge with an "uncertain/please verify" instruction. Comments in the code explicitly warn against having the web-search/LLM path override a DB response that already contains curated content (it previously hallucinated a nonexistent replacement part) — respect that boundary when touching this logic.

**Servo motor/drive lookups are bidirectional** (`app/services/servo_spec_search.py`): capacity-in-watts → drive recommendation; drive model name → full detail (discontinued status + replacement + compatible motors + competitor comparison, all in one reply); motor model name → reverse search for compatible drives. `chatbot._route` tries drive-detail lookup first, then motor-reverse-lookup, before falling through to generic `specs.lookup_specs()`.

**Inventory resolution has two sources reconciled in one place** (`app/services/inventory.py` → `_resolve_stock_quantity()`/`get_stock_state()`): DB `Inventory.current_stock` is the fallback; when `NAVER_COMMERCE_ENABLED=true`, live Smartstore stock via `naver_commerce.search_stock_by_model_name()` takes precedence. Because the Commerce API's `/products/search` endpoint doesn't support free-text search (confirmed by testing, documented at the top of `naver_commerce.py`), the whole product catalog (~3,175 items) is paginated and cached in-process with a 10-minute TTL, then matched client-side by substring. Do not resurrect the old `origin_product_no` pre-mapping approach for stock lookups — it's deliberately removed because mismatches silently reported real stock as "out of stock" (see git history around `finalize_matching.py`).

**Any successful stock lookup triggers a Kakao "message to self" admin notification** (`app/services/admin_notify.notify_admins()`), separate from the older Slack webhook path in `inventory.py`. `in_stock` fires too, not just low/out — the point is to see what customers are asking for, and the per-model 1-hour debounce (in-process dict, resets on restart) is what keeps the volume down. Unmatched models (`"unknown"` state) deliberately do *not* notify. The notification includes competitor pricing from the Naver Shopping API and logs to `StockAlert`/`PriceHistory`.

**Notification recipients are rows, not a singleton.** `notify_admins()` loops over active `AlarmRecipient` rows (`channel='kakao'`), each holding its own Kakao `refresh_token` in `channel_token`; tokens are refreshed per-recipient and written back to that row. A failing recipient is isolated — it can't block other recipients or the customer's stock reply — and its failure is recorded by recipient id so `/api/admin/kakao-status` names who needs to re-authenticate. The legacy `KakaoToken` singleton table still exists for rollback but nothing reads it. Adding a recipient currently requires a manual OAuth flow (see `kakao_test/kakao_auth_setup.py`); a self-registration endpoint is planned.

**Competitor pricing excludes overseas listings** via keywords in the `price_filter_keywords` table (seeded with 해외/구매대행/해외배송/직구, 5-minute in-process cache). Keywords live in the DB so the list can change without a deploy. Deactivating every keyword means no filtering — the hardcoded fallback list applies only when the DB query itself fails. Note that used/accessory listings are deliberately *not* filtered, so `PriceHistory.needs_adjustment` can read as a false positive when a secondhand unit undercuts the new one.

**Production DB connectivity runs over Tailscale.** `start.sh` boots `tailscaled` in userspace mode with a local SOCKS5 proxy, then `tailscale_proxy.py` forwards to the actual NAS-hosted MariaDB, before `uvicorn` starts. This only matters when debugging why the deployed app can/can't reach the DB — locally you just point `DATABASE_URL` at SQLite or a directly-reachable MariaDB.

## Data model (`app/db/models.py`)

Under async SQLAlchemy 2.0: `Product` (master, keyed by `model_name`) → `Specification` (1:1, includes a JSON `extra_specs` escape hatch for servo-specific fields like `capacity_w`/`compatible_motors`) and `Inventory` (1:1); `Replacement` (old→new product FK pair with compatibility flags); `StockAlert` and `PriceHistory` (notification/pricing audit trail); `AlarmCode` (manufacturer+series+code lookup, independent of `Product`); `AlarmRecipient` (notification recipients, one row per admin); `PriceFilterKeyword` (competitor-price exclusion list); `KakaoToken` (legacy singleton, superseded by `AlarmRecipient` — kept for rollback only). `app/db/seed.py` auto-populates a small hardcoded sample catalog and the full `IG5A`/`MR-J4`/`FR-E700`/`FR-D700` alarm code tables on startup if the DB is empty — this is why a fresh SQLite file isn't actually empty of useful data after first boot.

## Root-level one-off scripts

The repo root has many standalone scripts (`migrate_*.py`, `register_*.py`, `finalize_matching.py`, `apply_manual_mappings.py`, `analyze_product_matching.py`, `fetch_smartstore_products.py`, etc.) used for one-time data migration, product-catalog matching against Smartstore, and manual PDF ingestion (`upload_manual.py`, per `manuals/README.md`'s manufacturer/series naming convention). These are run directly with `python <script>.py`, not imported by the app, and are historical/maintenance tooling rather than part of the request-serving path — check git log/commit messages before assuming one is still relevant, several were superseded by the model-name-based live search in `naver_commerce.py`.
