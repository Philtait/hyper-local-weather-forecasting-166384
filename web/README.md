# Imarika website

Static site for Imarika with five pages: Home, How it works, Weather map, SMS demo and Contact.
No build step at deploy time and no runtime npm dependencies; fonts (Lexend, OFL) and Leaflet 1.9.4
are vendored in `fonts/` and `vendor/`.

## Pages, strings and images

The pages share one header, footer and bottom navigation. They are written from `site_src/` and the
strings in `i18n/en.json`; edit those, then regenerate the HTML (the output is committed):

```
python scripts/build_pages.py            # writes web/*.html
python scripts/make_images.py            # docs/reference/images-src/*.png -> web/img/*.webp
```

All visible words live in `i18n/en.json`. Icons are one sprite, `img/icons.svg`. The map uses
OpenStreetMap tiles when online and falls back to the boundary outlines when they cannot load.

## Data

The site reads only `data/*.json`, written by the Python builder from the forecast export:

```
python scripts/build_site_data.py --deploy-dir deploy_data --out web/data
python scripts/build_site_data.py --synthetic --out web/data      # fake preview data
```

If the export has `history_daily.csv` (device_id, date, rain_mm, temp_min, temp_max, humidity_mean,
readings_received) and a `coverage_14d` column in `station_status.csv`, the station charts show the last
14 days; a day without readings is shown as "No reading", never as zero. Without them the charts show
the forecast only. Synthetic mode makes up history with gaps.

`--reports-dir` (default `reports/`) is read for the accuracy and data-quality numbers. The
synthetic flag lives only in `meta.json` and is never shown on a page. Do not commit `web/data` while it
is synthetic; a test fails if a committed `meta.json` has `"synthetic": true`.

## Contact details

`contact.json` holds the email address, phone, organisation and address shown on the Contact page.
They are empty until filled in; empty values are hidden, and the email buttons appear only once an
email address is set. The contact form opens the visitor's email app (mailto); nothing is stored.

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
