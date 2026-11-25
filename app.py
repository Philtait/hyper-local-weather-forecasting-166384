"""Streamlit dashboard for the Imarika project.

Run: streamlit run app.py
Weather pages (Weather Map, What if?, About this forecast) read only the
forecast export in deploy_data/ and assets/kenya_boundaries.geojson, in plain
language. Technical pages read saved results in reports/ and, when present
locally, data/. No trained model runs here. The Weather Map uses carto-positron
tiles through plotly's scatter_map, with an offline mode that draws outlines
only. Test SMS sending goes through sms_sender.py and needs a confirm click.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from geo import BOUNDARIES, features_at, load_boundaries, polygons, sub_county
from insights import generate_insights, load_config, sms_action, sms_text
from sms_sender import send_with_fallback, validate_phone
from ui_style import INK, NAVY, card, header, inject_style, text


# =============================================================================
# Presentation settings and schema aliases for the saved preprocessing outputs
# =============================================================================
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
GREEN = "#2E7D32"
RED = "#B3261E"
REPRESENTATIVE = "101990961244800083"
GREY = "#9E9E9E"
# Forecast export contract: the only files the forecast pages read.
DEPLOY = ROOT / "deploy_data"
DEPLOY_FILES = {
    "forecast_daily.csv": ["device_id", "date", "rain_mm", "rain_upper", "temp_min", "temp_max", "wind_max"],
    "station_status.csv": ["device_id", "latitude", "longitude", "last_reading_utc", "active", "reason"],
    "insights_latest.csv": ["device_id", "type", "severity", "start_date", "message", "basis", "as_of", "caveat"],
    "latest_readings.csv": ["device_id", "reading_utc", "air_temperature", "air_humidity", "wind_speed",
                            "rain_today_mm", "age_hours"],
    "README.md": None,
}
OPTIONAL_FILES = {"latest_readings.csv"}
LOCAL_TZ = "Africa/Nairobi"
BANNER = None  # set only by the local preview script
BOUNDARY_CREDIT = "Boundaries: geoBoundaries (open licence)"
# Plain-language status: label and colour, in precedence order (words always shown with colour).
STATUS = {
    "warning": ("Very heavy rain expected", "#C62828"),
    "watch": ("Heavy rain expected", "#E65100"),
    "planting": ("Good rain for planting", "#2E7D32"),
    "delay": ("Rain coming: delay spraying", "#1565C0"),
    "none": ("No alerts this week", "#5B7A99"),
    "offline": ("Not reporting", "#9E9E9E"),
}
RELIABILITY = [
    "Every station's forecast is made the same way; it is not adjusted for each station.",
    "Rain is harder to predict than temperature.",
    "Check local advice before big decisions.",
    "The possible heavy-rain case is a cautious high estimate, not a certain outcome.",
]
NOT_GENERATED = "Forecasts have not been generated yet."
VARIABLES = {
    "air_temperature": ("Temperature", "°C", ["air_temperature", "iot_air_temperature"]),
    "air_humidity": ("Humidity", "%", ["air_humidity", "iot_air_humidity"]),
    "barometric_pressure_hpa": ("Pressure", "hPa", ["barometric_pressure_hpa", "iot_barometric_pressure"]),
    "wind_speed": ("Wind speed", "m/s", ["wind_speed", "iot_wind_speed"]),
    "rainfall_mm": ("Rainfall", "mm", ["rainfall_mm", "iot_rainfall_mm"]),
}
WEATHER_PAGES = ["Weather Map", "What if? Try a rain forecast", "About this forecast"]
TECH_PAGES = ["Project Overview", "Data Collection", "Model Results", "About the project"]
PAGES = WEATHER_PAGES + TECH_PAGES

# Collection snapshot from the supplied files. IoT count is BEFORE Busia/QC
# filtering; the quality report instead counts 717,471 geographically valid rows.
COLLECTION = pd.DataFrame([
    {"Source": "IoT stations (Anga)", "Records": 764309,
     "Period": "Aug 2025 – Sep 2026", "Resolution": "Irregular; typically ~15 minutes"},
    {"Source": "Open-Meteo (ERA5)", "Records": 255840,
     "Period": "Aug 2025 – Sep 2026", "Resolution": "Hourly"},
    {"Source": "NASA POWER", "Records": 63674,
     "Period": "Jan 2020 – Sep 2026", "Resolution": "Daily"},
    {"Source": "CHIRPS", "Records": 62504,
     "Period": "Jan 2020 – Aug 2026", "Resolution": "Daily rainfall"},
])


# =============================================================================
# Local readers: cache by file signature, and report missing/malformed artifacts
# =============================================================================
def file_signature(path):
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def display_path(path):
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


@st.cache_data(show_spinner=False)
def read_csv_cached(path, signature):
    return pd.read_csv(path, dtype={"device_id": "string"}, low_memory=False)


@st.cache_data(show_spinner=False)
def read_text_cached(path, signature):
    return Path(path).read_text(encoding="utf-8")


def load_csv(path, required=()):
    try:
        result = read_csv_cached(str(path), file_signature(path))
        absent = set(required) - set(result.columns)
        if absent:
            raise ValueError(f"missing columns: {', '.join(sorted(absent))}")
        if result.empty:
            raise ValueError("file contains no records")
        return result
    except (OSError, ValueError, pd.errors.ParserError) as error:
        st.warning(f"Cannot load {display_path(path)}: {error}")
        return None


def load_text(path):
    try:
        return read_text_cached(str(path), file_signature(path))
    except (OSError, ValueError) as error:
        st.warning(f"Cannot load {display_path(path)}: {error}")
        return None


def clean_ids(series):
    return series.astype("string").str.replace(r'''["'“”‘’]''', "", regex=True).str.strip()


def station_coordinates(records):
    frame = pd.DataFrame(records)
    if not {"device_id", "lat", "lon"}.issubset(frame.columns):
        raise ValueError("station registry needs device_id, lat and lon")
    frame["device_id"] = clean_ids(frame.device_id)
    for column in ["lat", "lon"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    inside = (frame.lat.between(0.1, 0.7) & frame.lon.between(34.1, 34.6)
              & frame.device_id.notna() & frame.device_id.ne("")
              & frame.device_id.ne("test-spatial-check-device"))
    return frame.loc[inside, ["device_id", "lat", "lon"]].drop_duplicates("device_id").sort_values("device_id")


def load_stations():
    path = DATA / "busia_stations.json"
    if not path.is_file():
        st.info("The station registry (data/busia_stations.json) is only available when the app runs "
                "locally with the data/ folder, so the map is not shown here. The Network Status page "
                "shows the stations from the exported forecast files.")
        return None
    text = load_text(path)
    if text is None:
        return None
    try:
        stations = station_coordinates(json.loads(text))
        if len(stations) != 26:
            st.warning(f"The station file contains {len(stations)} valid Busia stations; expected 26.")
        return stations
    except (ValueError, TypeError, KeyError) as error:
        st.warning(f"Cannot parse data/busia_stations.json: {error}")
        return None


# =============================================================================
# Shared figures and scientific labels
# =============================================================================
def style_figure(figure, height=400):
    figure.update_layout(template="plotly_white", height=height,
                         paper_bgcolor="white", plot_bgcolor="white",
                         font={"family": "Arial, sans-serif", "color": "#193021"},
                         margin={"l": 35, "r": 25, "t": 35, "b": 40},
                         legend={"orientation": "h", "y": 1.12, "x": 0})
    return figure


def parse_audit(text):
    """Read the audit's narrative numbers instead of assuming every zero flag is clean."""
    retained = re.search(r"Retained \*\*([\d,]+) of", text)
    flags = re.search(r"Sensor flags: \*\*([\d,]+) nonzero\*\*, \*\*([\d,]+) missing", text)
    correlation = re.search(r"Pearson r:\s*\*\*(\d+\.\d+)[–-](\d+\.\d+)", text)
    resets = re.search(r"Cumulative rain:\s*\*\*(\d+) downward-step reset candidates", text)
    clean_pct = None
    if retained and flags:
        total = int(retained.group(1).replace(",", ""))
        errors, missing = (int(value.replace(",", "")) for value in flags.groups())
        if total:
            clean_pct = 100 * (total - errors - missing) / total
    return {
        "zero_flag_pct": clean_pct,
        "correlation": f"{correlation.group(1)}–{correlation.group(2)}" if correlation else None,
        "reset_candidates": int(resets.group(1)) if resets else None,
    }


def skill_colour(value):
    if pd.isna(value):
        return ""
    return f"color: {GREEN if value > 0 else RED if value < 0 else '#526159'}; font-weight: 600"


# =============================================================================
# Page 1. Project overview
# =============================================================================
def project_overview():
    st.title("Imarika — Hyper-Localised Agricultural Weather Forecasting")
    st.subheader("Busia County, Kenya")
    st.write("A data-driven weather forecasting project supporting timely agricultural decisions for smallholder farmers.")
    for column, value, label in zip(st.columns(5), ["26", "764,309", "13 months", "4", "1,146,327"],
                                    ["IoT weather stations", "Raw sensor readings", "Aug 2025 – Sep 2026", "Integrated data sources", "Total records collected"]):
        column.metric(label, value)

    st.header("System architecture")
    # Static HTML/CSS is entirely local: no diagram renderer, Graphviz binary,
    # remote font, image, tile server, or external JavaScript is required.
    st.markdown("""
    <div class="architecture">
      <div class="stage"><b>01 · Collect</b><span>IoT stations<br>Open-Meteo · NASA POWER · CHIRPS</span></div>
      <span class="arrow" aria-hidden="true">→</span>
      <div class="stage"><b>02 · Prepare</b><span>Quality audit<br>Unit conversion · hourly alignment</span></div>
      <span class="arrow" aria-hidden="true">→</span>
      <div class="stage"><b>03 · Forecast</b><span>SARIMA baseline<br>Multi-output LSTM</span></div>
      <span class="arrow" aria-hidden="true">→</span>
      <div class="stage"><b>04 · Evaluate</b><span>Chronological holdout<br>RMSE · MAE · skill scores</span></div>
      <span class="arrow" aria-hidden="true">→</span>
      <div class="stage"><b>05 · Communicate</b><span>Presentation dashboard<br>Planned farmer advisories</span></div>
    </div>
    """, unsafe_allow_html=True)
    st.caption("Forecasting research workflow. This dashboard reads saved results; farmer-facing alerts are a planned delivery layer.")
    st.header("Research focus")
    left, right = st.columns(2)
    with left:
        st.markdown("**Local observations, wider context**\n\nCombine station measurements with hourly reanalysis and daily satellite-derived datasets.")
    with right:
        st.markdown("**Measurable forecasting performance**\n\nEvaluate temperature, humidity, pressure, wind speed and rainfall against transparent baselines.")


# =============================================================================
# Page 2. Station map, source snapshot, and faithful audit findings
# =============================================================================
def data_collection():
    st.title("Data Collection")
    st.write("Four complementary sources describe weather conditions across Busia County.")
    stations = load_stations()
    st.header("Busia station network")
    if stations is not None and not stations.empty:
        figure = go.Figure(go.Scatter(
            x=stations.lon, y=stations.lat, mode="markers", customdata=stations.device_id,
            marker={"color": GREEN, "size": 11, "line": {"color": "white", "width": 1.5}},
            hovertemplate="Station %{customdata}<br>Latitude %{y:.5f}<br>Longitude %{x:.5f}<extra></extra>",
        ))
        features = load_boundaries(BOUNDARIES)
        for level, names, width in [("county", {"Busia"}, 2.5), ("sub-county", None, 1)]:
            lons, lats = outline(features, level, names)
            if lons:
                figure.add_trace(go.Scatter(x=lons, y=lats, mode="lines", hoverinfo="skip", showlegend=False,
                                            line={"color": "#7A8CA5", "width": width}))
        figure.update_xaxes(title="Longitude (°E)", range=[34.1, 34.6])
        figure.update_yaxes(title="Latitude (°N)", range=[0.1, 0.7], scaleanchor="x", scaleratio=1)
        map_column, detail_column = st.columns([3, 2])
        with map_column:
            st.plotly_chart(style_figure(figure, 420), width="stretch", config={"displayModeBar": False})
            st.caption("Offline coordinate map within the study bounding box, with Busia County and "
                       "sub-county outlines. No web tiles.")
            st.caption(BOUNDARY_CREDIT)
        with detail_column:
            st.metric("Mapped Busia stations", len(stations))
            st.dataframe(stations.rename(columns={"device_id": "Station", "lat": "Latitude", "lon": "Longitude"}),
                         hide_index=True, width="stretch", height=315)

    st.header("Dataset summary")
    st.dataframe(COLLECTION.style.format({"Records": "{:,.0f}"}), hide_index=True, width="stretch")
    st.caption("Collection snapshot: September 2026. Counts are raw source rows; IoT filtering retains 717,471 Busia readings.")

    st.header("Data quality")
    audit = load_text(REPORTS / "data_quality/report.md")
    if audit is None:
        return
    metrics = parse_audit(audit)
    columns = st.columns(3)
    columns[0].metric("IoT records with zero error flag", f"{metrics['zero_flag_pct']:.2f}%" if metrics["zero_flag_pct"] is not None else "Unavailable")
    columns[1].metric("IoT vs ERA5 temperature · Pearson R", metrics["correlation"] or "Unavailable")
    columns[2].metric("Rain-counter downward-step candidates", str(metrics["reset_candidates"]) if metrics["reset_candidates"] is not None else "Unavailable")
    st.caption("94.93% refers to zero error flags among the geographically filtered IoT records, not independently verified complete readings.")
    if any(value is None for value in metrics.values()):
        st.warning("Some quality metrics could not be extracted from the audit. Consult the full report below.")
    events = load_csv(REPORTS / "data_quality/rain_reset_events.csv", ["rain_accumulation", "drop_mm"])
    if events is not None:
        counter = pd.to_numeric(events.rain_accumulation, errors="coerce")
        drops = pd.to_numeric(events.drop_mm, errors="coerce")
        if counter.notna().all() and counter.eq(0).sum() == 0 and drops.notna().all():
            st.info(f"No confirmed full counter reset: {len(events)} small decreases were flagged, "
                    f"with drops of {drops.min():.3f}–{drops.max():.3f} mm. Neither counter returned to zero.")
    with st.expander("Read the complete data quality audit"):
        st.markdown(audit)


# =============================================================================
# Page 3. Saved SARIMA metrics and actual-vs-predicted plots
# =============================================================================
def model_results():
    st.title("Model Results")
    st.write("SARIMA baseline · one-step-ahead test forecasts · saved evaluation outputs")
    results = load_csv(REPORTS / "sarima_results.csv", ["device_id", "variable", "rmse", "mae", "skill_score"])
    if results is None:
        return
    results = results.copy()
    results["device_id"] = clean_ids(results.device_id)
    results["variable"] = results.variable.replace({"barometric_pressure": "barometric_pressure_hpa"})
    if "status" in results:
        failed = results.status.ne("ok")
        if failed.any():
            st.warning(f"{int(failed.sum())} saved result rows are unavailable or failed. Showing successful evaluations.")
        results = results.loc[~failed]
    results = results.loc[results.variable.isin(VARIABLES) & results.device_id.notna()]
    for metric in ["rmse", "mae", "skill_score"]:
        results[metric] = pd.to_numeric(results[metric], errors="coerce").replace([np.inf, -np.inf], np.nan)
    if results.empty:
        st.warning("No successful SARIMA results are available yet.")
        return
    scopes = sorted(results.device_id.unique(), key=lambda value: (value != REPRESENTATIVE, value == "ALL_STATIONS_MEAN", value))
    selection = st.selectbox("Evaluation scope", scopes,
                             format_func=lambda value: "Saved station mean" if value == "ALL_STATIONS_MEAN" else f"Station {value}")
    chosen = results.loc[results.device_id.eq(selection)].copy()
    if chosen.variable.duplicated().any():
        st.warning("Duplicate variable results in this scope; please resolve the saved CSV before presenting it.")
        return
    known_stations = [station for station in scopes if station != "ALL_STATIONS_MEAN"]
    if selection == "ALL_STATIONS_MEAN":
        counts = pd.to_numeric(chosen.get("stations_successful", pd.Series(dtype=float)), errors="coerce").dropna()
        covered = f"{int(counts.min())}–{int(counts.max())}" if len(counts) and counts.min() != counts.max() else str(int(counts.iloc[0])) if len(counts) else str(len(known_stations))
        st.info(f"The saved station mean covers {covered} evaluated station(s), out of the 26-station network. "
                "This is an unweighted mean of station metrics.")
    else:
        st.caption(f"Results shown for one station. The saved result file currently contains {len(known_stations)} evaluated station(s).")
    chosen = chosen.set_index("variable").reindex([target for target in VARIABLES if target in set(chosen.variable)]).reset_index()
    table = pd.DataFrame({
        "Variable": chosen.variable.map(lambda target: VARIABLES[target][0]),
        "Unit": chosen.variable.map(lambda target: VARIABLES[target][1]),
        "RMSE": chosen.rmse, "MAE": chosen.mae, "Skill score": chosen.skill_score,
    })
    st.dataframe(table.style.format({"RMSE": "{:.4f}", "MAE": "{:.4f}", "Skill score": "{:+.3f}"}, na_rep="—")
                 .map(skill_colour, subset=["Skill score"]), hide_index=True, width="stretch")
    st.caption("Skill = 1 − RMSE_model / RMSE_persistence. Green is better than persistence; red is worse. "
               "SARIMA persistence predicts the previous hour's reading.")
    figure = go.Figure(go.Bar(x=table.Variable, y=table.RMSE, marker_color=GREEN,
                             customdata=table.Unit, text=table.RMSE.round(3), textposition="outside",
                             hovertemplate="%{x}<br>RMSE %{y:.4f} %{customdata}<extra></extra>"))
    figure.update_yaxes(title="RMSE (variable-specific units)", rangemode="tozero")
    st.plotly_chart(style_figure(figure, 320), width="stretch", config={"displayModeBar": False})
    st.caption("Variables have different units and scales; these bar heights are not a like-for-like ranking of model quality.")

    st.header("Actual vs predicted · held-out test set")
    if not known_stations:
        st.warning("No station-specific result rows are available to identify the saved plots.")
        return
    plot_station = selection
    if selection == "ALL_STATIONS_MEAN":
        plot_station = st.selectbox("Station plots", known_stations)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", plot_station):
        st.warning("The station identifier cannot be matched safely to a plot filename.")
        return
    for tab, (target, (label, unit, _)) in zip(st.tabs([values[0] for values in VARIABLES.values()]), VARIABLES.items()):
        with tab:
            path = REPORTS / "sarima_plots" / f"{plot_station}_{target}.png"
            try:
                st.image(path.read_bytes(), caption=f"{label} ({unit}) · station {plot_station}", width="stretch")
            except Exception as error:
                st.warning(f"Cannot display {display_path(path)}: {error}")


# =============================================================================
# Forecast export (deploy_data/): readers
# =============================================================================
def deploy_missing():
    """Required export files that are absent (latest_readings.csv is optional)."""
    return [name for name in DEPLOY_FILES if name not in OPTIONAL_FILES and not (DEPLOY / name).is_file()]


def deploy_notice(missing):
    st.info(f"{NOT_GENERATED} Please check back after the next forecast update "
            f"(missing: {', '.join(missing)}).")


def load_deploy_csv(name, allow_empty=False):
    """Read one export CSV, or return None (absent files are reported by deploy_notice)."""
    path = DEPLOY / name
    if not path.is_file():
        return None
    try:
        frame = read_csv_cached(str(path), file_signature(path)).copy()
    except (OSError, ValueError, pd.errors.ParserError) as error:
        st.warning(f"Cannot read {name}: {error}")
        return None
    absent = set(DEPLOY_FILES[name]) - set(frame.columns)
    if absent:
        st.warning(f"{name} is missing columns: {', '.join(sorted(absent))}")
        return None
    if frame.empty and not allow_empty:
        st.warning(f"{name} contains no rows.")
        return None
    frame["device_id"] = clean_ids(frame.device_id)
    return frame.loc[frame.device_id.notna() & frame.device_id.ne("")]


def prepare_forecast(frame):
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame.date, errors="coerce").dt.date
    for column in ["rain_mm", "rain_upper", "temp_min", "temp_max", "wind_max"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return frame.loc[frame.date.notna()].sort_values(["device_id", "date"])


def prepare_status(frame):
    frame = frame.copy()
    for column in ["latitude", "longitude"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["last_seen"] = pd.to_datetime(frame.last_reading_utc, utc=True, errors="coerce", format="mixed")
    frame["is_active"] = frame.active.astype("string").str.strip().str.lower().isin(["true", "1", "yes", "active"])
    return frame.drop_duplicates("device_id").sort_values("device_id")


def prepare_latest(frame):
    frame = frame.copy()
    frame["reading_time"] = pd.to_datetime(frame.reading_utc, utc=True, errors="coerce", format="mixed")
    for column in ["air_temperature", "air_humidity", "wind_speed", "rain_today_mm", "age_hours"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return frame.drop_duplicates("device_id", keep="last")


# =============================================================================
# Plain-language station status, names and summary
# =============================================================================
def status_key(insights, active):
    """Precedence: not reporting, flood warning, flood watch, planting, rain delay, none."""
    if not active:
        return "offline"
    kinds = {(item["type"], item["severity"]) for item in insights}
    types = {item["type"] for item in insights}
    if ("flood_alert", "warning") in kinds:
        return "warning"
    if ("flood_alert", "watch") in kinds:
        return "watch"
    if "planting_window" in types:
        return "planting"
    if "rain_delay" in types:
        return "delay"
    return "none"


def main_insight(insights, key):
    """The insight that decided the status (first by date), or None."""
    wanted = {"warning": ("flood_alert", "warning"), "watch": ("flood_alert", "watch"),
              "planting": ("planting_window", None), "delay": ("rain_delay", None)}.get(key)
    if wanted is None:
        return None
    return next((item for item in insights if item["type"] == wanted[0]
                 and (wanted[1] is None or item["severity"] == wanted[1])), None)


def short_ids(device_ids):
    """Shortest unique ending (at least 3 digits) for each station number."""
    ids = sorted(set(device_ids))
    for size in range(3, max([len(value) for value in ids] + [3]) + 1):
        endings = [value[-size:] for value in ids]
        if len(set(endings)) == len(endings):
            return dict(zip(ids, endings))
    return {value: value for value in ids}


def station_names(device_ids, status, features):
    """Plain names such as 'Station 083, Nambale' (no sub-county if outside all)."""
    endings = short_ids(device_ids)
    names = {}
    for device_id in device_ids:
        name = f"Station {endings[device_id]}"
        if status is not None:
            row = status.loc[status.device_id.eq(device_id)]
            if len(row):
                area = sub_county(features, row.longitude.iloc[0], row.latitude.iloc[0])
                if area:
                    name = f"{name}, {area}"
        names[device_id] = name
    return names


def build_network(forecast, status, features):
    """One row per station with its name, activity, insights and status."""
    ids = sorted(set(forecast.device_id if forecast is not None else [])
                 | set(status.device_id if status is not None else []))
    names = station_names(ids, status, features)
    rows = []
    for device_id in ids:
        known = status.loc[status.device_id.eq(device_id)] if status is not None else pd.DataFrame()
        active = bool(known.is_active.iloc[0]) if len(known) else forecast is not None
        days = forecast.loc[forecast.device_id.eq(device_id)] if forecast is not None else pd.DataFrame()
        try:
            insights = generate_insights(days) if len(days) else []
        except ValueError:
            insights = []
        key = status_key(insights, active)
        rows.append({
            "device_id": device_id, "name": names[device_id], "active": active, "key": key,
            "label": STATUS[key][0], "colour": STATUS[key][1], "insights": insights,
            "latitude": known.latitude.iloc[0] if len(known) else np.nan,
            "longitude": known.longitude.iloc[0] if len(known) else np.nan,
            "last_seen": known.last_seen.iloc[0] if len(known) else pd.NaT,
        })
    return pd.DataFrame(rows, columns=["device_id", "name", "active", "key", "label", "colour", "insights",
                                       "latitude", "longitude", "last_seen"])


def plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


def summary_sentence(network):
    total, reporting = len(network), int(network.active.sum())
    heavy = int(network.key.isin(["warning", "watch"]).sum())
    rain = (f"{plural(heavy, 'station')} {'expects' if heavy == 1 else 'expect'} heavy rain this week."
            if heavy else "No station expects heavy rain this week.")
    return f"Busia County: {reporting} of {plural(total, 'station')} are reporting. {rain}"


def local_time(value, pattern="%d %b %Y, %H:%M"):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).tz_convert(LOCAL_TZ).strftime(pattern) + " (Kenya time)"


# =============================================================================
# Maps: boundary outlines, station markers, Kenya locator
# =============================================================================
def outline(features, level, names=None):
    """Longitude and latitude lists for the outer rings, separated by None."""
    lons, lats = [], []
    for feature in features_at(features, level):
        if names is not None and feature["properties"].get("name") not in names:
            continue
        for rings in polygons(feature["geometry"]):
            lons += [point[0] for point in rings[0]] + [None]
            lats += [point[1] for point in rings[0]] + [None]
    return lons, lats


def map_view(features, network):
    """Centre and bounds of Busia County (or of the stations, or a default)."""
    lons, lats = outline(features, "county", {"Busia"})
    lons, lats = [value for value in lons if value is not None], [value for value in lats if value is not None]
    if not lons and len(network.dropna(subset=["latitude", "longitude"])):
        lons, lats = list(network.longitude.dropna()), list(network.latitude.dropna())
    if not lons:
        return (34.25, 0.45), (34.0, 34.5), (0.1, 0.8)
    return ((min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2), (min(lons), max(lons)), (min(lats), max(lats))


def map_figure(network, features, offline):
    """Station map. Online: carto-positron tiles via scatter_map. Offline: outlines only."""
    line_trace = go.Scatter if offline else go.Scattermap
    point_trace = go.Scatter if offline else go.Scattermap
    figure = go.Figure()
    areas = {name.split(", ", 1)[1] for name in network.name if ", " in name} or {"Nambale", "Butula"}
    for level, names, colour, width in [("country", None, "#5B6B80", 1.5), ("county", {"Busia"}, NAVY, 3),
                                        ("sub-county", areas, "#7A8CA5", 1.5)]:
        lons, lats = outline(features, level, names)
        if lons:
            figure.add_trace(line_trace(x=lons, y=lats, mode="lines", line={"color": colour, "width": width},
                                        hoverinfo="skip", showlegend=False) if offline else
                             line_trace(lon=lons, lat=lats, mode="lines", line={"color": colour, "width": width},
                                        hoverinfo="skip", showlegend=False))
    placed = network.dropna(subset=["latitude", "longitude"])
    for key, (label, colour) in STATUS.items():
        group = placed.loc[placed.key.eq(key)]
        if group.empty:
            continue
        hover = [f"{row.name}<br>{label}<br>Last reading: {local_time(row.last_seen) or 'not available'}"
                 for row in group.itertuples()]
        marker = {"color": colour, "size": 20}
        if offline:
            marker["line"] = {"color": "white", "width": 2}
            trace = point_trace(x=group.longitude, y=group.latitude, mode="markers", name=label, marker=marker,
                                customdata=group.device_id, text=hover, hovertemplate="%{text}<extra></extra>")
        else:
            trace = point_trace(lon=group.longitude, lat=group.latitude, mode="markers", name=label,
                                marker=marker, customdata=group.device_id, text=hover,
                                hovertemplate="%{text}<extra></extra>")
        figure.add_trace(trace)
    centre_point, lon_range, lat_range = map_view(features, network)
    layout = {"height": 520, "margin": {"l": 0, "r": 0, "t": 10, "b": 0}, "paper_bgcolor": "white",
              "legend": {"orientation": "h", "y": -0.02, "yanchor": "top", "x": 0, "font": {"size": 15}},
              "font": {"size": 15, "color": INK}}
    if offline:
        pad = 0.05
        figure.update_xaxes(range=[lon_range[0] - pad, lon_range[1] + pad], visible=False)
        figure.update_yaxes(range=[lat_range[0] - pad, lat_range[1] + pad], visible=False, scaleanchor="x")
        layout["plot_bgcolor"] = "#F7F9FC"
    else:
        layout["map"] = {"style": "carto-positron", "zoom": 9.2,
                         "center": {"lon": centre_point[0], "lat": centre_point[1]}}
    figure.update_layout(**layout)
    return figure


def locator_figure(features):
    """Small Kenya outline with Busia County filled, drawn without tiles."""
    kenya_lon, kenya_lat = outline(features, "country")
    busia_lon, busia_lat = outline(features, "county", {"Busia"})
    if not kenya_lon:
        return None
    figure = go.Figure([
        go.Scatter(x=kenya_lon, y=kenya_lat, mode="lines", line={"color": "#5B6B80", "width": 1.2},
                   hoverinfo="skip", showlegend=False),
        go.Scatter(x=busia_lon, y=busia_lat, mode="lines", fill="toself", fillcolor="#E65100",
                   line={"color": "#E65100", "width": 1}, hoverinfo="skip", showlegend=False),
    ])
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False, scaleanchor="x")
    figure.update_layout(height=220, margin={"l": 0, "r": 0, "t": 0, "b": 0},
                         paper_bgcolor="white", plot_bgcolor="white")
    return figure


def boundary_credit():
    st.markdown(f'<div class="im-credit">{BOUNDARY_CREDIT}</div>', unsafe_allow_html=True)


# =============================================================================
# Station card
# =============================================================================
def what_to_do(key, insights, config):
    item = main_insight(insights, key)
    if item is not None:
        return sms_action(item, config)
    if key == "offline":
        return "This station is not reporting, so there is no forecast for it. Check a nearby station."
    return "No rain alerts for this station this week."


def day_tiles(rows, insights, config):
    flood = config["flood_alert"]
    delay_days = {item["start_date"] for item in insights if item["type"] == "rain_delay"}
    lookahead = rows.date.min() + timedelta(days=config["rain_delay"]["lookahead_days"] - 1) if len(rows) else None
    tiles = []
    for row in rows.itertuples():
        rain, colour = row.rain_mm, None
        if pd.notna(rain) and rain >= flood["warning_mm"]:
            colour = STATUS["warning"][1]
        elif pd.notna(rain) and rain >= flood["watch_mm"]:
            colour = STATUS["watch"][1]
        elif (delay_days and pd.notna(rain) and rain >= config["rain_delay"]["rain_day_mm"]
              and row.date <= lookahead):
            colour = STATUS["delay"][1]
        rain_text = f"Rain: {rain:.0f} mm" if pd.notna(rain) else "Rain: no data"
        temp_text = (f"{row.temp_min:.0f}–{row.temp_max:.0f}°C"
                     if pd.notna(row.temp_min) and pd.notna(row.temp_max) else "Temp: no data")
        style = f' style="border-color:{colour};background:{colour}14"' if colour else ""
        tiles.append(f'<div class="im-day{" alert" if colour else ""}"{style}><b>{row.date:%a}</b>'
                     f'<span>{row.date:%d %b}</span><span>{rain_text}</span><span>{temp_text}</span></div>')
    return '<div class="im-days">' + "".join(tiles) + "</div>"


def right_now(station, latest):
    if not station.active:
        seen = station.last_seen
        when = f"on {pd.Timestamp(seen).tz_convert(LOCAL_TZ):%d %B %Y}" if pd.notna(seen) else ""
        return f'<p class="im-big">This station stopped reporting {when}.</p>'.replace(" .", ".")
    row = latest.loc[latest.device_id.eq(station.device_id)] if latest is not None else pd.DataFrame()
    if row.empty or pd.isna(row.reading_time.iloc[0]):
        return '<p class="im-big">No recent sensor reading is available.</p>'
    row = row.iloc[0]
    value = lambda number, unit: f"{number:.0f}{unit}" if pd.notna(number) else "no data"
    cells = [("Temperature", value(row.air_temperature, "°C")), ("Humidity", value(row.air_humidity, "%")),
             ("Wind", f"{row.wind_speed:.1f} m/s" if pd.notna(row.wind_speed) else "no data"),
             ("Rain so far today", f"{row.rain_today_mm:.1f} mm" if pd.notna(row.rain_today_mm) else "no data")]
    tiles = "".join(f"<div>{name}<b>{text(reading)}</b></div>" for name, reading in cells)
    return f'<div class="im-now">{tiles}</div><p>Reading from {text(local_time(row.reading_time))}</p>'


def planting_figure(rows, insights, config):
    rule = config["planting_window"]
    item = next((entry for entry in insights if entry["type"] == "planting_window"), None)
    start = pd.Timestamp(item["start_date"]).date() if item else rows.date.min()
    window = rows.loc[rows.date >= start].head(rule["window_days"])
    days = [f"{day:%a %d}" for day in window.date]
    total = window.rain_mm.fillna(0).cumsum()
    figure = go.Figure([
        go.Bar(x=days, y=window.rain_mm, name="Rain each day (mm)", marker_color=NAVY,
               text=[f"{value:.0f} mm" if pd.notna(value) else "" for value in window.rain_mm],
               textposition="outside"),
        go.Scatter(x=days, y=total, name="Rain added up (mm)", mode="lines+markers",
                   line={"color": "#2E7D32", "width": 3}),
    ])
    figure.add_trace(go.Scatter(x=days, y=[rule["min_total_mm"]] * len(days), mode="lines",
                                name=f"{rule['min_total_mm']:g} mm needed over {rule['window_days']} days",
                                line={"color": "#C62828", "dash": "dash", "width": 2}))
    figure.update_yaxes(title="Rain (mm)", rangemode="tozero")
    figure.update_layout(height=300, margin={"l": 10, "r": 10, "t": 30, "b": 10}, paper_bgcolor="white",
                         plot_bgcolor="white", legend={"orientation": "h", "y": 1.15, "x": 0},
                         font={"size": 15, "color": INK})
    return figure


def sms_control(insights, area):
    """Preview card and the confirm-before-send control for one station."""
    if not insights:
        st.write("There is no alert to send for this station this week.")
        return
    choice = 0
    if len(insights) > 1:
        choice = st.selectbox("Which alert?", list(range(len(insights))), key="sms_advisory",
                              format_func=lambda index: sms_action(insights[index], load_config()))
    message = sms_text(insights[choice], area=area)
    st.markdown(f'<div class="im-sms" id="sms-preview">{text(message)}</div>', unsafe_allow_html=True)
    st.caption(f"{len(message)}/160 characters. Preview. Sandbox only.")
    phone = st.text_input("Phone number (+254 format)", key="sms_phone", placeholder="+254712345678")
    if st.button("Confirm and send", key="sms_confirm"):
        try:
            number = validate_phone(phone)
        except ValueError as error:
            st.error(str(error))
            return
        result = send_with_fallback(number, message)
        if result.sent:
            st.success(result.detail)
        else:
            st.warning(result.detail)
            st.code(result.text, language=None)
    st.caption("Nothing is sent until Confirm and send is pressed.")


def station_card(station, forecast, latest, config):
    label, colour = STATUS[station.key]
    card(f'<div class="im-label">Station status</div>'
         f'<div class="im-headline" style="color:{colour}">{text(label)}</div>'
         f'<div class="im-station">{text(station["name"])}</div>', colour)
    card(f'<div class="im-label">What to do</div>'
         f'<div class="im-big">{text(what_to_do(station.key, station.insights, config))}</div>', colour)
    rows = forecast.loc[forecast.device_id.eq(station.device_id)] if forecast is not None else pd.DataFrame()
    if station.active and len(rows):
        st.markdown('<div class="im-label">This week</div>', unsafe_allow_html=True)
        st.markdown(day_tiles(rows, station.insights, config), unsafe_allow_html=True)
    card(f'<div class="im-label">Right now</div>{right_now(station, latest)}')
    if station.active and len(rows):
        planting = next((item for item in station.insights if item["type"] == "planting_window"), None)
        rule = config["planting_window"]
        message = planting["message"] if planting else (
            f"No good planting rain in the next {config['forecast']['horizon_days']} days: the rain does not "
            f"reach {rule['min_total_mm']:g} mm over {rule['window_days']} days with at least "
            f"{rule['min_rain_days']} rainy days.")
        card(f'<div class="im-label">Planting</div><div class="im-big">{text(message)}</div>',
             STATUS["planting"][1] if planting else None)
        st.plotly_chart(planting_figure(rows, station.insights, config), width="stretch",
                        config={"displayModeBar": False}, key="planting_chart")
    with st.expander("How reliable is this?"):
        for line in RELIABILITY:
            st.markdown(f"- {line}")
    with st.expander("Text message preview"):
        main = main_insight(station.insights, station.key)
        ordered = ([main] if main else []) + [item for item in station.insights if item is not main]
        sms_control(ordered if station.active else [], station["name"])


# =============================================================================
# Weather pages: Weather Map (landing), What if?, About this forecast
# =============================================================================
def issued_line(forecast):
    if forecast is None or forecast.empty:
        return NOT_GENERATED
    return f"Imarika forecast, issued {forecast.date.min() - timedelta(days=1):%d %B %Y}"


def weather_map():
    missing = deploy_missing()
    forecast = load_deploy_csv("forecast_daily.csv")
    forecast = prepare_forecast(forecast) if forecast is not None else None
    header("Busia County weather", issued_line(forecast))
    if missing:
        deploy_notice(missing)
    status = load_deploy_csv("station_status.csv")
    status = prepare_status(status) if status is not None else None
    latest = load_deploy_csv("latest_readings.csv", allow_empty=True)
    latest = prepare_latest(latest) if latest is not None else None
    features = load_boundaries(BOUNDARIES)
    if forecast is None and status is None:
        return
    network = build_network(forecast, status, features)
    if network.empty:
        st.warning("The forecast files list no stations.")
        return
    st.markdown(f'<div class="im-summary">{text(summary_sentence(network))}</div>', unsafe_allow_html=True)
    config = load_config()
    left, right = st.columns([3, 2])
    with left:
        offline = st.toggle("Offline map", key="offline_map",
                            help="Draws only the outlines and stations, with no map background.")
        event = st.plotly_chart(map_figure(network, features, offline), width="stretch", key="station_map",
                                on_select="rerun", selection_mode="points", config={"displayModeBar": False})
        if not features:
            st.caption("Boundary outlines are not available, so only the stations are shown.")
        boundary_credit()
        picked = [point.get("customdata") for point in (getattr(getattr(event, "selection", None), "points", None) or [])
                  if point.get("customdata") in set(network.device_id)]
        if picked and picked[0] != st.session_state.get("last_map_pick"):
            st.session_state["last_map_pick"] = picked[0]
            st.session_state["station_choice"] = picked[0]
        locator = locator_figure(features)
        if locator is not None:
            st.markdown('<div class="im-label">Where Busia is in Kenya</div>', unsafe_allow_html=True)
            st.plotly_chart(locator, width="stretch", config={"displayModeBar": False}, key="locator_map")
    with right:
        order = network.sort_values("name").device_id.tolist()
        names = dict(zip(network.device_id, network.name))
        choice = st.selectbox("Choose a station", order, key="station_choice", format_func=names.get)
        station_card(network.loc[network.device_id.eq(choice)].iloc[0], forecast, latest, config)


def what_if():
    header("What if? Try a rain forecast", "Scenario, not a forecast")
    st.warning("Scenario, not a forecast. Type how much rain might fall on each of the next seven days "
               "to see what advice Imarika would give. No text message is sent from this page.")
    start = date.today() + timedelta(days=1)
    dates = [start + timedelta(days=offset) for offset in range(7)]
    columns = st.columns(7)
    values = [column.number_input(f"{day:%a %d %b} (mm)", min_value=0.0, max_value=1000.0, value=0.0,
                                  step=1.0, key=f"scenario_rain_{index}")
              for index, (column, day) in enumerate(zip(columns, dates))]
    frame = pd.DataFrame({"device_id": "Scenario", "date": dates, "rain_mm": values, "rain_upper": values,
                          "temp_min": np.nan, "temp_max": np.nan, "wind_max": np.nan})
    config = load_config()
    insights = generate_insights(frame, config)
    key = status_key(insights, True)
    label, colour = STATUS[key]
    card(f'<div class="im-label">Scenario result</div>'
         f'<div class="im-headline" style="color:{colour}">{text(label)}</div>'
         f'<div class="im-big">{text(what_to_do(key, insights, config))}</div>', colour)
    if not insights:
        st.success("Scenario: no alert would be given for this rain.")
    for item in insights:
        card(f'<div class="im-big">{text(item["message"])}</div>'
             f'<div class="im-label" style="margin-top:10px">Text message preview</div>'
             f'<div class="im-sms">{text(sms_text(item, area="Scenario", config=config))}</div>')


def about_forecast():
    header("About this forecast", "What Imarika does and what it cannot do")
    card('<div class="im-label">What this site does</div><div class="im-big">'
         "Imarika gives a weather forecast for the next seven days at each weather station in Busia County. "
         "It shows how much rain is expected each day and the lowest and highest temperature. "
         "It turns the rain forecast into simple advice about floods, planting and spraying. "
         "It can show the same advice as a short text message. "
         "It is a student project and does not replace advice from local experts.</div>")
    card('<div class="im-label">Where the data comes from</div><div class="im-big">'
         "Weather sensors at stations in Busia County, Kenya, mostly in Nambale and Butula sub-counties "
         "(two stations are in Matayos). Each station measures temperature, humidity, wind and rain.</div>")
    card('<div class="im-label">Limits</div><div class="im-big">'
         + "<br>".join(text(line) for line in RELIABILITY) + "</div>")
    boundary_credit()


# =============================================================================
# Technical page: academic attribution
# =============================================================================
def about():
    st.title("About the Project")
    st.subheader("ICS 4101 Final Year Capstone")
    st.markdown("""
**Student**  
Philip Tait (166384)

**Programme**  
BSc Informatics and Computer Science — ICS 4E

**Institution**  
Strathmore University

**Supervisor**  
Ms. Julliet Kirui

**Industry**  
@iLabAfrica Research and Innovation Centre Strathmore University
""")
    st.divider()
    st.markdown("**Imarika** brings local sensor measurements and complementary weather datasets together to support forecasting research for smallholder farmers in Busia County, Kenya.")
    st.caption(BOUNDARY_CREDIT + ". Kenya and county outlines: RCMRD (public domain); "
               "sub-county outlines: IEBC and OCHA ROSEA (CC BY 3.0 IGO). See assets/README.md.")


def switch_to_weather():
    st.session_state["nav_tech"] = None


def switch_to_tech():
    st.session_state["nav_weather"] = None


def main():
    st.set_page_config(page_title="Imarika | Busia weather", page_icon="🌦", layout="wide")
    inject_style()
    if BANNER:
        st.markdown(f'<div class="im-preview-banner">{text(BANNER)}</div>', unsafe_allow_html=True)
    with st.sidebar:
        st.title("Imarika")
        st.caption("BUSIA COUNTY · KENYA")
        weather = st.radio("Weather", WEATHER_PAGES, key="nav_weather", on_change=switch_to_weather)
        st.divider()
        st.caption("Project details (technical)")
        technical = st.radio("Project details (technical)", TECH_PAGES, index=None, key="nav_tech",
                             on_change=switch_to_tech, label_visibility="collapsed")
    page = technical or weather or WEATHER_PAGES[0]
    {
        "Weather Map": weather_map,
        "What if? Try a rain forecast": what_if,
        "About this forecast": about_forecast,
        "Project Overview": project_overview,
        "Data Collection": data_collection,
        "Model Results": model_results,
        "About the project": about,
    }[page]()


if __name__ == "__main__":
    main()
