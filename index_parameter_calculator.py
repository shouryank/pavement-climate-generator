"""
Index Parameter Calculator – Backend
====================================
Computes climate index parameters from CMIP model data (LOCA downscaled)
for baseline (historic) and future periods, comparing them against observed
historic data extracted from HCD files.

Developer note
--------------
This version is a corrective rewrite intended to align the implementation
more closely with the uploaded documentation.

What was wrong before:
- PG High and PG Low used simplified approximations that did not follow the
  documented report equations closely enough.
- PG change-factor and final projected PG logic depended on approximate
  time-series summaries rather than period-level climate statistics.
- Heatwave threshold was hard-coded without a clear configuration path.
- "Mean annual precipitation" comments were misleading.
- Precipitation return-period logic was mixed in with undocumented assumptions.

What was changed:
- Added explicit period-level PG statistic helpers.
- Implemented reconstructed LTPP-style PG formulas using the constants visible
  in the uploaded report.
- Added reliability selection (50% or 98%) support.
- Kept time-series CSV outputs backward-compatible.
- Labeled precipitation return-period logic clearly as an approximation.

What remains approximate:
- The full FHWA precipitation quantile-ratio / confidence-interval method from
  the CMIP documentation is not fully reproduced here because the exact
  equations were not fully recoverable from the parsed PDF text.
"""

from __future__ import annotations

import csv
import math
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, pstdev, stdev
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

from main_v3 import available_years_for_model, load_daily_series_for_year
from operation_control import check_cancelled

# ---------------------------------------------------------------------------
# Constants / configuration
# ---------------------------------------------------------------------------

MM_TO_INCH = 0.03937007874
INCH_TO_MM = 25.4

FREEZING_F = 32.0
DEFAULT_HEATWAVE_THRESHOLD_F = 95.0  # configurable default
EPS = 1e-9
HISTORICAL_SCENARIO = "historical"

SUMMER_MONTHS = {6, 7, 8}
WINTER_MONTHS = {12, 1, 2}

SEASON_MAP = {
    "DJF": {12, 1, 2},
    "MAM": {3, 4, 5},
    "JJA": {6, 7, 8},
    "SON": {9, 10, 11},
}

PG_RELIABILITY_MAP = {
    "50%": 0.0,
    "98%": 2.055,
}

AEP_LEVELS: List[Tuple[float, str]] = [
    (0.50, "50% AEP (2-yr)"),
    (0.20, "20% AEP (5-yr)"),
    (0.10, "10% AEP (10-yr)"),
    (0.04, "4% AEP (25-yr)"),
    (0.02, "2% AEP (50-yr)"),
    (0.01, "1% AEP (100-yr)"),
    (0.002, "0.2% AEP (500-yr)"),
]
RETURN_PERIOD_AEP = 0.01

MONTH_LABELS: List[Tuple[int, str]] = [
    (1, "Jan"),
    (2, "Feb"),
    (3, "Mar"),
    (4, "Apr"),
    (5, "May"),
    (6, "Jun"),
    (7, "Jul"),
    (8, "Aug"),
    (9, "Sep"),
    (10, "Oct"),
    (11, "Nov"),
    (12, "Dec"),
]

SEASON_LABELS: List[Tuple[str, str]] = [
    ("DJF", "Winter"),
    ("MAM", "Spring"),
    ("JJA", "Summer"),
    ("SON", "Fall"),
]

DailyRecord = Tuple[date, float, float, float]  # (date, tmax_F, tmin_F, precip_in)
YearlyDaily = Dict[int, List[DailyRecord]]
ScalarSeries = Dict[int, Optional[float]]
SeasonalSeries = Dict[int, Dict[str, Optional[float]]]

# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------


def f_to_c(value_f: float) -> float:
    return (value_f - 32.0) * 5.0 / 9.0


def c_to_f(value_c: float) -> float:
    return value_c * 9.0 / 5.0 + 32.0


def safe_mean(values: Iterable[Optional[float]]) -> Optional[float]:
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    return mean(filtered)


def safe_pstdev(values: Iterable[Optional[float]]) -> float:
    filtered = [v for v in values if v is not None]
    if len(filtered) < 2:
        return 0.0
    return pstdev(filtered)


def safe_stdev(values: Iterable[Optional[float]]) -> float:
    filtered = [v for v in values if v is not None]
    if len(filtered) < 2:
        return 0.0
    return stdev(filtered)


def pct_change(new_value: Optional[float], baseline_value: Optional[float]) -> Optional[float]:
    if new_value is None or baseline_value is None or abs(baseline_value) <= EPS:
        return None
    return ((new_value - baseline_value) / abs(baseline_value)) * 100.0


# ---------------------------------------------------------------------------
# HCD parsing (historic / observed data)
# ---------------------------------------------------------------------------


def _parse_hcd_daily(hcd_path: Union[str, Path]) -> YearlyDaily:
    """
    Parse an HCD file and return daily records:
        { year: [ (date, tmax_F, tmin_F, precip_in), ... ] }

    Assumes hourly CSV lines where:
      timestamp is parts[0] in YYYYMMDDHH form
      temperature is parts[1] in °F
      precipitation is parts[-2] in inches
    """
    hcd_path = Path(hcd_path)
    if not hcd_path.exists():
        raise FileNotFoundError(f"HCD file not found: {hcd_path}")

    grouped: Dict[Tuple[int, int, int], Dict[str, List[float]]] = {}

    with hcd_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if "," not in line:
                continue

            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue

            try:
                ts = parts[0]
                if len(ts) < 10:
                    continue
                yr = int(ts[:4])
                mo = int(ts[4:6])
                dy = int(ts[6:8])
                temp_f = float(parts[1])
                precip_in = float(parts[-2])
            except (ValueError, IndexError):
                continue

            key = (yr, mo, dy)
            grouped.setdefault(key, {"temps": [], "precip": []})
            grouped[key]["temps"].append(temp_f)
            grouped[key]["precip"].append(precip_in)

    result: YearlyDaily = {}
    for (yr, mo, dy), vals in sorted(grouped.items()):
        tmax_f = max(vals["temps"])
        tmin_f = min(vals["temps"])
        precip_in = sum(vals["precip"])
        result.setdefault(yr, []).append((date(yr, mo, dy), tmax_f, tmin_f, precip_in))

    return result


def load_historic_daily_from_hcd(hcd_path: Union[str, Path], baseline_start: int, baseline_end: int) -> YearlyDaily:
    all_daily = _parse_hcd_daily(hcd_path)
    return {yr: days for yr, days in all_daily.items() if baseline_start <= yr <= baseline_end}


# ---------------------------------------------------------------------------
# Model daily loader
# ---------------------------------------------------------------------------


def _daily_from_model_year(
    year: int,
    model_base: Path,
    model_name: str,
    scenario: str,
    lon: float,
    lat: float,
) -> List[DailyRecord]:
    """
    Load CMIP model daily data for one year.

    Assumes load_daily_series_for_year returns:
      tmax_vals, tmin_vals, pr_vals, n_days
    in Fahrenheit / Fahrenheit / inches already.
    """
    tmax_vals, tmin_vals, pr_vals, n_days = load_daily_series_for_year(
        year,
        model_base,
        model_name,
        scenario,
        target_lon=lon,
        target_lat=lat,
    )
    start = date(year, 1, 1)
    records: List[DailyRecord] = []
    for d in range(n_days):
        dt = start + timedelta(days=d)
        records.append((dt, tmax_vals[d], tmin_vals[d], pr_vals[d]))
    return records


def load_model_daily(
    scenario_root: Union[str, Path],
    scenario: str,
    model: str,
    year_start: int,
    year_end: int,
    lon: float,
    lat: float,
    cancel_event=None,
) -> YearlyDaily:
    model_base = Path(scenario_root) / scenario / model
    available_years = set(available_years_for_model(model_base, required_vars=("tasmax", "tasmin", "pr")))
    result: YearlyDaily = {}
    for yr in range(year_start, year_end + 1):
        check_cancelled(cancel_event)
        if available_years and yr not in available_years:
            continue
        try:
            result[yr] = _daily_from_model_year(yr, model_base, model, scenario, lon, lat)
        except Exception as exc:
            print(f"Warning: Could not load model data for {scenario}/{model}/{yr}: {exc}")
    return result


# ---------------------------------------------------------------------------
# Time-series statistic helpers
# ---------------------------------------------------------------------------


def longest_run_above_threshold(days: List[DailyRecord], threshold_f: float) -> int:
    max_run = 0
    current = 0
    for _, tmax_f, _, _ in days:
        if tmax_f > threshold_f:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run


def fir_moving_average(values: List[float], window_size: int) -> List[float]:
    """Rectangular FIR moving average used for the PG high temperature window."""
    if window_size <= 0 or len(values) < window_size:
        return []

    running_sum = sum(values[:window_size])
    averages = [running_sum / window_size]
    for idx in range(window_size, len(values)):
        running_sum += values[idx] - values[idx - window_size]
        averages.append(running_sum / window_size)
    return averages


def annual_hottest_7day_avg_max(days: List[DailyRecord]) -> Optional[float]:
    """
    Compute the annual hottest 7-day average of daily max temperature (°F).
    This is the key hot-weather air statistic used for PG high calculations.
    """
    if len(days) < 7:
        return None
    tmax_values = [tmax for _, tmax, _, _ in days]
    rolling = fir_moving_average(tmax_values, 7)
    return max(rolling) if rolling else None


def annual_absolute_min_air(days: List[DailyRecord]) -> Optional[float]:
    if not days:
        return None
    return min(tmin for _, _, tmin, _ in days)


def annual_max_daily_precip(days: List[DailyRecord]) -> Optional[float]:
    if not days:
        return None
    return max(p for _, _, _, p in days)


def annual_total_precip(days: List[DailyRecord]) -> Optional[float]:
    if not days:
        return None
    return sum(p for _, _, _, p in days)


def annual_extreme_heat_value(days: List[DailyRecord]) -> Optional[float]:
    if not days:
        return None
    return max(tmax for _, tmax, _, _ in days)


def annual_extreme_cold_value(days: List[DailyRecord]) -> Optional[float]:
    if not days:
        return None
    return min(tmin for _, _, tmin, _ in days)


def annual_mean_daily_max(days: List[DailyRecord]) -> Optional[float]:
    if not days:
        return None
    return mean(tmax for _, tmax, _, _ in days)


def annual_mean_daily_min(days: List[DailyRecord]) -> Optional[float]:
    if not days:
        return None
    return mean(tmin for _, _, tmin, _ in days)


def _season_year_and_name(dt: date) -> Tuple[int, str]:
    """
    Map a calendar date to a meteorological season and its season-year.

    DJF is treated as a cross-year season:
    - Jan/Feb belong to DJF of the same year
    - Dec belongs to DJF of the following year
    """
    month = dt.month
    if month in {12, 1, 2}:
        return (dt.year + 1, "DJF") if month == 12 else (dt.year, "DJF")
    if month in {3, 4, 5}:
        return dt.year, "MAM"
    if month in {6, 7, 8}:
        return dt.year, "JJA"
    return dt.year, "SON"


def summarize_annual_max_series(yearly_daily: YearlyDaily) -> List[float]:
    series: List[float] = []
    for yr in sorted(yearly_daily):
        value = annual_max_daily_precip(yearly_daily[yr])
        if value is not None:
            series.append(value)
    return series


# ---------------------------------------------------------------------------
# Period-level PG statistic helpers
# ---------------------------------------------------------------------------


def extract_pg_high_air_statistics(yearly_daily: YearlyDaily) -> Tuple[Optional[float], float]:
    """
    Returns:
      mean hottest-7day-average annual statistic (°C),
      std dev of hottest-7day-average annual statistic (°C)
    """
    annual_values_f = [annual_hottest_7day_avg_max(days) for _, days in sorted(yearly_daily.items())]
    annual_values_f = [v for v in annual_values_f if v is not None]
    if not annual_values_f:
        return None, 0.0

    annual_values_c = [f_to_c(v) for v in annual_values_f]
    return safe_mean(annual_values_c), safe_stdev(annual_values_c)


def extract_pg_low_air_statistics(yearly_daily: YearlyDaily) -> Tuple[Optional[float], float]:
    """
    Returns:
      mean annual absolute minimum air temperature statistic (°C),
      std dev of annual absolute minimum air temperature statistic (°C)
    """
    annual_values_f = [annual_absolute_min_air(days) for _, days in sorted(yearly_daily.items())]
    annual_values_f = [v for v in annual_values_f if v is not None]
    if not annual_values_f:
        return None, 0.0

    annual_values_c = [f_to_c(v) for v in annual_values_f]
    return safe_mean(annual_values_c), safe_stdev(annual_values_c)


# ---------------------------------------------------------------------------
# PG formula implementations
# ---------------------------------------------------------------------------


def pg_high_from_air_stats(t_air_high_c: Optional[float], sigma_air_high_c: float, latitude_deg: float, z: float) -> Optional[float]:
    """
    Reconstructed LTPP-style PG high equation consistent with the uploaded report.

    Implemented as:
        T_high = ((T_air,high + z*σ_air,high)
                  - 0.00618*Lat^2 + 0.2289*Lat + 42.2) * 0.9545 - 17.78

    Notes:
    - Constants 0.00618, 0.2289, 0.9545, and 17.78 are visible in the report.
    - The 42.2 constant is part of the standard LTPPBind high-temperature form
      and is included here to complete the equation structure.
    """
    if t_air_high_c is None:
        return None

    return (
        (
            (t_air_high_c + z * sigma_air_high_c)
            - 0.00618 * (latitude_deg ** 2)
            + 0.2289 * latitude_deg
            + 42.2
        )
        * 0.9545
        - 17.78
    )


def pg_low_from_air_stats(t_air_low_c: Optional[float], sigma_air_low_c: float, latitude_deg: float, z: float) -> Optional[float]:
    """
    Reconstructed LTPP-style PG low equation consistent with the uploaded report.

    Implemented as:
        T_low = 7.191 + 0.72*T_air,low - 0.004*Lat^2
                - z*sqrt(4.4 + 0.52*(σ_air,low^2))

    Notes:
    - This matches the visible constants in the uploaded report:
      7.191, 0.72, 0.004, 4.4, and 0.52.
    """
    if t_air_low_c is None:
        return None

    return (
        7.191
        + 0.72 * t_air_low_c
        - 0.004 * (latitude_deg ** 2)
        - z * math.sqrt(4.4 + 0.52 * (sigma_air_low_c ** 2))
    )


def calc_pg_high_temperature(yearly_daily: YearlyDaily, latitude_deg: float, reliability_z: float) -> ScalarSeries:
    """
    Year-by-year PG high series.

    For yearly series we compute the annual hottest 7-day average max air temp
    and apply the PG high relationship with sigma=0.0 for that single-year value.
    Period-level sigma is used separately for summary/change-factor calculations.
    """
    result: ScalarSeries = {}
    for yr, days in yearly_daily.items():
        t_air_high_f = annual_hottest_7day_avg_max(days)
        if t_air_high_f is None:
            result[yr] = None
            continue
        result[yr] = pg_high_from_air_stats(
            t_air_high_c=f_to_c(t_air_high_f),
            sigma_air_high_c=0.0,
            latitude_deg=latitude_deg,
            z=reliability_z,
        )
    return result


def calc_pg_low_temperature(yearly_daily: YearlyDaily, latitude_deg: float, reliability_z: float) -> ScalarSeries:
    """
    Year-by-year PG low series.

    For yearly series we compute the annual absolute minimum air temperature and
    apply the PG low relationship with sigma=0.0 for that single-year value.
    Period-level sigma is used separately for summary/change-factor calculations.
    """
    result: ScalarSeries = {}
    for yr, days in yearly_daily.items():
        t_air_low_f = annual_absolute_min_air(days)
        if t_air_low_f is None:
            result[yr] = None
            continue
        result[yr] = pg_low_from_air_stats(
            t_air_low_c=f_to_c(t_air_low_f),
            sigma_air_low_c=0.0,
            latitude_deg=latitude_deg,
            z=reliability_z,
        )
    return result


def summarize_pg_high_period(yearly_daily: YearlyDaily, latitude_deg: float, reliability_z: float) -> Optional[float]:
    t_air_high_c, sigma_air_high_c = extract_pg_high_air_statistics(yearly_daily)
    return pg_high_from_air_stats(t_air_high_c, sigma_air_high_c, latitude_deg, reliability_z)


def summarize_pg_low_period(yearly_daily: YearlyDaily, latitude_deg: float, reliability_z: float) -> Optional[float]:
    t_air_low_c, sigma_air_low_c = extract_pg_low_air_statistics(yearly_daily)
    return pg_low_from_air_stats(t_air_low_c, sigma_air_low_c, latitude_deg, reliability_z)


def calc_pg_change_factor(baseline_summary: Optional[float], future_summary: Optional[float]) -> Optional[float]:
    """
    Equation (5) style change factor:
        CF_T = T_projection - T_backcast
    """
    if baseline_summary is None or future_summary is None:
        return None
    return future_summary - baseline_summary


def calc_final_projected_pg(observed_pg: Optional[float], change_factor: Optional[float]) -> Optional[float]:
    """
    Equation (6) style final projected PG:
        T_final = T_observed + CF_T
    """
    if observed_pg is None or change_factor is None:
        return None
    return observed_pg + change_factor


def standard_pg_low_grade(pg_low_c: Optional[float]) -> Optional[float]:
    if pg_low_c is None:
        return None
    return float(6 * math.floor((pg_low_c - 8.0) / 6.0) + 8.0)


def standard_pg_high_grade(pg_high_c: Optional[float]) -> Optional[float]:
    if pg_high_c is None:
        return None
    return float(6 * math.floor((pg_high_c - 4.01) / 6.0) + 10.0)


# ---------------------------------------------------------------------------
# Climate index calculation functions
# ---------------------------------------------------------------------------


def calc_mean_annual_temperature(yearly_daily: YearlyDaily) -> ScalarSeries:
    result: ScalarSeries = {}
    for yr, days in yearly_daily.items():
        daily_avg = [((tmax + tmin) / 2.0) for _, tmax, tmin, _ in days]
        result[yr] = mean(daily_avg) if daily_avg else None
    return result


def calc_max_summer_temperature(yearly_daily: YearlyDaily) -> ScalarSeries:
    result: ScalarSeries = {}
    for yr, days in yearly_daily.items():
        values = [tmax for dt, tmax, _, _ in days if dt.month in SUMMER_MONTHS]
        result[yr] = max(values) if values else None
    return result


def calc_min_winter_temperature(yearly_daily: YearlyDaily) -> ScalarSeries:
    result: ScalarSeries = {yr: None for yr in yearly_daily}
    winter_values: Dict[int, List[float]] = {yr: [] for yr in yearly_daily}

    for days in yearly_daily.values():
        for dt, _, tmin, _ in days:
            season_year, season_name = _season_year_and_name(dt)
            if season_name == "DJF" and season_year in winter_values:
                winter_values[season_year].append(tmin)

    for yr, values in winter_values.items():
        result[yr] = min(values) if values else None
    return result


def calc_average_annual_max_temperature(yearly_daily: YearlyDaily) -> ScalarSeries:
    return {yr: annual_mean_daily_max(days) for yr, days in yearly_daily.items()}


def calc_average_annual_min_temperature(yearly_daily: YearlyDaily) -> ScalarSeries:
    return {yr: annual_mean_daily_min(days) for yr, days in yearly_daily.items()}


def calc_freezing_days(yearly_daily: YearlyDaily) -> ScalarSeries:
    result: ScalarSeries = {}
    for yr, days in yearly_daily.items():
        result[yr] = float(sum(1 for _, _, tmin, _ in days if tmin <= FREEZING_F))
    return result


def calc_heatwave_duration(yearly_daily: YearlyDaily, threshold_f: float = DEFAULT_HEATWAVE_THRESHOLD_F) -> ScalarSeries:
    result: ScalarSeries = {}
    for yr, days in yearly_daily.items():
        result[yr] = float(longest_run_above_threshold(days, threshold_f))
    return result


def calc_annual_extreme_heat(yearly_daily: YearlyDaily) -> ScalarSeries:
    return {yr: annual_extreme_heat_value(days) for yr, days in yearly_daily.items()}


def calc_seasonal_extreme_heat(yearly_daily: YearlyDaily) -> SeasonalSeries:
    result: SeasonalSeries = {
        yr: {season_name: None for season_name in SEASON_MAP}
        for yr in yearly_daily
    }
    season_values: Dict[int, Dict[str, List[float]]] = {
        yr: {season_name: [] for season_name in SEASON_MAP}
        for yr in yearly_daily
    }

    for days in yearly_daily.values():
        for dt, tmax, _, _ in days:
            season_year, season_name = _season_year_and_name(dt)
            if season_year in season_values:
                season_values[season_year][season_name].append(tmax)

    for yr, seasons in season_values.items():
        for season_name, values in seasons.items():
            result[yr][season_name] = max(values) if values else None
    return result


def calc_extreme_cold(yearly_daily: YearlyDaily) -> ScalarSeries:
    return {yr: annual_extreme_cold_value(days) for yr, days in yearly_daily.items()}


def calc_mean_annual_precipitation(yearly_daily: YearlyDaily) -> ScalarSeries:
    """
    Annual total precipitation by year (inches/year).

    This name is kept for UI/backward compatibility, but the value returned is
    the total precipitation accumulated within each year.
    """
    return {yr: annual_total_precip(days) for yr, days in yearly_daily.items()}


def calc_max_24hr_precipitation(yearly_daily: YearlyDaily) -> ScalarSeries:
    return {yr: annual_max_daily_precip(days) for yr, days in yearly_daily.items()}


def calc_std_dev_extreme_rainfall(yearly_daily: YearlyDaily) -> ScalarSeries:
    """
    Period standard deviation of annual maximum daily precipitation.

    Returns the same period-level sigma for each year for compatibility with the
    existing summary / CSV flow.
    """
    annual_maxes = summarize_annual_max_series(yearly_daily)
    sigma = safe_pstdev(annual_maxes)
    return {yr: sigma for yr in yearly_daily}


def _gumbel_parameters_from_annual_maxes(annual_maxes: List[float]) -> Tuple[Optional[float], Optional[float]]:
    if not annual_maxes:
        return None, None

    sigma = safe_pstdev(annual_maxes)
    mu = mean(annual_maxes)
    if abs(sigma) <= EPS:
        return mu, 0.0

    beta = (math.sqrt(6.0) * sigma) / math.pi
    location = mu - 0.5772 * beta
    return location, beta


def _gumbel_quantile(location: Optional[float], beta: Optional[float], aep: float) -> Optional[float]:
    if location is None or beta is None:
        return None
    if abs(beta) <= EPS:
        return location
    non_exceedance = 1.0 - aep
    return location - beta * math.log(-math.log(non_exceedance))


def _constant_series(yearly_daily: YearlyDaily, value: Optional[float]) -> ScalarSeries:
    return {yr: value for yr in yearly_daily}


def _percentile(values: List[float], probability: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])

    ordered = sorted(float(v) for v in values)
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _period_tmax_percentile(yearly_daily: YearlyDaily, probability: float) -> Optional[float]:
    return _percentile([tmax for days in yearly_daily.values() for _, tmax, _, _ in days], probability)


def _period_tmin_percentile(yearly_daily: YearlyDaily, probability: float) -> Optional[float]:
    return _percentile([tmin for days in yearly_daily.values() for _, _, tmin, _ in days], probability)


def _period_nonzero_precip_percentile(yearly_daily: YearlyDaily, probability: float) -> Optional[float]:
    return _percentile(
        [precip for days in yearly_daily.values() for _, _, _, precip in days if precip > 0.0],
        probability,
    )


def _season_days_by_year(yearly_daily: YearlyDaily, season_code: str) -> Dict[int, List[DailyRecord]]:
    grouped: Dict[int, List[DailyRecord]] = {yr: [] for yr in yearly_daily}
    for days in yearly_daily.values():
        for record in days:
            dt = record[0]
            season_year, derived_season = _season_year_and_name(dt)
            if derived_season == season_code and season_year in grouped:
                grouped[season_year].append(record)
    return grouped


def _running_average(values: List[float], window_size: int) -> List[float]:
    return fir_moving_average(values, window_size)


def _running_sum(values: List[float], window_size: int) -> List[float]:
    if window_size <= 0 or len(values) < window_size:
        return []
    running_sum = sum(values[:window_size])
    sums = [running_sum]
    for idx in range(window_size, len(values)):
        running_sum += values[idx] - values[idx - window_size]
        sums.append(running_sum)
    return sums


def _longest_run_threshold(days: List[DailyRecord], threshold_f: float, extractor: Callable[[DailyRecord], float]) -> float:
    max_run = 0
    current = 0
    for record in days:
        if extractor(record) >= threshold_f:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return float(max_run)


def _count_threshold(days: List[DailyRecord], threshold_f: float, extractor: Callable[[DailyRecord], float]) -> float:
    return float(sum(1 for record in days if extractor(record) >= threshold_f))


def _freeze_thaw_fluctuations(days: List[DailyRecord]) -> float:
    fluctuations = 0
    previous_state: Optional[bool] = None
    for _, _, tmin, _ in sorted(days, key=lambda item: item[0]):
        current_state = tmin <= FREEZING_F
        if previous_state is not None and current_state != previous_state:
            fluctuations += 1
        previous_state = current_state
    return float(fluctuations)


def _monthly_total_precipitation(yearly_daily: YearlyDaily, month: int) -> ScalarSeries:
    result: ScalarSeries = {}
    for yr, days in yearly_daily.items():
        result[yr] = sum(precip for dt, _, _, precip in days if dt.month == month)
    return result


def _seasonal_total_precipitation(yearly_daily: YearlyDaily, season_code: str) -> ScalarSeries:
    result: ScalarSeries = {}
    season_days = _season_days_by_year(yearly_daily, season_code)
    for yr, days in season_days.items():
        result[yr] = sum(precip for _, _, _, precip in days) if days else None
    return result


def _seasonal_days_above_temperature_threshold(yearly_daily: YearlyDaily, season_code: str, threshold_f: float) -> ScalarSeries:
    result: ScalarSeries = {}
    season_days = _season_days_by_year(yearly_daily, season_code)
    for yr, days in season_days.items():
        result[yr] = _count_threshold(days, threshold_f, lambda record: record[1]) if days else None
    return result


def _summer_average_temperature(yearly_daily: YearlyDaily) -> ScalarSeries:
    result: ScalarSeries = {}
    for yr, days in yearly_daily.items():
        daily_mean = [((tmax + tmin) / 2.0) for dt, tmax, tmin, _ in days if dt.month in SUMMER_MONTHS]
        result[yr] = mean(daily_mean) if daily_mean else None
    return result


def _summer_running_high_average(yearly_daily: YearlyDaily, window_size: int) -> ScalarSeries:
    result: ScalarSeries = {}
    for yr, days in yearly_daily.items():
        summer_tmax = [tmax for dt, tmax, _, _ in days if dt.month in SUMMER_MONTHS]
        rolling = _running_average(summer_tmax, window_size)
        result[yr] = max(rolling) if rolling else None
    return result


def _winter_average_temperature(yearly_daily: YearlyDaily) -> ScalarSeries:
    result: ScalarSeries = {}
    season_days = _season_days_by_year(yearly_daily, "DJF")
    for yr, days in season_days.items():
        daily_mean = [((tmax + tmin) / 2.0) for _, tmax, tmin, _ in days]
        result[yr] = mean(daily_mean) if daily_mean else None
    return result


def _winter_running_low_average(yearly_daily: YearlyDaily, window_size: int) -> ScalarSeries:
    result: ScalarSeries = {}
    season_days = _season_days_by_year(yearly_daily, "DJF")
    for yr, days in season_days.items():
        winter_tmin = [tmin for _, _, tmin, _ in days]
        rolling = _running_average(winter_tmin, window_size)
        result[yr] = min(rolling) if rolling else None
    return result


def _largest_3day_precipitation_for_season(yearly_daily: YearlyDaily, season_code: str) -> ScalarSeries:
    result: ScalarSeries = {}
    season_days = _season_days_by_year(yearly_daily, season_code)
    for yr, days in season_days.items():
        precip_values = [precip for _, _, _, precip in days]
        rolling = _running_sum(precip_values, 3)
        result[yr] = max(rolling) if rolling else None
    return result


def _aep_key(label: str) -> str:
    return label.split(" ", 1)[0].replace(".", "p").replace("%", "pct").replace("-", "_").lower()


def _compute_aep_quantiles(yearly_daily: YearlyDaily) -> Dict[str, Optional[float]]:
    annual_maxes = summarize_annual_max_series(yearly_daily)
    location, beta = _gumbel_parameters_from_annual_maxes(annual_maxes)
    return {
        _aep_key(label): _gumbel_quantile(location, beta, aep)
        for aep, label in AEP_LEVELS
    }


def _compute_quantile_ratio_dict(
    baseline_quantiles: Dict[str, Optional[float]],
    future_quantiles: Dict[str, Optional[float]],
) -> Dict[str, Optional[float]]:
    ratios: Dict[str, Optional[float]] = {}
    for key in sorted(set(baseline_quantiles) | set(future_quantiles)):
        baseline_value = baseline_quantiles.get(key)
        future_value = future_quantiles.get(key)
        if baseline_value is None or future_value is None or abs(baseline_value) <= EPS:
            ratios[key] = None
        else:
            ratios[key] = future_value / baseline_value
    return ratios


def _build_reference_thresholds(yearly_daily: YearlyDaily) -> Dict[str, Optional[float]]:
    return {
        "baseline_very_hot_threshold": _period_tmax_percentile(yearly_daily, 0.95),
        "baseline_extremely_hot_threshold": _period_tmax_percentile(yearly_daily, 0.99),
        "baseline_very_heavy_precip_threshold": _period_nonzero_precip_percentile(yearly_daily, 0.95),
        "baseline_extremely_heavy_precip_threshold": _period_nonzero_precip_percentile(yearly_daily, 0.99),
    }




def calc_return_period_rainfall(yearly_daily: YearlyDaily) -> ScalarSeries:
    """
    Return the 100-year rainfall estimate using Gumbel distribution.

    Workflow:
    - extract annual maximum daily precipitation
    - fit Gumbel (Type I Extreme Value) distribution via method of moments
    - evaluate the 100-year return level (1% AEP)

    Uses the formula: x_T = u + alpha * y_T
    where y_T = -ln(-ln(1 - 1/T)), u and alpha are Gumbel parameters
    """
    annual_maxes = summarize_annual_max_series(yearly_daily)
    location, beta = _gumbel_parameters_from_annual_maxes(annual_maxes)
    quantile = _gumbel_quantile(location, beta, RETURN_PERIOD_AEP)

    return {yr: quantile for yr in yearly_daily}


def calc_standard_pg_high_grade(yearly_daily: YearlyDaily, latitude_deg: float, reliability_z: float) -> ScalarSeries:
    pg_high_series = calc_pg_high_temperature(yearly_daily, latitude_deg, reliability_z)
    return {yr: standard_pg_high_grade(value) for yr, value in pg_high_series.items()}


def calc_standard_pg_low_grade(yearly_daily: YearlyDaily, latitude_deg: float, reliability_z: float) -> ScalarSeries:
    pg_low_series = calc_pg_low_temperature(yearly_daily, latitude_deg, reliability_z)
    return {yr: standard_pg_low_grade(value) for yr, value in pg_low_series.items()}


# ---------------------------------------------------------------------------
# Registry of all index parameters
# ---------------------------------------------------------------------------

CURRENT_TO_CMIP_NAME_MAP = {
    "Mean Annual Temperature": "Average Annual Mean Temperature",
    "Annual Extreme Heat": "Hottest Temperature of the Year",
    "Extreme Cold": "Coldest Temperature of the Year",
    "Mean Annual Precipitation": "Average Total Annual Precipitation",
    "Return Period Rainfall (100-yr)": "Ratio of 24-hour Precipitation Quantiles",
}

PARAMETER_METADATA: List[Dict[str, Any]] = []


def _register_parameter(
    key: str,
    display_name: str,
    parameter_type: str,
    unit: str,
    category: str,
    section: str,
    reason: str = "",
) -> None:
    PARAMETER_METADATA.append(
        {
            "key": key,
            "display_name": display_name,
            "parameter_type": parameter_type,
            "unit": unit,
            "category": category,
            "section": section,
            "reason": reason,
        }
    )


CMIP_SECTION = "CMIP Tool Parameters"
PG_SECTION = "Pavement PG Parameters"

_register_parameter("mean_annual_temp", "Average Annual Mean Temperature", "temp", "°F", "Annual Temperature Averages", CMIP_SECTION)
_register_parameter("avg_annual_max_temp", "Average Annual Maximum Temperature", "temp", "°F", "Annual Temperature Averages", CMIP_SECTION)
_register_parameter("avg_annual_min_temp", "Average Annual Minimum Temperature", "temp", "°F", "Annual Temperature Averages", CMIP_SECTION)
_register_parameter("annual_extreme_heat", "Hottest Temperature of the Year", "temp", "°F", "Annual Extreme Heat", CMIP_SECTION)
_register_parameter("very_hot_day_temp", "Very Hot Day Temperature", "temp", "°F", "Annual Extreme Heat", CMIP_SECTION)
_register_parameter("extremely_hot_day_temp", "Extremely Hot Day Temperature", "temp", "°F", "Annual Extreme Heat", CMIP_SECTION)
_register_parameter("avg_days_above_baseline_very_hot", "Average Number of Days per Year above Baseline Very Hot Temperature", "temp", "days", "Annual Extreme Heat", CMIP_SECTION)
_register_parameter("avg_days_above_baseline_extremely_hot", "Average Number of Days per Year above Baseline Extremely Hot Temperature", "temp", "days", "Annual Extreme Heat", CMIP_SECTION)
for threshold in (95, 100, 105, 110):
    _register_parameter(f"avg_days_above_{threshold}", f"Average Number of Days per Year above {threshold}°F", "temp", "days", "Annual Extreme Heat", CMIP_SECTION)
_register_parameter("max_consecutive_days_above_baseline_very_hot", "Maximum Number of Consecutive Days per Year above Baseline Very Hot Temperature", "temp", "days", "Annual Extreme Heat", CMIP_SECTION)
_register_parameter("max_consecutive_days_above_baseline_extremely_hot", "Maximum Number of Consecutive Days per Year above Baseline Extremely Hot Temperature", "temp", "days", "Annual Extreme Heat", CMIP_SECTION)
for threshold in (95, 100, 105, 110):
    _register_parameter(f"max_consecutive_days_above_{threshold}", f"Maximum Number of Consecutive Days per Year above {threshold}°F", "temp", "days", "Annual Extreme Heat", CMIP_SECTION)
_register_parameter("average_summer_temperature", "Average Summer Temperature", "temp", "°F", "Seasonal Extreme Heat", CMIP_SECTION)
_register_parameter("highest_4day_average_summer_high_temperature", "Highest 4-Day Average Summer High Temperature", "temp", "°F", "Seasonal Extreme Heat", CMIP_SECTION)
_register_parameter("highest_7day_average_summer_high_temperature", "Highest 7-Day Average Summer High Temperature", "temp", "°F", "Seasonal Extreme Heat", CMIP_SECTION)
for _season_code, season_name in SEASON_LABELS:
    for threshold in (95, 100, 105, 110):
        _register_parameter(f"days_{season_name.lower()}_above_{threshold}", f"Number of Days in {season_name} above {threshold}°F", "temp", "days", "Seasonal Extreme Heat", CMIP_SECTION)
_register_parameter("extreme_cold", "Coldest Temperature of the Year", "temp", "°F", "Extreme Cold", CMIP_SECTION)
_register_parameter("very_cold_day_temp", "Very Cold Day Temperature", "temp", "°F", "Extreme Cold", CMIP_SECTION)
_register_parameter("extremely_cold_day_temp", "Extremely Cold Day Temperature", "temp", "°F", "Extreme Cold", CMIP_SECTION)
_register_parameter("avg_days_below_freezing", "Average Number of Days per Year Below Freezing", "temp", "days", "Extreme Cold", CMIP_SECTION)
_register_parameter("avg_freeze_thaw_fluctuations", "Average Number of Times per Year Low Temperatures Fluctuate around Freezing", "temp", "count", "Extreme Cold", CMIP_SECTION)
_register_parameter("average_winter_temperature", "Average Winter Temperature", "temp", "°F", "Extreme Cold", CMIP_SECTION)
_register_parameter("lowest_4day_average_winter_low_temperature", "Lowest 4-Day Average Winter Low Temperature", "temp", "°F", "Extreme Cold", CMIP_SECTION)
_register_parameter("lowest_7day_average_winter_low_temperature", "Lowest 7-Day Average Winter Low Temperature", "temp", "°F", "Extreme Cold", CMIP_SECTION)
_register_parameter("mean_annual_precip", "Average Total Annual Precipitation", "precip", "inches", "Average Precipitation Outputs", CMIP_SECTION)
for month_num, month_name in MONTH_LABELS:
    _register_parameter(f"monthly_precip_{month_name.lower()}", f"Average Total Monthly Precipitation - {month_name}", "precip", "inches", "Average Precipitation Outputs", CMIP_SECTION)
for _season_code, season_name in SEASON_LABELS:
    _register_parameter(f"seasonal_precip_{season_name.lower()}", f"Average Total Seasonal Precipitation - {season_name}", "precip", "inches", "Average Precipitation Outputs", CMIP_SECTION)
_register_parameter("very_heavy_24hr_precip_amount", "Very Heavy 24-hour Precipitation Amount", "precip", "inches", "Heavy Precipitation Outputs", CMIP_SECTION)
_register_parameter("extremely_heavy_24hr_precip_amount", "Extremely Heavy 24-hour Precipitation Amount", "precip", "inches", "Heavy Precipitation Outputs", CMIP_SECTION)
_register_parameter("avg_baseline_very_heavy_precip_events", "Average Number of Baseline Very Heavy Precipitation Events per Year", "precip", "days", "Heavy Precipitation Outputs", CMIP_SECTION)
_register_parameter("avg_baseline_extremely_heavy_precip_events", "Average Number of Baseline Extremely Heavy Precipitation Events per Year", "precip", "days", "Heavy Precipitation Outputs", CMIP_SECTION)
for _season_code, season_name in SEASON_LABELS:
    _register_parameter(f"largest_3day_precip_{season_name.lower()}", f"Largest 3-Day Precipitation Event - {season_name}", "precip", "inches", "Other Precipitation Outputs", CMIP_SECTION)
for aep, label in AEP_LEVELS:
    compact = _aep_key(label)
    display_label = label.split(" ", 1)[0]
    _register_parameter(f"ratio_24hr_precip_quantile_{compact}", f"Ratio of 24-hour Precipitation Quantiles - {display_label}", "precip", "ratio", "Other Precipitation Outputs", CMIP_SECTION, "Requires baseline and future annual maximum precipitation series.")
    _register_parameter(f"precip_24hr_aep_{compact}", f"24-hour Precipitation with {display_label}", "precip", "inches", "Other Precipitation Outputs", CMIP_SECTION, "Requires enough annual maximum precipitation data to fit the Gumbel distribution.")
_register_parameter("pg_high_temp", "PG High Temperature", "pg", "°C", "Pavement PG Parameters", PG_SECTION)
_register_parameter("pg_low_temp", "PG Low Temperature", "pg", "°C", "Pavement PG Parameters", PG_SECTION)
_register_parameter("standard_pg_high_grade", "Standard PG High Grade", "pg", "grade", "Pavement PG Parameters", PG_SECTION)
_register_parameter("standard_pg_low_grade", "Standard PG Low Grade", "pg", "grade", "Pavement PG Parameters", PG_SECTION)
_register_parameter("pg_change_factor_high", "PG Change Factor High", "pg", "°C", "Pavement PG Parameters", PG_SECTION)
_register_parameter("pg_change_factor_low", "PG Change Factor Low", "pg", "°C", "Pavement PG Parameters", PG_SECTION)
_register_parameter("final_projected_pg_high", "Final Projected PG High", "pg", "°C", "Pavement PG Parameters", PG_SECTION)
_register_parameter("final_projected_pg_low", "Final Projected PG Low", "pg", "°C", "Pavement PG Parameters", PG_SECTION)

PARAMETER_REGISTRY = [(meta["key"], meta["display_name"], meta["parameter_type"], meta["unit"], meta["category"]) for meta in PARAMETER_METADATA]
PARAMETER_METADATA_BY_KEY = {meta["key"]: meta for meta in PARAMETER_METADATA}
CMIP_PARAMETER_KEYS = {meta["key"] for meta in PARAMETER_METADATA if meta["section"] == CMIP_SECTION}
PG_PARAMETER_KEYS = {meta["key"] for meta in PARAMETER_METADATA if meta["section"] == PG_SECTION}
TEMPERATURE_DELTA_C_KEYS = {meta["key"] for meta in PARAMETER_METADATA if meta["parameter_type"] == "temp" and meta["unit"] == "°F"}
EXPECTED_CMIP_PARAMETER_NAMES = [meta["display_name"] for meta in PARAMETER_METADATA if meta["section"] == CMIP_SECTION]


# ---------------------------------------------------------------------------
# Main compute engine
# ---------------------------------------------------------------------------


def compute_all_parameters(
    yearly_daily: YearlyDaily,
    latitude_deg: float,
    reliability_z: float,
    heatwave_threshold_f: float = DEFAULT_HEATWAVE_THRESHOLD_F,
    reference_thresholds: Optional[Dict[str, Optional[float]]] = None,
    precip_quantile_ratios: Optional[Dict[str, Optional[float]]] = None,
) -> Dict[str, ScalarSeries]:
    """Compute all summary-facing CMIP and PG parameter series.

    Most outputs are annual scalar series.
    Period-only CMIP metrics are expanded into constant annual series so the
    existing summary and time-series pipelines can continue to operate without
    special-case handling.
    """
    results: Dict[str, ScalarSeries] = {}
    reference_thresholds = reference_thresholds or _build_reference_thresholds(yearly_daily)

    results["mean_annual_temp"] = calc_mean_annual_temperature(yearly_daily)
    results["avg_annual_max_temp"] = calc_average_annual_max_temperature(yearly_daily)
    results["avg_annual_min_temp"] = calc_average_annual_min_temperature(yearly_daily)
    results["annual_extreme_heat"] = calc_annual_extreme_heat(yearly_daily)
    results["extreme_cold"] = calc_extreme_cold(yearly_daily)
    results["mean_annual_precip"] = calc_mean_annual_precipitation(yearly_daily)
    results["pg_high_temp"] = calc_pg_high_temperature(yearly_daily, latitude_deg, reliability_z)
    results["pg_low_temp"] = calc_pg_low_temperature(yearly_daily, latitude_deg, reliability_z)
    results["standard_pg_high_grade"] = calc_standard_pg_high_grade(yearly_daily, latitude_deg, reliability_z)
    results["standard_pg_low_grade"] = calc_standard_pg_low_grade(yearly_daily, latitude_deg, reliability_z)

    very_hot = _period_tmax_percentile(yearly_daily, 0.95)
    extremely_hot = _period_tmax_percentile(yearly_daily, 0.99)
    very_cold = _period_tmin_percentile(yearly_daily, 0.05)
    extremely_cold = _period_tmin_percentile(yearly_daily, 0.01)
    very_heavy_precip = _period_nonzero_precip_percentile(yearly_daily, 0.95)
    extremely_heavy_precip = _period_nonzero_precip_percentile(yearly_daily, 0.99)
    aep_quantiles = _compute_aep_quantiles(yearly_daily)

    # CMIP percentile thresholds are period metrics, so they are repeated across
    # years to preserve the existing annual-series export contract.
    results["very_hot_day_temp"] = _constant_series(yearly_daily, very_hot)
    results["extremely_hot_day_temp"] = _constant_series(yearly_daily, extremely_hot)
    results["very_cold_day_temp"] = _constant_series(yearly_daily, very_cold)
    results["extremely_cold_day_temp"] = _constant_series(yearly_daily, extremely_cold)
    results["very_heavy_24hr_precip_amount"] = _constant_series(yearly_daily, very_heavy_precip)
    results["extremely_heavy_24hr_precip_amount"] = _constant_series(yearly_daily, extremely_heavy_precip)

    baseline_very_hot = reference_thresholds.get("baseline_very_hot_threshold")
    baseline_extremely_hot = reference_thresholds.get("baseline_extremely_hot_threshold")
    baseline_very_heavy = reference_thresholds.get("baseline_very_heavy_precip_threshold")
    baseline_extremely_heavy = reference_thresholds.get("baseline_extremely_heavy_precip_threshold")

    results["avg_days_above_baseline_very_hot"] = {
        yr: _count_threshold(days, baseline_very_hot, lambda record: record[1]) if baseline_very_hot is not None else None
        for yr, days in yearly_daily.items()
    }
    results["avg_days_above_baseline_extremely_hot"] = {
        yr: _count_threshold(days, baseline_extremely_hot, lambda record: record[1]) if baseline_extremely_hot is not None else None
        for yr, days in yearly_daily.items()
    }
    for threshold in (95, 100, 105, 110):
        results[f"avg_days_above_{threshold}"] = {
            yr: _count_threshold(days, float(threshold), lambda record: record[1])
            for yr, days in yearly_daily.items()
        }
    results["max_consecutive_days_above_baseline_very_hot"] = {
        yr: _longest_run_threshold(days, baseline_very_hot, lambda record: record[1]) if baseline_very_hot is not None else None
        for yr, days in yearly_daily.items()
    }
    results["max_consecutive_days_above_baseline_extremely_hot"] = {
        yr: _longest_run_threshold(days, baseline_extremely_hot, lambda record: record[1]) if baseline_extremely_hot is not None else None
        for yr, days in yearly_daily.items()
    }
    for threshold in (95, 100, 105, 110):
        results[f"max_consecutive_days_above_{threshold}"] = {
            yr: _longest_run_threshold(days, float(threshold), lambda record: record[1])
            for yr, days in yearly_daily.items()
        }

    results["average_summer_temperature"] = _summer_average_temperature(yearly_daily)
    results["highest_4day_average_summer_high_temperature"] = _summer_running_high_average(yearly_daily, 4)
    results["highest_7day_average_summer_high_temperature"] = _summer_running_high_average(yearly_daily, 7)
    for season_code, season_name in SEASON_LABELS:
        for threshold in (95, 100, 105, 110):
            results[f"days_{season_name.lower()}_above_{threshold}"] = _seasonal_days_above_temperature_threshold(
                yearly_daily,
                season_code,
                float(threshold),
            )

    results["avg_days_below_freezing"] = calc_freezing_days(yearly_daily)
    results["avg_freeze_thaw_fluctuations"] = {
        yr: _freeze_thaw_fluctuations(days) for yr, days in yearly_daily.items()
    }
    results["average_winter_temperature"] = _winter_average_temperature(yearly_daily)
    results["lowest_4day_average_winter_low_temperature"] = _winter_running_low_average(yearly_daily, 4)
    results["lowest_7day_average_winter_low_temperature"] = _winter_running_low_average(yearly_daily, 7)

    for month_num, month_name in MONTH_LABELS:
        results[f"monthly_precip_{month_name.lower()}"] = _monthly_total_precipitation(yearly_daily, month_num)
    for season_code, season_name in SEASON_LABELS:
        results[f"seasonal_precip_{season_name.lower()}"] = _seasonal_total_precipitation(yearly_daily, season_code)
        results[f"largest_3day_precip_{season_name.lower()}"] = _largest_3day_precipitation_for_season(yearly_daily, season_code)

    results["avg_baseline_very_heavy_precip_events"] = {
        yr: _count_threshold(days, baseline_very_heavy, lambda record: record[3]) if baseline_very_heavy is not None else None
        for yr, days in yearly_daily.items()
    }
    results["avg_baseline_extremely_heavy_precip_events"] = {
        yr: _count_threshold(days, baseline_extremely_heavy, lambda record: record[3]) if baseline_extremely_heavy is not None else None
        for yr, days in yearly_daily.items()
    }

    # The FHWA/CMIP workbook exposes AEP depths and future/baseline ratios as
    # period summaries, so they are also expanded into constant annual series.
    for compact_key, quantile in aep_quantiles.items():
        results[f"precip_24hr_aep_{compact_key}"] = _constant_series(yearly_daily, quantile)

    if precip_quantile_ratios is None:
        precip_quantile_ratios = {key: 1.0 if value is not None else None for key, value in aep_quantiles.items()}
    for compact_key, ratio in precip_quantile_ratios.items():
        results[f"ratio_24hr_precip_quantile_{compact_key}"] = _constant_series(yearly_daily, ratio)

    return results


def _summarize_timeseries(ts_dict: ScalarSeries) -> Optional[float]:
    return safe_mean(ts_dict.values())


def _summarize_nested_timeseries(ts_dict: SeasonalSeries) -> Dict[str, float]:
    nested_buckets: Dict[str, List[float]] = {}
    for _, entries in ts_dict.items():
        for entry_name, value in entries.items():
            if value is not None:
                nested_buckets.setdefault(entry_name, []).append(value)
    return {name: mean(vals) for name, vals in nested_buckets.items() if vals}


def _period_pg_summary(
    yearly_daily: YearlyDaily,
    latitude_deg: float,
    reliability_z: float,
) -> Dict[str, Optional[float]]:
    pg_high_temp = summarize_pg_high_period(yearly_daily, latitude_deg, reliability_z)
    pg_low_temp = summarize_pg_low_period(yearly_daily, latitude_deg, reliability_z)
    return {
        "pg_high_temp": pg_high_temp,
        "pg_low_temp": pg_low_temp,
        "standard_pg_high_grade": standard_pg_high_grade(pg_high_temp),
        "standard_pg_low_grade": standard_pg_low_grade(pg_low_temp),
    }


def _grid_station_count(station_grid: str) -> int:
    return {"1x1": 1, "2x2": 4, "3x3": 9}.get(station_grid, 1)


def _selected_grid_stations(city_info, station_grid: str) -> List[Dict[str, float]]:
    grid_stations = list(city_info.get("grid_stations") or [])
    if not grid_stations:
        station = str(city_info.get("station", "")).strip()
        station_lat = city_info.get("station_lat")
        station_lon = city_info.get("station_lon")
        if station and station_lat is not None and station_lon is not None:
            grid_stations = [
                {
                    "station": station,
                    "station_lat": float(station_lat),
                    "station_lon": float(station_lon),
                    "distance_mi": 0.0,
                }
            ]

    if not grid_stations:
        raise ValueError("No station grid data found for selected city.")

    sorted_stations = sorted(grid_stations, key=lambda item: item.get("distance_mi", float("inf")))
    return sorted_stations[: _grid_station_count(station_grid)]


def _station_weight(distance_mi: float) -> float:
    effective_distance = max(float(distance_mi), 1e-6)
    return 1.0 / (effective_distance ** 2)


def _weighted_scalar(values_and_weights: Iterable[Tuple[Optional[float], float]]) -> Optional[float]:
    numerator = 0.0
    denominator = 0.0
    for value, weight in values_and_weights:
        if value is None:
            continue
        numerator += value * weight
        denominator += weight
    if denominator <= EPS:
        return None
    return numerator / denominator


def _average_optional_scalars(values: Iterable[Optional[float]]) -> Optional[float]:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return mean(filtered)


def _average_scalar_series(series_list: Iterable[ScalarSeries]) -> ScalarSeries:
    normalized = [series or {} for series in series_list]
    years = sorted({year for series in normalized for year in series})
    return {
        year: _average_optional_scalars(series.get(year) for series in normalized)
        for year in years
    }


def _average_seasonal_series(series_list: Iterable[SeasonalSeries]) -> SeasonalSeries:
    normalized = [series or {} for series in series_list]
    years = sorted({year for series in normalized for year in series})
    result: SeasonalSeries = {}
    for year in years:
        season_names = sorted(
            {
                season
                for series in normalized
                for season in (series.get(year, {}) or {})
            }
        )
        result[year] = {
            season: _average_optional_scalars(
                (series.get(year, {}) or {}).get(season) for series in normalized
            )
            for season in season_names
        }
    return result


def _average_parameter_payloads(payloads: Iterable[Dict[str, object]]) -> Dict[str, object]:
    payload_list = [payload or {} for payload in payloads]
    if not payload_list:
        return {}

    result: Dict[str, object] = {}
    keys = sorted({key for payload in payload_list for key in payload})
    for key in keys:
        series_values = [payload.get(key) for payload in payload_list if isinstance(payload.get(key), dict)]
        if not series_values:
            continue

        sample_value = None
        for candidate in series_values:
            if candidate:
                sample_value = next(iter(candidate.values()), None)
                if sample_value is not None:
                    break

        if isinstance(sample_value, dict):
            result[key] = _average_seasonal_series(series_values)  # type: ignore[arg-type]
        else:
            result[key] = _average_scalar_series(series_values)  # type: ignore[arg-type]

    return result


def _average_pg_summaries(summaries: Iterable[Dict[str, Optional[float]]]) -> Dict[str, Optional[float]]:
    summary_list = [summary or {} for summary in summaries]
    keys = sorted({key for summary in summary_list for key in summary})
    return {
        key: _average_optional_scalars(summary.get(key) for summary in summary_list)
        for key in keys
    }


def _weighted_dict(values_and_weights: Iterable[Tuple[Dict[str, Optional[float]], float]]) -> Dict[str, Optional[float]]:
    all_keys = set()
    materialized = []
    for value, weight in values_and_weights:
        value = value or {}
        materialized.append((value, weight))
        all_keys.update(value.keys())

    return {
        key: _weighted_scalar((value.get(key), weight) for value, weight in materialized)
        for key in sorted(all_keys)
    }


def _weighted_series(values_and_weights: Iterable[Tuple[ScalarSeries, float]]) -> ScalarSeries:
    all_years = set()
    materialized = []
    for series, weight in values_and_weights:
        materialized.append((series or {}, weight))
        all_years.update((series or {}).keys())

    return {
        year: _weighted_scalar((series.get(year), weight) for series, weight in materialized)
        for year in sorted(all_years)
    }


def _weighted_seasonal_series(values_and_weights: Iterable[Tuple[SeasonalSeries, float]]) -> SeasonalSeries:
    all_years = set()
    materialized = []
    for series, weight in values_and_weights:
        materialized.append((series or {}, weight))
        all_years.update((series or {}).keys())

    result: SeasonalSeries = {}
    for year in sorted(all_years):
        entries = []
        for series, weight in materialized:
            entries.append((series.get(year, {}), weight))
        result[year] = _weighted_dict(entries)
    return result


def _weighted_value(values_and_weights: Iterable[Tuple[object, float]]):
    materialized = list(values_and_weights)
    first_value = next((value for value, _weight in materialized if value is not None), None)
    if first_value is None:
        return None
    if isinstance(first_value, dict):
        return _weighted_dict((value or {}, weight) for value, weight in materialized)
    return _weighted_scalar((value, weight) for value, weight in materialized)


def _difference_value(left, right):
    if left is None or right is None:
        return None
    if isinstance(left, dict) and isinstance(right, dict):
        keys = sorted(set(left) | set(right))
        return {
            key: None if left.get(key) is None or right.get(key) is None else left.get(key) - right.get(key)
            for key in keys
        }
    return left - right


def _normalize_temperature_delta_c(param_key: str, diff_value: Optional[float]) -> Optional[float]:
    if diff_value is None or param_key not in TEMPERATURE_DELTA_C_KEYS:
        return None
    return diff_value * 5.0 / 9.0


def _missing_reason(param_key: str, value: Optional[float]) -> str:
    if value is not None:
        return ""
    meta = PARAMETER_METADATA_BY_KEY.get(param_key, {})
    explicit_reason = meta.get("reason", "")
    if explicit_reason:
        return str(explicit_reason)
    return "Required input data were not available for the selected period."


def validate_parameter_names(summary_results: Dict[str, Dict[str, object]]) -> Dict[str, List[str]]:
    actual_names = [
        summary_results[key]["display_name"]
        for key in summary_results
        if key in CMIP_PARAMETER_KEYS and isinstance(summary_results.get(key), dict)
    ]
    expected_set = set(EXPECTED_CMIP_PARAMETER_NAMES)
    actual_set = set(actual_names)
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    print("CMIP validation - missing parameters:", missing)
    print("CMIP validation - extra parameters:", extra)
    return {"missing": missing, "extra": extra}


def _safe_component(value: str) -> str:
    return str(value).replace("/", "_").replace("\\", "_").replace(" ", "_")


# ---------------------------------------------------------------------------
# Main application entry point
# ---------------------------------------------------------------------------


def _run_index_calculation_single_station_legacy(
    city,
    city_info,
    scenario_root,
    hcd_input_dir,
    scenarios,
    models,
    baseline_start,
    baseline_end,
    future_start,
    future_end,
    selected_params,
    ts_flags,
    summary_flags,
    output_root,
    progress_callback=None,
    reliability_z: float = PG_RELIABILITY_MAP["98%"],
    heatwave_threshold_f: float = DEFAULT_HEATWAVE_THRESHOLD_F,
):
    return run_index_calculation(
        city=city,
        city_info=city_info,
        scenario_root=scenario_root,
        hcd_input_dir=hcd_input_dir,
        scenarios=scenarios,
        models=models,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        future_start=future_start,
        future_end=future_end,
        selected_params=selected_params,
        ts_flags=ts_flags,
        summary_flags=summary_flags,
        output_root=output_root,
        progress_callback=progress_callback,
        reliability_z=reliability_z,
        heatwave_threshold_f=heatwave_threshold_f,
        station_grid="1x1",
    )

def run_index_calculation(
    city,
    city_info,
    scenario_root,
    hcd_input_dir,
    scenarios,
    models,
    baseline_start,
    baseline_end,
    future_start,
    future_end,
    selected_params,
    ts_flags,
    summary_flags,
    output_root,
    progress_callback=None,
    reliability_z: float = PG_RELIABILITY_MAP["98%"],
    heatwave_threshold_f: float = DEFAULT_HEATWAVE_THRESHOLD_F,
    station_grid: str = "1x1",
    cancel_event=None,
):
    if not city_info:
        raise ValueError(f"No info for city: {city}")

    city_lat = float(city_info.get("lat", 0.0))
    city_lon = float(city_info.get("lon", 0.0))
    selected_stations = _selected_grid_stations(city_info, station_grid)

    total_steps = len(selected_stations) * (
        len(models) + len(scenarios) * len(models) * 2
    )
    current_step = [0]

    def _tick(msg=""):
        current_step[0] += 1
        if progress_callback:
            progress_callback(current_step[0], total_steps, msg)

    station_results = []

    for station_index, station_info in enumerate(selected_stations, start=1):
        check_cancelled(cancel_event)
        station = str(station_info.get("station", "")).strip()
        lat = float(station_info.get("station_lat", city_lat))
        lon = float(station_info.get("station_lon", city_lon))
        distance_mi = float(station_info.get("distance_mi", 0.0))
        weight = _station_weight(distance_mi)

        if not station:
            raise ValueError(f"No station value found for city: {city}")

        baseline_by_model = {}
        for model in models:
            check_cancelled(cancel_event)
            baseline_model_daily = load_model_daily(
                scenario_root=scenario_root,
                scenario=HISTORICAL_SCENARIO,
                model=model,
                year_start=baseline_start,
                year_end=baseline_end,
                lon=lon,
                lat=lat,
                cancel_event=cancel_event,
            )
            if not baseline_model_daily:
                raise ValueError(
                    f"No historical model data found for {model} at station {station} in years {baseline_start}-{baseline_end}."
                )
            baseline_reference_thresholds = _build_reference_thresholds(baseline_model_daily)
            baseline_aep_quantiles = _compute_aep_quantiles(baseline_model_daily)
            baseline_by_model[model] = {
                "daily": baseline_model_daily,
                "params": compute_all_parameters(
                    baseline_model_daily,
                    latitude_deg=lat,
                    reliability_z=reliability_z,
                    heatwave_threshold_f=heatwave_threshold_f,
                    reference_thresholds=baseline_reference_thresholds,
                    precip_quantile_ratios={key: 1.0 if value is not None else None for key, value in baseline_aep_quantiles.items()},
                ),
                "pg_summary": _period_pg_summary(baseline_model_daily, lat, reliability_z),
                "reference_thresholds": baseline_reference_thresholds,
                "aep_quantiles": baseline_aep_quantiles,
            }
            _tick(f"Loaded historical baseline: {model} ({station_index}/{len(selected_stations)}: {station})")

        historic_params = _average_parameter_payloads(
            data["params"] for data in baseline_by_model.values()
        )
        historic_pg_summary = _average_pg_summaries(
            data["pg_summary"] for data in baseline_by_model.values()
        )

        model_results = {}
        for scenario in scenarios:
            for model in models:
                check_cancelled(cancel_event)
                label = f"{scenario}/{model}"
                baseline_model_params = baseline_by_model[model]["params"]
                baseline_model_pg_summary = baseline_by_model[model]["pg_summary"]
                _tick(f"Prepared baseline model: {label} ({station})")

                future_model_daily = load_model_daily(
                    scenario_root=scenario_root,
                    scenario=scenario,
                    model=model,
                    year_start=future_start,
                    year_end=future_end,
                    lon=lon,
                    lat=lat,
                    cancel_event=cancel_event,
                )
                if not future_model_daily:
                    raise ValueError(
                        f"No future model data found for {scenario}/{model} at station {station} in years {future_start}-{future_end}."
                    )
                future_aep_quantiles = _compute_aep_quantiles(future_model_daily)
                precip_quantile_ratios = _compute_quantile_ratio_dict(
                    baseline_by_model[model]["aep_quantiles"],
                    future_aep_quantiles,
                )

                future_model_params = compute_all_parameters(
                    future_model_daily,
                    latitude_deg=lat,
                    reliability_z=reliability_z,
                    heatwave_threshold_f=heatwave_threshold_f,
                    reference_thresholds=baseline_by_model[model]["reference_thresholds"],
                    precip_quantile_ratios=precip_quantile_ratios,
                )
                future_model_pg_summary = _period_pg_summary(future_model_daily, lat, reliability_z)
                _tick(f"Loaded future model: {label} ({station})")

                cf_high = calc_pg_change_factor(
                    baseline_model_pg_summary["pg_high_temp"],
                    future_model_pg_summary["pg_high_temp"],
                )
                cf_low = calc_pg_change_factor(
                    baseline_model_pg_summary["pg_low_temp"],
                    future_model_pg_summary["pg_low_temp"],
                )

                model_results[label] = {
                    "baseline_model": baseline_model_params,
                    "future_model": future_model_params,
                    "baseline_model_pg_summary": baseline_model_pg_summary,
                    "future_model_pg_summary": future_model_pg_summary,
                    "future_aep_quantiles": future_aep_quantiles,
                    "pg_change_factor_high": cf_high,
                    "pg_change_factor_low": cf_low,
                    "final_projected_pg_high": calc_final_projected_pg(historic_pg_summary["pg_high_temp"], cf_high),
                    "final_projected_pg_low": calc_final_projected_pg(historic_pg_summary["pg_low_temp"], cf_low),
                }

        station_results.append(
            {
                "station": station,
                "station_lat": lat,
                "station_lon": lon,
                "distance_mi": distance_mi,
                "weight": weight,
                "historic_params": historic_params,
                "historic_pg_summary": historic_pg_summary,
                "model_results": model_results,
            }
        )

    summary_results = {}
    ts_files: List[str] = []

    for param_key, display_name, ptype, unit, category in PARAMETER_REGISTRY:
        check_cancelled(cancel_event)
        if param_key not in selected_params:
            continue

        if param_key in ("pg_high_temp", "pg_low_temp", "standard_pg_high_grade", "standard_pg_low_grade"):
            hist_summary = _weighted_scalar(
                (station_data["historic_pg_summary"][param_key], station_data["weight"])
                for station_data in station_results
            )
        elif param_key in (
            "pg_change_factor_high",
            "pg_change_factor_low",
            "final_projected_pg_high",
            "final_projected_pg_low",
        ):
            hist_key = "pg_high_temp" if "high" in param_key else "pg_low_temp"
            hist_summary = _weighted_scalar(
                (station_data["historic_pg_summary"][hist_key], station_data["weight"])
                for station_data in station_results
            )
        else:
            hist_summary = _weighted_scalar(
                (_summarize_timeseries(station_data["historic_params"].get(param_key, {})), station_data["weight"])
                for station_data in station_results
            )

        model_summaries = {}
        for scenario in scenarios:
            for model in models:
                label = f"{scenario}/{model}"
                if param_key in ("pg_high_temp", "pg_low_temp", "standard_pg_high_grade", "standard_pg_low_grade"):
                    historical_model_value = _weighted_scalar(
                        (station_data["model_results"][label]["baseline_model_pg_summary"][param_key], station_data["weight"])
                        for station_data in station_results
                    )
                elif param_key in ("pg_change_factor_high", "final_projected_pg_high"):
                    historical_model_value = _weighted_scalar(
                        (station_data["model_results"][label]["baseline_model_pg_summary"]["pg_high_temp"], station_data["weight"])
                        for station_data in station_results
                    )
                elif param_key in ("pg_change_factor_low", "final_projected_pg_low"):
                    historical_model_value = _weighted_scalar(
                        (station_data["model_results"][label]["baseline_model_pg_summary"]["pg_low_temp"], station_data["weight"])
                        for station_data in station_results
                    )
                else:
                    historical_model_value = _weighted_scalar(
                        (_summarize_timeseries(station_data["model_results"][label]["baseline_model"].get(param_key, {})), station_data["weight"])
                        for station_data in station_results
                    )

                if param_key in ("pg_high_temp", "pg_low_temp", "standard_pg_high_grade", "standard_pg_low_grade"):
                    aggregate_value = _weighted_scalar(
                        (station_data["model_results"][label]["future_model_pg_summary"][param_key], station_data["weight"])
                        for station_data in station_results
                    )
                elif param_key == "pg_change_factor_high":
                    aggregate_value = _weighted_scalar(
                        (station_data["model_results"][label]["pg_change_factor_high"], station_data["weight"])
                        for station_data in station_results
                    )
                elif param_key == "pg_change_factor_low":
                    aggregate_value = _weighted_scalar(
                        (station_data["model_results"][label]["pg_change_factor_low"], station_data["weight"])
                        for station_data in station_results
                    )
                elif param_key == "final_projected_pg_high":
                    aggregate_value = _weighted_scalar(
                        (station_data["model_results"][label]["final_projected_pg_high"], station_data["weight"])
                        for station_data in station_results
                    )
                elif param_key == "final_projected_pg_low":
                    aggregate_value = _weighted_scalar(
                        (station_data["model_results"][label]["final_projected_pg_low"], station_data["weight"])
                        for station_data in station_results
                    )
                else:
                    aggregate_value = _weighted_scalar(
                        (_summarize_timeseries(station_data["model_results"][label]["future_model"].get(param_key, {})), station_data["weight"])
                        for station_data in station_results
                    )

                diff = _difference_value(aggregate_value, historical_model_value)
                model_summaries[label] = {
                    "historical_model": historical_model_value,
                    "future_model": aggregate_value,
                    "difference": diff,
                    "normalized_delta_c": _normalize_temperature_delta_c(param_key, diff),
                    "future_reason": _missing_reason(param_key, aggregate_value),
                    "difference_reason": _missing_reason(param_key, diff),
                    "pct_change": pct_change(aggregate_value, historical_model_value),
                }

        station_summaries = []
        for station_data in station_results:
            if param_key in ("pg_high_temp", "pg_low_temp", "standard_pg_high_grade", "standard_pg_low_grade"):
                station_hist = station_data["historic_pg_summary"][param_key]
            elif param_key in (
                "pg_change_factor_high",
                "pg_change_factor_low",
                "final_projected_pg_high",
                "final_projected_pg_low",
            ):
                hist_key = "pg_high_temp" if "high" in param_key else "pg_low_temp"
                station_hist = station_data["historic_pg_summary"][hist_key]
            else:
                station_hist = _summarize_timeseries(station_data["historic_params"].get(param_key, {}))

            station_models = {}
            scalar_values = []
            for scenario in scenarios:
                for model in models:
                    label = f"{scenario}/{model}"
                    if param_key in ("pg_high_temp", "pg_low_temp", "standard_pg_high_grade", "standard_pg_low_grade"):
                        station_historical_value = station_data["model_results"][label]["baseline_model_pg_summary"][param_key]
                    elif param_key in ("pg_change_factor_high", "final_projected_pg_high"):
                        station_historical_value = station_data["model_results"][label]["baseline_model_pg_summary"]["pg_high_temp"]
                    elif param_key in ("pg_change_factor_low", "final_projected_pg_low"):
                        station_historical_value = station_data["model_results"][label]["baseline_model_pg_summary"]["pg_low_temp"]
                    else:
                        station_historical_value = _summarize_timeseries(
                            station_data["model_results"][label]["baseline_model"].get(param_key, {})
                        )

                    if param_key in ("pg_high_temp", "pg_low_temp", "standard_pg_high_grade", "standard_pg_low_grade"):
                        station_value = station_data["model_results"][label]["future_model_pg_summary"][param_key]
                    elif param_key == "pg_change_factor_high":
                        station_value = station_data["model_results"][label]["pg_change_factor_high"]
                    elif param_key == "pg_change_factor_low":
                        station_value = station_data["model_results"][label]["pg_change_factor_low"]
                    elif param_key == "final_projected_pg_high":
                        station_value = station_data["model_results"][label]["final_projected_pg_high"]
                    elif param_key == "final_projected_pg_low":
                        station_value = station_data["model_results"][label]["final_projected_pg_low"]
                    else:
                        station_value = _summarize_timeseries(
                            station_data["model_results"][label]["future_model"].get(param_key, {})
                        )
                    station_models[label] = {
                        "historical": station_historical_value,
                        "future": station_value,
                    }
                    if isinstance(station_value, (int, float)):
                        scalar_values.append(float(station_value))

            station_summaries.append(
                {
                    "station": station_data["station"],
                    "lat": station_data["station_lat"],
                    "lon": station_data["station_lon"],
                    "distance_mi": station_data["distance_mi"],
                    "historic": station_hist,
                    "ensemble_average": safe_mean(scalar_values),
                    "models": station_models,
                }
            )

        summary_results[param_key] = {
            "display_name": display_name,
            "unit": unit,
            "category": category,
            "section": PARAMETER_METADATA_BY_KEY.get(param_key, {}).get("section", CMIP_SECTION),
            "historic": hist_summary,
            "historic_reason": _missing_reason(param_key, hist_summary),
            "models": model_summaries,
            "station_summaries": station_summaries,
            "stations": [
                {
                    "station": station_data["station"],
                    "lat": station_data["station_lat"],
                    "lon": station_data["station_lon"],
                    "distance_mi": station_data["distance_mi"],
                }
                for station_data in station_results
            ],
        }

    summary_results["__validation__"] = validate_parameter_names(summary_results)

    output_dir = Path(output_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    city_safe = _safe_component(city)

    for scenario in scenarios:
        check_cancelled(cancel_event)
        scenario_dir = output_dir / _safe_component(scenario)
        scenario_dir.mkdir(parents=True, exist_ok=True)

        for param_key, display_name, ptype, unit, category in PARAMETER_REGISTRY:
            check_cancelled(cancel_event)
            if param_key not in selected_params or not ts_flags.get(param_key, False):
                continue
            if param_key in (
                "pg_change_factor_high",
                "pg_change_factor_low",
                "final_projected_pg_high",
                "final_projected_pg_low",
            ):
                continue

            param_dir = scenario_dir / _safe_component(display_name)
            param_dir.mkdir(parents=True, exist_ok=True)

            station_exports = []

            for station_data in station_results:
                check_cancelled(cancel_event)
                station_id = station_data["station"]
                station_lat = station_data["station_lat"]
                station_lon = station_data["station_lon"]
                historic_ts = station_data["historic_params"].get(param_key, {})

                model_payloads = {}
                for model in models:
                    label = f"{scenario}/{model}"
                    model_payloads[model] = {
                        "baseline": station_data["model_results"][label]["baseline_model"].get(param_key, {}),
                        "future": station_data["model_results"][label]["future_model"].get(param_key, {}),
                    }

                csv_path = param_dir / f"{city_safe}_{_safe_component(station_id)}_{station_grid}.csv"
                metadata = [
                    "Scenario", scenario,
                    "Location", city,
                    "Station ID", station_id,
                    "Input Longitude", city_lon,
                    "Input Latitude", city_lat,
                    "Station Longitude", station_lon,
                    "Station Latitude", station_lat,
                    "Distance (mi)", f"{station_data['distance_mi']:.3f}",
                    "Grid", station_grid,
                ]

                _write_station_comparison_csv(
                    csv_path,
                    metadata,
                    historic_ts,
                    model_payloads,
                    baseline_start,
                    baseline_end,
                    future_start,
                    future_end,
                    unit,
                )
                ts_files.append(str(csv_path))
                station_exports.append(
                    {
                        "station": station_id,
                        "station_lat": station_lat,
                        "station_lon": station_lon,
                        "distance_mi": station_data["distance_mi"],
                        "historic_ts": historic_ts,
                        "model_payloads": model_payloads,
                    }
                )

            combined_csv_path = param_dir / f"{city_safe}_{station_grid}_all_stations.csv"
            _write_multi_station_comparison_csv(
                combined_csv_path,
                [
                    "Scenario", scenario,
                    "Location", city,
                    "Input Longitude", city_lon,
                    "Input Latitude", city_lat,
                    "Grid", station_grid,
                ],
                station_exports,
                bl_start=baseline_start,
                bl_end=baseline_end,
                fut_start=future_start,
                fut_end=future_end,
                unit=unit,
            )
            ts_files.append(str(combined_csv_path))

    return summary_results, ts_files

# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------


def _write_timeseries_csv(
    path: Union[str, Path],
    historic_ts: ScalarSeries,
    baseline_model_ts: ScalarSeries,
    future_model_ts: ScalarSeries,
    bl_start: int,
    bl_end: int,
    fut_start: int,
    fut_end: int,
    unit: str,
) -> None:
    safe_unit = _csv_safe_unit(unit)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Year", f"Historical Baseline ({safe_unit})", f"Historical Model ({safe_unit})", f"Future Model ({safe_unit})"])

        for yr in range(bl_start, bl_end + 1):
            h = historic_ts.get(yr)
            hm = baseline_model_ts.get(yr)
            writer.writerow([
                yr,
                f"{h:.4f}" if isinstance(h, (int, float)) else "",
                f"{hm:.4f}" if isinstance(hm, (int, float)) else "",
                "",
            ])

        for yr in range(fut_start, fut_end + 1):
            fm = future_model_ts.get(yr)
            writer.writerow([
                yr,
                "",
                "",
                f"{fm:.4f}" if isinstance(fm, (int, float)) else "",
            ])


def _write_station_comparison_csv(
    path: Union[str, Path],
    metadata_row: List[object],
    historic_ts: ScalarSeries,
    model_payloads: Dict[str, Dict[str, ScalarSeries]],
    bl_start: int,
    bl_end: int,
    fut_start: int,
    fut_end: int,
    unit: str,
) -> None:
    models = list(model_payloads.keys())
    safe_unit = _csv_safe_unit(unit)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(metadata_row)
        writer.writerow(["Year", f"Historical Baseline ({safe_unit})"] + [f"{model} ({safe_unit})" for model in models])

        for yr in range(bl_start, bl_end + 1):
            row = [yr, f"{historic_ts.get(yr):.4f}" if isinstance(historic_ts.get(yr), (int, float)) else ""]
            for model in models:
                value = model_payloads[model]["baseline"].get(yr)
                row.append(f"{value:.4f}" if isinstance(value, (int, float)) else "")
            writer.writerow(row)

        for yr in range(fut_start, fut_end + 1):
            row = [yr, ""]
            for model in models:
                value = model_payloads[model]["future"].get(yr)
                row.append(f"{value:.4f}" if isinstance(value, (int, float)) else "")
            writer.writerow(row)


def _write_multi_station_comparison_csv(
    path: Union[str, Path],
    metadata_row: List[object],
    station_exports: List[Dict[str, Any]],
    bl_start: int,
    bl_end: int,
    fut_start: int,
    fut_end: int,
    unit: str,
) -> None:
    if not station_exports:
        return

    models = list(station_exports[0]["model_payloads"].keys())
    safe_unit = _csv_safe_unit(unit)
    block_header = [f"Historical Baseline ({safe_unit})"] + [f"{model} ({safe_unit})" for model in models] + [f"Ensemble ({safe_unit})"]

    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(metadata_row)

        station_header = ["Year"]
        for station_index, station_export in enumerate(station_exports, start=1):
            station_header.extend(
                [
                    f"Station - {station_index} ID: {station_export['station']} | "
                    f"Lat/Lon: {station_export['station_lat']:.5f}, {station_export['station_lon']:.5f} | "
                    f"Distance (mi): {station_export['distance_mi']:.3f}"
                ]
                + [""] * (len(block_header) - 1)
            )
            if station_index < len(station_exports):
                station_header.append("")
        writer.writerow(station_header)

        header = ["Year"]
        for station_index, _station_export in enumerate(station_exports, start=1):
            header.extend(block_header)
            if station_index < len(station_exports):
                header.append("")
        writer.writerow(header)

        for yr in range(bl_start, bl_end + 1):
            row = [yr]
            for station_index, station_export in enumerate(station_exports, start=1):
                historic_value = station_export["historic_ts"].get(yr)
                row.append(f"{historic_value:.4f}" if isinstance(historic_value, (int, float)) else "")
                baseline_values = []
                for model in models:
                    value = station_export["model_payloads"][model]["baseline"].get(yr)
                    row.append(f"{value:.4f}" if isinstance(value, (int, float)) else "")
                    if isinstance(value, (int, float)):
                        baseline_values.append(float(value))
                row.append(f"{safe_mean(baseline_values):.4f}" if baseline_values else "")
                if station_index < len(station_exports):
                    row.append("")
            writer.writerow(row)

        for yr in range(fut_start, fut_end + 1):
            row = [yr]
            for station_index, station_export in enumerate(station_exports, start=1):
                row.append("")
                future_values = []
                for model in models:
                    value = station_export["model_payloads"][model]["future"].get(yr)
                    row.append(f"{value:.4f}" if isinstance(value, (int, float)) else "")
                    if isinstance(value, (int, float)):
                        future_values.append(float(value))
                row.append(f"{safe_mean(future_values):.4f}" if future_values else "")
                if station_index < len(station_exports):
                    row.append("")
            writer.writerow(row)


def _write_station_comparison_nested_csv(
    path: Union[str, Path],
    metadata_row: List[object],
    historic_ts: SeasonalSeries,
    model_payloads: Dict[str, Dict[str, SeasonalSeries]],
    bl_start: int,
    bl_end: int,
    fut_start: int,
    fut_end: int,
) -> None:
    models = list(model_payloads.keys())
    nested_keys = sorted(
        {
            key
            for year_map in historic_ts.values()
            for key in (year_map or {})
        }
        | {
            key
            for payload in model_payloads.values()
            for period_key in ("baseline", "future")
            for year_map in payload.get(period_key, {}).values()
            for key in (year_map or {})
        }
    )

    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(metadata_row)
        header = ["Year"]
        header.extend([f"Historical Baseline {nested_key}" for nested_key in nested_keys])
        for model in models:
            header.extend([f"{model} {nested_key}" for nested_key in nested_keys])
        writer.writerow(header)

        for yr in range(bl_start, bl_end + 1):
            row = [yr]
            hist_year = historic_ts.get(yr, {})
            row.extend(
                f"{hist_year.get(nested_key):.4f}" if isinstance(hist_year.get(nested_key), (int, float)) else ""
                for nested_key in nested_keys
            )
            for model in models:
                year_data = model_payloads[model]["baseline"].get(yr, {})
                row.extend(
                    f"{year_data.get(nested_key):.4f}" if isinstance(year_data.get(nested_key), (int, float)) else ""
                    for nested_key in nested_keys
                )
            writer.writerow(row)

        for yr in range(fut_start, fut_end + 1):
            row = [yr]
            row.extend("" for _ in nested_keys)
            for model in models:
                year_data = model_payloads[model]["future"].get(yr, {})
                row.extend(
                    f"{year_data.get(nested_key):.4f}" if isinstance(year_data.get(nested_key), (int, float)) else ""
                    for nested_key in nested_keys
                )
            writer.writerow(row)


def _write_seasonal_csv(
    path: Union[str, Path],
    historic_ts: SeasonalSeries,
    baseline_model_ts: SeasonalSeries,
    future_model_ts: SeasonalSeries,
    bl_start: int,
    bl_end: int,
    fut_start: int,
    fut_end: int,
) -> None:
    seasons = list(SEASON_MAP.keys())
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header = ["Year"]
        for s in seasons:
            header.extend([f"Historic {s} (°F)", f"Hist Model {s} (°F)", f"Future Model {s} (°F)"])
        writer.writerow(header)

        for yr in range(bl_start, bl_end + 1):
            row = [yr]
            h_seasons = historic_ts.get(yr, {})
            bm_seasons = baseline_model_ts.get(yr, {})
            for s in seasons:
                hv = h_seasons.get(s) if isinstance(h_seasons, dict) else None
                bmv = bm_seasons.get(s) if isinstance(bm_seasons, dict) else None
                row.append(f"{hv:.4f}" if isinstance(hv, (int, float)) else "")
                row.append(f"{bmv:.4f}" if isinstance(bmv, (int, float)) else "")
                row.append("")
            writer.writerow(row)

        for yr in range(fut_start, fut_end + 1):
            row = [yr]
            fm_seasons = future_model_ts.get(yr, {})
            for s in seasons:
                fmv = fm_seasons.get(s) if isinstance(fm_seasons, dict) else None
                row.append("")
                row.append("")
                row.append(f"{fmv:.4f}" if isinstance(fmv, (int, float)) else "")
            writer.writerow(row)


def _write_station_comparison_seasonal_csv(
    path: Union[str, Path],
    metadata_row: List[object],
    historic_ts: SeasonalSeries,
    model_payloads: Dict[str, Dict[str, SeasonalSeries]],
    bl_start: int,
    bl_end: int,
    fut_start: int,
    fut_end: int,
) -> None:
    models = list(model_payloads.keys())
    seasons = list(SEASON_MAP.keys())
    safe_unit = _csv_safe_unit("\u00B0F")
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(metadata_row)
        header = ["Year"]
        header.extend([f"Historic {season} ({safe_unit})" for season in seasons])
        for model in models:
            header.extend([f"{model} {season} ({safe_unit})" for season in seasons])
        writer.writerow(header)

        for yr in range(bl_start, bl_end + 1):
            row = [yr]
            hist_year = historic_ts.get(yr, {})
            row.extend(
                f"{hist_year.get(season):.4f}" if isinstance(hist_year.get(season), (int, float)) else ""
                for season in seasons
            )
            for model in models:
                year_data = model_payloads[model]["baseline"].get(yr, {})
                row.extend(
                    f"{year_data.get(season):.4f}" if isinstance(year_data.get(season), (int, float)) else ""
                    for season in seasons
                )
            writer.writerow(row)

        for yr in range(fut_start, fut_end + 1):
            row = [yr]
            row.extend("" for _ in seasons)
            for model in models:
                year_data = model_payloads[model]["future"].get(yr, {})
                row.extend(
                    f"{year_data.get(season):.4f}" if isinstance(year_data.get(season), (int, float)) else ""
                    for season in seasons
                )
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Lightweight debug / self-check helpers
# ---------------------------------------------------------------------------


def validate_pg_stat_preparation(yearly_daily: YearlyDaily) -> Dict[str, Optional[float]]:
    high_mean_c, high_sigma_c = extract_pg_high_air_statistics(yearly_daily)
    low_mean_c, low_sigma_c = extract_pg_low_air_statistics(yearly_daily)
    return {
        "pg_high_air_mean_c": high_mean_c,
        "pg_high_air_sigma_c": high_sigma_c,
        "pg_low_air_mean_c": low_mean_c,
        "pg_low_air_sigma_c": low_sigma_c,
    }


def validate_precip_annual_max_series(yearly_daily: YearlyDaily) -> Dict[str, Optional[float]]:
    ams = summarize_annual_max_series(yearly_daily)
    return {
        "count_years": float(len(ams)),
        "mean_annual_max": safe_mean(ams),
        "std_annual_max": safe_pstdev(ams),
    }


def validate_summary_logic(historic: Optional[float], future: Optional[float]) -> Dict[str, Optional[float]]:
    diff = None if historic is None or future is None else future - historic
    return {
        "historic": historic,
        "future": future,
        "difference": diff,
        "pct_change": pct_change(future, historic),
    }


def _csv_safe_unit(unit: str) -> str:
    return str(unit).replace("\u00B0F", "deg F").replace("\u00B0C", "deg C")


if __name__ == "__main__":
    print("index_parameter_calculator.py loaded successfully.")
    print("PG reliability options:", PG_RELIABILITY_MAP)
