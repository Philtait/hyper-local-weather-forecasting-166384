"""Streamlit dashboard for the Imarika project.

Run: streamlit run app.py
Reads saved results in reports/, exported forecasts in deploy_data/ (the only
forecast source in the deployed app) and, when present locally, data/. It runs
no trained models. Maps use local Plotly coordinates rather than web tiles.
Test SMS sending goes through sms_sender.py and needs a confirm click.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from insights import generate_insights, sms_text
from sms_sender import send_with_fallback, validate_phone


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
    "README.md": None,
}
NOT_GENERATED = "Forecasts have not been generated yet."
FORECAST_CAVEATS = [
    "Model orders are reused from one station and not tuned per station.",
    "Rainfall forecasts are less reliable than temperature forecasts.",
    "The rain upper envelope is not a statistical interval.",
]
INSIGHT_LABELS = {"flood_alert": "Flood alert", "planting_window": "Planting window", "rain_delay": "Rain delay"}
VARIABLES = {
    "air_temperature": ("Temperature", "°C", ["air_temperature", "iot_air_temperature"]),
    "air_humidity": ("Humidity", "%", ["air_humidity", "iot_air_humidity"]),
    "barometric_pressure_hpa": ("Pressure", "hPa", ["barometric_pressure_hpa", "iot_barometric_pressure"]),
    "wind_speed": ("Wind speed", "m/s", ["wind_speed", "iot_wind_speed"]),
    "rainfall_mm": ("Rainfall", "mm", ["rainfall_mm", "iot_rainfall_mm"]),
}
PAGES = ["Project Overview", "Data Collection", "Model Results", "Forecast and Advisories", "Network Status", "About"]

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
        figure.update_xaxes(title="Longitude (°E)", range=[34.1, 34.6])
        figure.update_yaxes(title="Latitude (°N)", range=[0.1, 0.7], scaleanchor="x", scaleratio=1)
        map_column, detail_column = st.columns([3, 2])
        with map_column:
            st.plotly_chart(style_figure(figure, 420), width="stretch", config={"displayModeBar": False})
            st.caption("Offline coordinate map within the study bounding box. No web tiles or inferred county boundary.")
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
# Forecast export (deploy_data/): readers and labels
# =============================================================================
def deploy_missing():
    return [name for name in DEPLOY_FILES if not (DEPLOY / name).is_file()]


def deploy_notice(missing):
    st.info(f"{NOT_GENERATED} The forecast export writes these files to deploy_data/; "
            f"missing: {', '.join(missing)}.")


def load_deploy_csv(name, allow_empty=False):
    """Read one export CSV, or return None (absent files are reported by deploy_notice)."""
    path = DEPLOY / name
    if not path.is_file():
        return None
    try:
        frame = read_csv_cached(str(path), file_signature(path)).copy()
    except (OSError, ValueError, pd.errors.ParserError) as error:
        st.warning(f"Cannot read deploy_data/{name}: {error}")
        return None
    absent = set(DEPLOY_FILES[name]) - set(frame.columns)
    if absent:
        st.warning(f"deploy_data/{name} is missing columns: {', '.join(sorted(absent))}")
        return None
    if frame.empty and not allow_empty:
        st.warning(f"deploy_data/{name} contains no rows.")
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
    frame["reason"] = frame.reason.fillna("").astype(str)
    return frame.sort_values("device_id")


def station_area(device_id, status):
    """Area name if the export provides an optional 'area' column, else None."""
    if status is None or "area" not in status:
        return None
    match = status.loc[status.device_id.eq(device_id), "area"].dropna().astype(str).str.strip()
    return match.iloc[0] if len(match) and match.iloc[0] else None


def station_label(device_id, status):
    label = f"Station {device_id}"
    area = station_area(device_id, status)
    if area:
        return f"{area} · {label}"
    if status is not None:
        row = status.loc[status.device_id.eq(device_id)]
        if len(row) and row.latitude.notna().iloc[0] and row.longitude.notna().iloc[0]:
            return f"{label} ({row.latitude.iloc[0]:.3f}°N, {row.longitude.iloc[0]:.3f}°E)"
    return label


def forecast_figures(rows):
    days = [str(day) for day in rows.date]
    rain = go.Figure()
    rain.add_trace(go.Bar(x=days, y=rows.rain_mm, name="Rain forecast", marker_color=GREEN))
    rain.add_trace(go.Scatter(x=days, y=rows.rain_upper, name="Upper envelope (not a statistical interval)",
                              mode="lines+markers", line={"color": "#D9822B", "dash": "dash"}))
    rain.update_yaxes(title="Rain (mm/day)", rangemode="tozero")
    temperature = go.Figure()
    temperature.add_trace(go.Scatter(x=days, y=rows.temp_max, name="Maximum", mode="lines+markers",
                                     line={"color": RED}))
    temperature.add_trace(go.Scatter(x=days, y=rows.temp_min, name="Minimum", mode="lines+markers",
                                     line={"color": "#1565C0"}))
    temperature.update_yaxes(title="Temperature (°C)")
    wind = go.Figure(go.Scatter(x=days, y=rows.wind_max, name="Maximum wind", mode="lines+markers",
                                line={"color": "#526159"}))
    wind.update_yaxes(title="Wind (m/s)", rangemode="tozero")
    return [style_figure(figure, 320) for figure in (rain, temperature, wind)]


def render_insight(item):
    with st.container(border=True):
        st.markdown(f"**{INSIGHT_LABELS.get(item['type'], item['type'])} · {item['severity']}** "
                    f"· from {item['start_date']}")
        st.write(item["message"])
        st.caption(f"Basis: {item['basis']}")
        st.caption(f"Caveat: {item['caveat']}")


def insight_name(item):
    return f"{INSIGHT_LABELS.get(item['type'], item['type'])} ({item['severity']}) from {item['start_date']}"


def sms_control(insights, area):
    st.header("Send test SMS")
    if not insights:
        st.caption("There is no advisory to send for this station.")
        return
    choice = st.selectbox("Advisory", list(range(len(insights))), key="sms_advisory",
                          format_func=lambda index: insight_name(insights[index]))
    text = sms_text(insights[choice], area=area)
    st.text_area("Message preview", text, disabled=True, key="sms_preview")
    st.caption(f"{len(text)} characters (single SMS).")
    phone = st.text_input("Phone number (+254 format)", key="sms_phone", placeholder="+254712345678")
    if st.button("Confirm and send", key="sms_confirm"):
        try:
            number = validate_phone(phone)
        except ValueError as error:
            st.error(str(error))
            return
        result = send_with_fallback(number, text)
        if result.sent:
            st.success(result.detail)
        else:
            st.warning(result.detail)
            st.code(result.text, language=None)
    st.caption("Nothing is sent until Confirm and send is pressed.")


# =============================================================================
# Page 4. Forecast and advisories (with scenario mode)
# =============================================================================
def scenario_mode():
    st.subheader("Scenario, not a forecast")
    st.warning("Scenario, not a forecast: enter seven daily rainfall amounts to see which advisories "
               "the rules would issue. Nothing here comes from a model, and no SMS is sent.")
    columns = st.columns(7)
    start = date.today() + timedelta(days=1)
    dates = [start + timedelta(days=offset) for offset in range(7)]
    values = [column.number_input(f"Day {index + 1} ({day:%d %b}) mm", min_value=0.0, max_value=1000.0,
                                  value=0.0, step=1.0, key=f"scenario_rain_{index}")
              for index, (column, day) in enumerate(zip(columns, dates))]
    frame = pd.DataFrame({"device_id": "Scenario", "date": dates, "rain_mm": values, "rain_upper": values,
                          "temp_min": np.nan, "temp_max": np.nan, "wind_max": np.nan})
    insights = generate_insights(frame)
    st.header("Scenario advisories")
    if not insights:
        st.success("Scenario: no advisory would be issued for these rainfall values.")
    for item in insights:
        render_insight(item)
        st.text_area(f"Scenario SMS preview: {insight_name(item)}", sms_text(item, area="Scenario"),
                     disabled=True, key=f"scenario_sms_{item['type']}_{item['start_date']}")


def forecast_and_advisories():
    st.title("Forecast and Advisories")
    mode = st.radio("Mode", ["Forecast", "Scenario"], horizontal=True, key="advisory_mode")
    if mode == "Scenario":
        scenario_mode()
        return
    missing = deploy_missing()
    if missing:
        deploy_notice(missing)
    forecast = load_deploy_csv("forecast_daily.csv")
    if forecast is None:
        st.caption("Scenario mode works without exported forecasts.")
        return
    forecast = prepare_forecast(forecast)
    status = load_deploy_csv("station_status.csv")
    status = prepare_status(status) if status is not None else None
    stations = sorted(forecast.device_id.unique())
    if not stations:
        st.warning("deploy_data/forecast_daily.csv has no rows with a usable date.")
        return
    station = st.selectbox("Station", stations, key="forecast_station",
                           format_func=lambda device_id: station_label(device_id, status))
    rows = forecast.loc[forecast.device_id.eq(station)]
    try:
        insights = generate_insights(rows)
    except ValueError as error:
        st.warning(f"Advisories could not be produced: {error}")
        insights = []
    as_of = rows.date.min() - timedelta(days=1)
    cards = st.columns(3)
    cards[0].metric("Forecast as of", f"{as_of:%d %b %Y}")
    cards[1].metric("Forecast days", str(rows.date.nunique()))
    cards[2].metric("Advisories", str(len(insights)))
    st.info("Caveats:\n\n" + "\n".join(f"- {caveat}" for caveat in FORECAST_CAVEATS))

    st.header("7-day forecast")
    table = rows[["date", "rain_mm", "rain_upper", "temp_min", "temp_max", "wind_max"]].rename(columns={
        "date": "Date", "rain_mm": "Rain (mm)", "rain_upper": "Rain upper envelope (mm)",
        "temp_min": "Min temp (°C)", "temp_max": "Max temp (°C)", "wind_max": "Max wind (m/s)"})
    st.dataframe(table, hide_index=True, width="stretch")
    for tab, figure in zip(st.tabs(["Rainfall", "Temperature", "Wind"]), forecast_figures(rows)):
        with tab:
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})

    st.header("Advisories")
    if not insights:
        st.success("No advisories for this station in the forecast period.")
    for item in insights:
        render_insight(item)
    exported = load_deploy_csv("insights_latest.csv", allow_empty=True)
    if exported is not None:
        saved = exported.loc[exported.device_id.eq(station)]
        if sorted(zip(saved.type, saved.start_date.astype(str))) != sorted(
                (item["type"], item["start_date"]) for item in insights):
            st.warning("deploy_data/insights_latest.csv differs from the advisories recomputed from "
                       "forecast_daily.csv for this station; the export may be out of date.")

    sms_control(insights, station_area(station, status) or f"Station {station}")
    readme = DEPLOY / "README.md"
    if readme.is_file():
        with st.expander("About this forecast export"):
            st.markdown(load_text(readme) or "")


# =============================================================================
# Page 5. Network status from the forecast export
# =============================================================================
def network_status():
    st.title("Network Status")
    missing = deploy_missing()
    if missing:
        deploy_notice(missing)
    status = load_deploy_csv("station_status.csv")
    if status is None:
        return
    status = prepare_status(status)
    active, inactive = status.loc[status.is_active], status.loc[~status.is_active]
    cards = st.columns(3)
    cards[0].metric("Active stations", str(len(active)))
    cards[1].metric("Inactive stations", str(len(inactive)))
    cards[2].metric("Stations listed", str(len(status)))
    figure = go.Figure()
    for subset, name, colour in [(active, "Active", GREEN), (inactive, "Inactive", GREY)]:
        mapped = subset.loc[subset.latitude.notna() & subset.longitude.notna()]
        seen = mapped.last_seen.dt.strftime("%Y-%m-%d %H:%M UTC").fillna("never")
        figure.add_trace(go.Scatter(
            x=mapped.longitude, y=mapped.latitude, mode="markers", name=name,
            customdata=np.column_stack([mapped.device_id, seen, mapped.reason]) if len(mapped) else None,
            marker={"color": colour, "size": 12, "line": {"color": "white", "width": 1.5}},
            hovertemplate=(f"Station %{{customdata[0]}}<br>{name}<br>Last seen %{{customdata[1]}}"
                           "<br>%{customdata[2]}<extra></extra>")))
    figure.update_xaxes(title="Longitude (°E)", range=[34.1, 34.6])
    figure.update_yaxes(title="Latitude (°N)", range=[0.1, 0.7], scaleanchor="x", scaleratio=1)
    st.plotly_chart(style_figure(figure, 460), width="stretch", config={"displayModeBar": False})
    unmapped = int((status.latitude.isna() | status.longitude.isna()).sum())
    if unmapped:
        st.caption(f"{unmapped} station(s) have no usable coordinates and are not mapped.")
    st.caption("Offline coordinate map within the study bounding box. Green: active. Grey: inactive.")
    st.dataframe(status.assign(last_seen=status.last_seen.dt.strftime("%Y-%m-%d %H:%M UTC"))[
        ["device_id", "is_active", "last_seen", "reason"]].rename(columns={
            "device_id": "Station", "is_active": "Active", "last_seen": "Last reading", "reason": "Reason"}),
        hide_index=True, width="stretch")


# =============================================================================
# Page 6. Academic attribution
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


def main():
    st.set_page_config(page_title="Imarika | Busia Weather Forecasting", page_icon="🌿", layout="wide")
    st.markdown("""
    <style>
      .block-container { max-width: 1280px; padding-top: 2.1rem; }
      h1, h2, h3 { color: #234D28; }
      [data-testid="stMetric"] { background: #F4F8F4; border: 1px solid #DFEADF;
        border-top: 3px solid #2E7D32; border-radius: 8px; padding: 14px; }
      [data-testid="stMetricValue"] { font-size: 1.75rem; }
      .architecture { display: flex; align-items: stretch; gap: 10px; margin: 16px 0; flex-wrap: wrap; }
      .stage { flex: 1; min-width: 145px; padding: 18px 14px; border: 1px solid #CFE0CF;
        border-radius: 8px; background: #F7FAF7; }
      .stage b { display: block; color: #2E7D32; margin-bottom: 10px; }
      .stage span { font-size: 0.88rem; line-height: 1.6; color: #3E5142; }
      .arrow { align-self: center; color: #2E7D32; font-size: 1.4rem; }
    </style>
    """, unsafe_allow_html=True)
    with st.sidebar:
        st.title("Imarika")
        st.caption("BUSIA COUNTY · KENYA")
        page = st.radio("Navigation", PAGES)
        st.divider()
        st.caption("ICS 4101 · Project presentation\n\nLocal data and saved results · Offline-ready")
    {
        "Project Overview": project_overview,
        "Data Collection": data_collection,
        "Model Results": model_results,
        "Forecast and Advisories": forecast_and_advisories,
        "Network Status": network_status,
        "About": about,
    }[page]()


if __name__ == "__main__":
    main()
