# Deploying the on-demand endpoint

The page gets its edition from `api/live.py` when that is reachable, and from
the committed `data/live.json` when it is not. Everything works without the
endpoint — it is just as stale as the last scheduled GitHub Actions run.

## Why an endpoint is needed at all

The page cannot assemble its own edition. Measured from a browser, 6 Nov 2026:

| Allows cross-origin reads | Refuses |
| --- | --- |
| CNBC, MarketWatch, The Record, Cybersecurity Dive, open-meteo | Yahoo (both hosts), Stooq, Nasdaq, BBC, Guardian, Sky, Al Jazeera, Artemis, Insurance Journal, Reinsurance News, Autosport, Motorsport, Yahoo Sports, RealGM |

No price source is reachable, so the gate in particular can only be built
server-side.

## Deploy

```
npx vercel login
npx vercel --prod
```

Run both from this directory. The first opens a browser to authenticate; the
second deploys the static page and the function together. No environment
variables and no database — the function holds no state.

## After the first deploy

The Vercel-hosted copy works immediately: `api/live` is same-origin there.

To make the **GitHub Pages** copy live as well, put the deployed endpoint in
`API_ABS` near the top of the script block in `index.html`:

```js
var API_ABS = "https://<project>.vercel.app/api/live";
```

Leave it empty and the Pages copy keeps reading the committed file.

## What it costs

One function invocation per page load, minus the 60-second edge cache. A
rebuild takes about 1.2 seconds and makes roughly 30 upstream requests in
parallel. Nothing is written anywhere.
