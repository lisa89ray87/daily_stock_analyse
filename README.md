# Daily Stock Analysis

Automated research and reporting system for a fixed watchlist plus dynamic US/Nasdaq opportunities.

This project generates a daily report that combines:

- Market regime context
- Technical indicators
- Provider-backed news summaries
- Long/short/no-trade signal logic
- Responsive HTML output for email

This is a research and analysis workflow only. It does not execute brokerage orders.

## Architecture

- `src/daily_stock_analyse/config.py`: environment + watchlist/weights config loader
- `src/daily_stock_analyse/providers/`: market/news provider abstractions and yfinance implementation
- `src/daily_stock_analyse/scoring.py`: transparent long/short scoring model with configurable weights
- `src/daily_stock_analyse/selector.py`: dynamic additional stock selection (exactly 3)
- `src/daily_stock_analyse/market.py`: market regime analysis (Nasdaq/S&P/Dow/VIX/10Y/sector proxies)
- `src/daily_stock_analyse/reporting.py`: markdown + responsive HTML rendering
- `src/daily_stock_analyse/email_provider.py`: email abstraction + Resend implementation
- `src/daily_stock_analyse/runner.py`: orchestration, resilience, output artifacts
- `templates/daily_report.html`: responsive email template

## Fixed Watchlist

Always analyzed:

1. NOK
2. AMD
3. NVDA
4. INTC
5. SNDK
6. 000660.KS

Configured in `config/watchlist.json`.

## Dynamic Selection

The system selects exactly 3 additional US/Nasdaq opportunities from a configurable candidate universe.

- Not permanently hard-coded to the same 3 symbols
- Supports LONG, SHORT, HOLD, SELL, and NO TRADE outcomes
- Prioritizes conviction from score spread and magnitude

## Scoring Model

Default configurable weights (`config/watchlist.json`):

- Trend: 20%
- Momentum: 15%
- Volume: 10%
- Relative strength: 10%
- Fundamentals/news: 20%
- Catalyst/event: 10%
- Risk/reward: 15%

Model computes both long and short paths independently and does not force bullish outputs.

## Data and Limitations

- Market data provider default: `yfinance`
- News provider default: `yfinance` news feed
- Uses latest available provider data, not guaranteed real-time
- Extended-hours data is labeled when available
- Missing fields are marked `UNAVAILABLE` instead of fabricated

## AI Analysis

Optional AI overlay is enabled when `OPENAI_API_KEY` is configured.

Prompt constraints enforce:

- No fabricated prices/news/earnings/ratings
- FACT vs INTERPRETATION separation
- Admitting uncertainty
- LONG and SHORT allowed
- NO TRADE allowed

If AI call fails, deterministic data-driven report is still produced.

## Email Configuration

Email provider abstraction: `EmailProvider.send_html(...)`

Current provider: Resend (HTML email).

Required env vars:

- `RESEND_API_KEY`
- `EMAIL_FROM`
- `EMAIL_TO` (defaults to `raymond87tan@gmail.com`)

Disable sending while still generating report:

- `SEND_EMAIL=0`

## Setup

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` values into your environment (do not commit `.env`).

## Local Execution

```bash
python -m src.daily_stock_analyse
```

No-email mode:

```bash
SEND_EMAIL=0 python -m src.daily_stock_analyse
```

Outputs are written to `artifacts/`:

- `daily_stock_analysis.md`
- `daily_stock_analysis.html`
- `daily_stock_analysis.json`

## GitHub Actions

Workflow file:

- `.github/workflows/daily_stock_analysis.yml`

Supports:

- Manual run (`workflow_dispatch`)
- Scheduled weekdays via UTC cron (default `0 23 * * 1-5`)

Timezone note:

- `23:00 UTC` corresponds to approximately `07:00` Malaysia time on the next day.
- This is configured for morning research delivery and is not a US market-open claim.

### Required GitHub Secrets

- `OPENAI_API_KEY`
- `RESEND_API_KEY`
- `EMAIL_FROM`
- `EMAIL_TO` (set to `raymond87tan@gmail.com` if desired)

Optional GitHub Variable:

- `DAILY_REPORT_CRON_UTC`

## Error Handling Behavior

- If one symbol fails: continue remaining symbols and mark unavailable
- If news fails: continue with technical analysis and mark news unavailable
- If AI fails: continue with deterministic fallback
- If email send fails: process exits non-zero, report artifacts remain in `artifacts/`

## Testing

Focused tests cover:

- Scoring and long/short behavior
- Dynamic selection behavior
- Missing market data resilience
- Invalid-like provider response tolerance
- HTML rendering
- Email payload construction
- Configuration loading

Run:

```bash
pytest tests -q
```

## Risk Disclaimer

This system provides automated research summaries only, not investment advice. It does not guarantee outcomes and does not execute trades.