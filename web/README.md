# Imarika website

Static showcase site for Imarika: station map, 7-day forecasts, weekly alerts, an SMS sandbox demo and an
explanation of how reliable the forecasts are. No build step and no runtime npm dependencies; fonts
(Lexend, OFL) and Leaflet 1.9.4 are vendored in `fonts/` and `vendor/`.

## Data

The site reads only `data/*.json`, written by the Python builder from the forecast export:

```
python scripts/build_site_data.py --deploy-dir deploy_data --out web/data
python scripts/build_site_data.py --synthetic --out web/data      # fake preview data, red banner
```

`--reports-dir` (default `reports/`) is read for the accuracy and data-quality numbers. Do not commit
`web/data` while it is synthetic; a test fails if a committed `meta.json` has `"synthetic": true`.

## Run locally

```
python -m http.server 8080 --directory web
```

Open http://localhost:8080. The static server has no SMS function, so the demo shows
"Demo mode: message shown on the phone only".

## Deploy (Vercel)

1. Build `web/data` from the real export and commit it.
2. Create a Vercel project with root directory `web` (no framework, no build command).
3. Set the environment variables `AT_USERNAME` (must be exactly `sandbox`) and `AT_API_KEY`.
4. Deploy. `api/send-sms.js` becomes `/api/send-sms`; `vercel.json` bundles `data/` with it.

The function sends only to the Africa's Talking sandbox and refuses to run with any other username.

## Tests

```
python -m pytest -q tests/test_build_site_data.py
node --test tests/node/send_sms.test.js
python scripts/make_icons.py     # redraws the PWA icons
```
