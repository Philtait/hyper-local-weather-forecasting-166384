"""Offline Streamlit presentation dashboard for the Imarika project.

Run: streamlit run app.py
Reads local CSV/JSON/Markdown/PNG files only; imports or runs no trained models.
The station map uses local Plotly coordinates rather than st.map's network tile
service, so the presentation remains usable with the network disconnected.
"""

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# =============================================================================
# Presentation settings and schema aliases for the saved preprocessing outputs
# =============================================================================
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
GREEN = "#2E7D32"
RED = "#B3261E"
REPRESENTATIVE = "101990961244800083"
LOCAL_TZ = "Africa/Nairobi"
VARIABLES = {
    "air_temperature": ("Temperature", "°C", ["air_temperature", "iot_air_temperature"]),
    "air_humidity": ("Humidity", "%", ["air_humidity", "iot_air_humidity"]),
    "barometric_pressure_hpa": ("Pressure", "hPa", ["barometric_pressure_hpa", "iot_barometric_pressure"]),
    "wind_speed": ("Wind speed", "m/s", ["wind_speed", "iot_wind_speed"]),
    "rainfall_mm": ("Rainfall", "mm", ["rainfall_mm", "iot_rainfall_mm"]),
}
PAGES = ["Project Overview", "Data Collection", "Model Results", "Live Demo", "About"]

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
    text = load_text(DATA / "busia_stations.json")
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


@st.cache_data(show_spinner=False)
def read_unified_cached(path, signature):
    columns = pd.read_csv(path, nrows=0).columns
    timestamp = next((c for c in ["timestamp", "hour", "received_at"] if c in columns), None)
    if timestamp is None or "device_id" not in columns:
        raise ValueError("expected device_id and timestamp/hour columns")
    aliases = {}
    for target, (_, _, candidates) in VARIABLES.items():
        source = next((c for c in candidates if c in columns), None)
        if source:
            aliases[source] = target
    if not aliases:
        raise ValueError("none of the five weather variables are available")
    frame = pd.read_csv(path, usecols=["device_id", timestamp] + list(aliases),
                        dtype={"device_id": "string"}, low_memory=False)
    frame = frame.rename(columns={timestamp: "timestamp", **aliases})
    frame["device_id"] = clean_ids(frame.device_id)
    frame["timestamp"] = pd.to_datetime(frame.timestamp, format="mixed", utc=True, errors="coerce")
    if frame.timestamp.isna().any() or frame.device_id.isna().any() or frame.device_id.eq("").any():
        raise ValueError("missing/invalid timestamps or station identifiers")
    if not frame.timestamp.eq(frame.timestamp.dt.floor("h")).all():
        raise ValueError("unified timestamps must lie on hourly boundaries")
    if frame.duplicated(["device_id", "timestamp"]).any():
        raise ValueError("duplicate station/hour records")
    for target in aliases.values():
        frame[target] = pd.to_numeric(frame[target], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return frame.loc[frame.device_id.ne("test-spatial-check-device")].sort_values(["device_id", "timestamp"])


def load_unified():
    path = DATA / "unified_dataset.csv"
    try:
        return read_unified_cached(str(path), file_signature(path))
    except (OSError, ValueError, pd.errors.ParserError) as error:
        st.warning(f"Cannot load {display_path(path)}: {error}")
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


def seasonal_persistence(series):
    """Last seven available days and next-day persistence, compared in UTC.

    Accept timezone-aware strings as well as DatetimeIndex input. Convert every
    timestamp to the same timezone BEFORE sorting, filtering, or reindexing;
    merely stripping an offset would change the instant it represents. The end
    is the selected variable's latest non-null reading, not today's date or a
    trailing placeholder row. Missing measurements remain missing.
    """
    if series.empty:
        raise ValueError("An hourly time series is required")
    series = series.copy()
    series.index = pd.DatetimeIndex(pd.to_datetime(series.index, format="mixed", utc=True, errors="coerce"))
    if series.index.isna().any():
        raise ValueError("Invalid hourly timestamps")
    if series.index.has_duplicates:
        raise ValueError("Duplicate hourly timestamps")
    series = series.sort_index()
    end = series.last_valid_index()
    if end is None:
        end = series.index.max()
    # Open left / closed right gives exactly 168 clock hours, including end.
    # Use datetime comparisons, never string/date-only comparisons or wall time.
    start = end - pd.Timedelta(days=7)
    window = series.loc[(series.index > start) & (series.index <= end)]
    actual_index = pd.date_range(end=end, periods=168, freq="h")
    actual = window.reindex(actual_index)
    previous_day = pd.date_range(end - pd.Timedelta(hours=23), end, freq="h")
    future = pd.date_range(end + pd.Timedelta(hours=1), periods=24, freq="h")
    forecast = pd.Series(actual.reindex(previous_day).to_numpy(), index=future, name="Persistence baseline forecast")
    return actual, forecast


def demo_figure(actual, forecast, label, unit):
    figure = go.Figure()
    local_actual = actual.index.tz_convert(LOCAL_TZ).tz_localize(None)
    local_future = forecast.index.tz_convert(LOCAL_TZ).tz_localize(None)
    figure.add_trace(go.Scatter(x=local_actual, y=actual, mode="lines", name="Actual readings",
                               line={"color": GREEN, "width": 2}, connectgaps=False))
    # Include the final historical point solely to visually join the forecast.
    figure.add_trace(go.Scatter(x=[local_actual[-1], *local_future], y=[actual.iloc[-1], *forecast],
                               mode="lines", name="Persistence baseline forecast",
                               line={"color": "#D9822B", "width": 2.5, "dash": "dot"}, connectgaps=False))
    figure.add_vrect(x0=local_actual[-1], x1=local_future[-1], fillcolor="#EDF5ED", opacity=.6,
                    layer="below", line_width=0)
    figure.update_xaxes(title="Time · Africa/Nairobi (UTC+3)")
    figure.update_yaxes(title=f"{label} ({unit})")
    figure.update_layout(hovermode="x unified")
    return style_figure(figure, height=460)


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
# Page 4. Historical interactive demo and next-day seasonal persistence
# =============================================================================
def live_demo():
    st.title("Live Demo")
    st.write("Explore the latest saved station readings and a transparent next-day baseline.")
    frame = load_unified()
    if frame is None or frame.empty:
        st.warning("No unified readings are available for the demo.")
        return
    stations = load_stations()
    if stations is not None:
        frame = frame.loc[frame.device_id.isin(stations.device_id)]
    if frame.empty:
        st.warning("No readings match the available Busia station registry.")
        return
    options = sorted(frame.device_id.unique())
    left, right = st.columns(2)
    with left:
        station = st.selectbox("Station", options, index=options.index(REPRESENTATIVE) if REPRESENTATIVE in options else 0)
    with right:
        available = [target for target in VARIABLES if target in frame]
        variable = st.selectbox("Weather variable", available, format_func=lambda target: VARIABLES[target][0])
    label, unit, _ = VARIABLES[variable]
    series = frame.loc[frame.device_id.eq(station)].set_index("timestamp")[variable]
    actual, forecast = seasonal_persistence(series)
    observed = actual.dropna()
    if observed.empty:
        st.warning("No observed values are available for this station and variable.")
    cards = st.columns(3)
    cards[0].metric("Latest observed value", f"{observed.iloc[-1]:.2f} {unit}" if len(observed) else "Unavailable")
    cards[1].metric("Hours in displayed window", str(len(actual)))
    cards[2].metric("Forecast horizon", "24 hours")
    st.plotly_chart(demo_figure(actual, forecast, label, unit), width="stretch",
                    config={"displayModeBar": False})
    start = actual.index.min().tz_convert(LOCAL_TZ)
    cutoff = actual.index.max().tz_convert(LOCAL_TZ)
    dataset_end = frame.timestamp.max().tz_convert(LOCAL_TZ)
    st.caption(f"Displayed window: {start:%d %b %Y, %H:%M} – {cutoff:%d %b %Y, %H:%M} EAT. "
               f"Dataset latest timestamp: {dataset_end:%d %b %Y, %H:%M} EAT. "
               "The window ends at this station/variable's latest available reading. Solid green: actual data. "
                "Dotted orange: Persistence baseline forecast.")
    missing_actual = int(actual.isna().sum())
    st.caption(f"Observed readings: {len(observed)} / {len(actual)} hourly slots. Missing values: {missing_actual}.")
    if missing_actual:
        st.info(f"The full seven-day window is displayed, but {missing_actual} hourly measurements are missing "
                "in the saved data. Gaps in the line reflect unavailable readings, not timezone filtering.")
    st.info("Persistence baseline forecast: each of the next 24 hours repeats the corresponding value from the previous 24 hours. "
            "This is seasonal persistence, displayed from saved data without model inference.")
    missing = int(forecast.isna().sum())
    if missing:
        st.warning(f"{missing} forecast hour(s) remain missing because the corresponding previous-day reading is missing. No values are fabricated.")


# =============================================================================
# Page 5. Academic attribution
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
        "Live Demo": live_demo,
        "About": about,
    }[page]()


if __name__ == "__main__":
    main()
