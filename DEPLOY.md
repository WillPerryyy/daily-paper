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

Connect the repository on <https://vercel.com/new> and pick `daily-paper`.
Accept the defaults: `vercel.json` already declares the function, there are no
environment variables, no build step and no database. Every push then
redeploys, including the hourly `live.json` commits from the Action.

### The CLI does not work on this machine

```
npx vercel login
npx vercel --prod
```

is the usual route and it fails here, before reaching the network:

```
EPERM: operation not permitted, symlink
  '..\..\~npm~@vercel+build-utils@15.3.0\node_modules\@vercel\build-utils'
  -> 'C:\Users\willi\AppData\Local\com.vercel.cli\Cache\builders-vlt\...'
```

Vercel CLI 65 installs its builders as a tree of directory symlinks, and
Windows refuses `fs.symlink(type:'dir')` unless Developer Mode is on or the
shell is elevated. Confirmed directly — `type:'dir'` returns EPERM on this
machine while `type:'junction'` succeeds. The failure is local packaging, not
authentication: `vercel deploy --temporary`, which needs no account at all,
dies at the same point.

Things that do not get round it, so as not to be retried: pinning an older CLI
(v39 has no `--temporary`), and preloading a Node shim that rewrites directory
symlinks to junctions (the installer runs outside the patched module graph —
`VERCEL_CLI_USE_NATIVE_BINARY=0` does not bring it back in).

To use the CLI, turn on **Settings → System → For developers → Developer
Mode** and run the two commands again. Otherwise use the GitHub connection
above, which needs no CLI and is the better fit anyway: the data commits push
hourly, so the deployment tracks them by itself.

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
