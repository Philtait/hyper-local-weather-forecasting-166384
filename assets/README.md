# Assets

## kenya_boundaries.geojson

Boundaries: geoBoundaries (open licence).

Built with `scripts/build_boundaries.py` from the geoBoundaries gbOpen simplified
releases for Kenya (release commit 9469f09, data year 2020, downloaded 2026-11-25):

| Level | geoBoundaries name | Source | Licence |
|---|---|---|---|
| ADM0 | Republic of Kenya | RCMRD GeoPortal, geoBoundaries | Public Domain |
| ADM1 | Counties | RCMRD GeoPortal, Africa GeoPortal | Public Domain |
| ADM2 | Sub-Counties | IEBC, OCHA ROSEA | CC BY 3.0 IGO |

Download URLs (`<L>` = ADM0, ADM1, ADM2):
`https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/KEN/<L>/geoBoundaries-KEN-<L>_simplified.geojson`

The file keeps the Kenya outline (`level` "country"), Busia County ("county") and
the seven ADM2 units whose centre lies in Busia ("sub-county": Budalangi, Butula,
Funyula, Matayos, Nambale, Teso North, Teso South). Coordinates are rounded to
4 decimal places. The ADM2 layer has 290 units, the number of Kenya's
constituencies, so these units may be constituencies rather than administrative
sub-counties; the names used here match both for Busia.

geoBoundaries: Runfola, D. et al. (2020) geoBoundaries: A global database of
political administrative boundaries. PLoS ONE 15(4): e0231866.
