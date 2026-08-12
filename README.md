# The Daily Paper

A phone-readable morning brief: the TQQQ gate, markets, NYC weather, plus jobs,
cyber-insurance intel, market news, England and sport.

## Two owners, two files

`data/live.json` — **owned by the scheduled Action.** The TQQQ gate, the markets
table and the weather. Regenerated every weekday evening by `update_live.py`.

`data/research.json` — **owned by Claude.** Jobs, cyber intel, market news,
England and sport. Written when the daily research run happens.

They are separate files so neither process can clobber the other's work. The page
reads both and reports each one's freshness independently: if the research is
older than today it says so rather than passing stale news off as current.

## Why the data is committed rather than fetched in the browser

A browser cannot read Yahoo — it has the prices but sends no CORS header, so
Safari blocks it. Alpha Vantage sends CORS but caps free history at 100 sessions,
and the volatility gates need 250. Fetching server-side in the Action sidesteps
both: CORS is a browser rule and does not apply there, and the committed JSON is
served same-origin. No API key, no rate limit, nothing to configure.

The rule reads the prior close by design, so a once-daily refresh is the right
cadence rather than a compromise.

## The gate

20-day realised vol < 35% · 20d/250d vol ratio < 1.10 · drawdown from the
250-day high better than −20% (re-entry above −10%). All three, holding two
consecutive sessions.

Computed on QQQ closes. The tested rule (`tqqq-strategy/spec.py`) uses the
Nasdaq-100 and total returns; QQQ price tracks it at 99.98% signal agreement
across 6,646 sessions. Reads the prior close; act at the next one.

Not investment advice.
