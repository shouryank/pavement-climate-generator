from csv import DictReader, reader as csv_reader
import re
from collections import defaultdict
from pathlib import Path
from math import sin, cos, tan, pi, radians, acos
from datetime import datetime

# ---------------- CONFIG (adjust if your HCD layout differs) ----------------
HAS_HEADER = False          # set True if HCD has a header row
COL_TS       = 0   # "YYYYMMDDHH"
COL_TEMP     = 1   # Temperature (F)
COL_WIND     = 2   # Wind Speed (mph)
COL_PCTSUN   = 3   # Percent Sunshine (0–100)
COL_RAIN     = 4   # Rainfall (in)
COL_RH       = 5   # Relative Humidity (%)  # parsed but not used
# ---------------------------------------------------------------------------

RAD_DAT_PATH = "rad.dat"    # month,day,declination_deg,ratio (as in your setup)

def read_text(path):
    return Path(path).read_text(encoding="utf-8", errors="ignore")

def write_text(path, text):
    Path(path).write_text(text, encoding="utf-8")

# ---------- DGPX parsing helpers ----------

def split_shell(dgpx_text):
    """Return (prefix, records_text, suffix) for the whole <ClimateRecord> region."""
    open_pat = re.compile(r"<\s*ClimateRecord\s*>", re.IGNORECASE)
    close_pat = re.compile(r"</\s*ClimateRecord\s*>", re.IGNORECASE)
    opens = list(open_pat.finditer(dgpx_text))
    closes = list(close_pat.finditer(dgpx_text))
    if not opens or not closes:
        raise ValueError("No <ClimateRecord> tags found.")
    first_open = opens[0].start()
    last_close = closes[-1].end()
    return dgpx_text[:first_open], dgpx_text[first_open:last_close], dgpx_text[last_close:]

def extract_template(records_text):
    # Find first ClimateRecord block
    m = re.search(r"(<\s*ClimateRecord\s*>)(.*?)(</\s*ClimateRecord\s*>)",
                  records_text, re.IGNORECASE | re.DOTALL)
    if not m:
        raise ValueError("No <ClimateRecord> block found in records region.")
    block = m.group(0)

    # Newline style
    eol = "\r\n" if "\r\n" in block else "\n"

    # Capture the <dailyRecord> header line (to learn its indent)
    daily_hdr_m = re.search(
        r"^[ \t]*(<\s*dailyRecord\s*>)([^\r\n]*)(\r?\n)",
        block, flags=re.IGNORECASE | re.MULTILINE
    )
    if not daily_hdr_m:
        raise ValueError("Template missing <dailyRecord> header line.")

    daily_indent = re.match(r"^[ \t]*", daily_hdr_m.group(0)).group(0)
    daily_tag_literal = daily_hdr_m.group(1)        # "<dailyRecord>"
    daily_tag = f"{daily_indent}{daily_tag_literal}"
    daily_hdr_eol = daily_hdr_m.group(3)

    # Decide the <ClimateRecord> indent: exactly two spaces less than daily
    if daily_indent.endswith("  "):
        open_indent = daily_indent[:-2]
    elif daily_indent.startswith("\t"):
        # If template uses tabs, make ClimateRecord one tab less (or none)
        open_indent = daily_indent[:-1] if len(daily_indent) >= 1 else ""
    else:
        # Fallback: keep same indent as daily, then daily will still be deeper by hourly indent
        open_indent = daily_indent

    # Learn hourly indent from first "0 ..." line; fallback to daily + two spaces
    hourly_line_m = re.search(r"^([ \t]*)0[ \t]+.*$", block, flags=re.MULTILINE)
    hourly_indent = hourly_line_m.group(1) if hourly_line_m else (daily_indent + "  ")

    # Build clean wrappers (no stray lowercase tags)
    before_daily_hdr = f"{open_indent}<ClimateRecord>{eol}"
    daily_close = f"</dailyRecord>{eol}"
    climate_close = f"{open_indent}</ClimateRecord>{eol}"

    return {
        "before_daily_hdr": before_daily_hdr,
        "daily_tag": daily_tag,           # includes daily_indent
        "daily_hdr_eol": daily_hdr_eol,   # preserve CRLF/LF
        "hourly_indent": hourly_indent,   # indent for hourly rows
        "daily_close": daily_close,       # </dailyRecord> with daily_indent
        "climate_close": climate_close,   # </ClimateRecord> with open_indent
    }

def get_latitude_and_water_table(dgpx_text):
    """Extract latitude and annualWaterTable from <climateData>."""
    # latitude
    lat_m = re.search(r"<\s*latitude\s*>\s*([-\d\.]+)\s*</\s*latitude\s*>", dgpx_text, re.IGNORECASE)
    latitude = float(lat_m.group(1)) if lat_m else 0.0
    # annual water table depth (feet)
    wt_m = re.search(r"<\s*annualWaterTable\s*>\s*([-\d\.]+)\s*</\s*annualWaterTable\s*>", dgpx_text, re.IGNORECASE)
    wtable = float(wt_m.group(1)) if wt_m else 0.0
    return latitude, wtable

# ---------- HCD & RAD parsing ----------

def parse_hcd(hcd_csv_path):
    """
    Returns: dict[(Y,M,D)] -> list of 24 dicts with fields:
      hour, temp, rain, wind, pcts
    """
    per_day = defaultdict(lambda: [None]*24)
    with open(hcd_csv_path, "r", newline="") as f:
        rdr = csv_reader(f)
        if HAS_HEADER:
            next(rdr, None)
        for row in rdr:
            if not row:
                continue
            ts = re.sub(r"\D", "", row[COL_TS].strip())
            if len(ts) < 10:
                continue
            Y, M, D, HH = int(ts[:4]), int(ts[4:6]), int(ts[6:8]), int(ts[8:10])
            if not (0 <= HH <= 23):
                continue
            try:
                temp = float(row[COL_TEMP])
            except: temp = 0.0
            try:
                wind = float(row[COL_WIND])
            except: wind = 0.0
            try:
                pcts = float(row[COL_PCTSUN])
            except: pcts = 0.0
            try:
                rain = float(row[COL_RAIN])
            except: rain = 0.0
            
            rh = float(row[COL_RH]) if row[COL_RH] else 0.0

            per_day[(Y, M, D)][HH] = {
                "hour": HH, "temp": temp, "rain": rain, "wind": wind, "pcts": int(round(pcts)), "rh": rh
            }
    # fill missing hours with zeros
    for key, arr in per_day.items():
        for h in range(24):
            if arr[h] is None:
                arr[h] = {"hour": h, "temp": 0.0, "rain": 0.0, "wind": 0.0, "pcts": 0, "rh": 0.0}
    return per_day

def parse_rad_dat(path):
    """
    rad.dat: lines of 'month,day,declination_deg,ratio'
    Returns dict[(M,D)] -> (decl_deg, ratio)
    """
    out = {}
    with open(path, "r") as f:
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                try:
                    M, D = int(parts[0]), int(parts[1])
                    decl, ratio = float(parts[2]), float(parts[3])
                    out[(M, D)] = (decl, ratio)
                except:
                    pass
    return out

# ---------- Solar pieces (Sunrise/Sunset + EICM-like radiation) ----------

def safe_acos(x):
    return acos(max(-1.0, min(1.0, x)))

def daylength_hours(latitude_deg, declination_deg):
    """Approximate photoperiod in hours (local solar time)."""
    phi = radians(latitude_deg)
    delta = radians(declination_deg)
    cosH0 = -tan(phi) * tan(delta)
    try:
        H0 = safe_acos(cosH0)              # radians
    except:
        # polar day/night fallback
        H0 = 0.0 if cosH0 > 1 else pi
    return (2 * H0) * (24.0 / (2*pi))       # 24/pi * H0

def sunrise_sunset_decimals(latitude_deg, declination_deg):
    """Return (sunrise_hour_decimal, sunset_hour_decimal), local solar time."""
    dl = daylength_hours(latitude_deg, declination_deg)
    sunrise = max(0.0, 12.0 - dl/2.0)
    sunset  = min(24.0, 12.0 + dl/2.0)
    return sunrise, sunset

def calculate_eicm_radiation(latitude_deg, declination_deg, ratio):
    """
    Same approach as in your reference script (units consistent with rad.dat & EICM). 
    """
    lat_rad = radians(min(latitude_deg, 66.0))
    frdec = radians(declination_deg)
    a = sin(frdec) if frdec >= 0 else -sin(abs(frdec))
    b = cos(abs(frdec))
    try:
        cos_HA = -tan(lat_rad) * (a / b)
        HA = safe_acos(abs(cos_HA))
    except ValueError:
        HA = 0
    z = pi - HA if cos_HA < 0 else HA
    x = tan(z)
    solar_rad = (24 / pi) * 444.7 * ratio * sin(lat_rad) * a * (z - x)
    return solar_rad

# ---------- Rebuild records ----------

def overwrite_daily_header_line(daily_tag, eol, m, d, y, sunrise, sunset, solar_rad):
    return f"{daily_tag}{m} {d} {y} {sunrise:.3f} {sunset:.3f} {solar_rad:.3f}{eol}"

# Change the DGPX hourly output format (field order and decimal precision) here in the future.
def format_hour_line(indent, hour, temp_f, rain_in, wind_mph, pcts, water_tbl, rh):
    return (
        f"{indent}{hour} "
        f"{temp_f:.6f} "     # Temperature (F) — 6 decimals
        f"{float(rain_in):.6f} "  # Rain (in) — 6 decimals
        f"{float(wind_mph):.3f} " # Wind (mph) — 3 decimals
        f"{int(round(pcts))} "
        f"{int(round(water_tbl))} "
        f"{int(round(rh))}\n"
    )

def rebuild(dgpx_in, hcd_csv, dgpx_out):
    dgpx_text = read_text(dgpx_in)
    prefix, records_text, suffix = split_shell(dgpx_text)
    template = extract_template(records_text)
    latitude, water_table = get_latitude_and_water_table(dgpx_text)
    rad = parse_rad_dat(RAD_DAT_PATH)
    hcd = parse_hcd(hcd_csv)

    # Build new blocks ordered by date
    out_blocks = []
    for (Y, M, D) in sorted(hcd.keys()):
        decl, ratio = rad.get((M, D), (0.0, 0.0))
        srise, sset = sunrise_sunset_decimals(latitude, decl)
        solrad = calculate_eicm_radiation(latitude, decl, ratio)

        # <ClimateRecord> preamble from template
        out_blocks.append(template["before_daily_hdr"])
        # daily header with new format
        out_blocks.append(overwrite_daily_header_line(
            template["daily_tag"], template["daily_hdr_eol"],
            M, D, Y, srise, sset, solrad
        ))
        # 24 hours
        for row in hcd[(Y, M, D)]:
            out_blocks.append(
                format_hour_line(
                    template["hourly_indent"],
                    row["hour"], row["temp"], row["rain"], row["wind"], row["pcts"], water_table, row["rh"]
                )
            )
        # closers
        out_blocks.append(template["daily_close"])
        out_blocks.append(template["climate_close"])

    wrapped = "\t<climateRecord>\n" + "".join(out_blocks) + "\t</climateRecord>"

    write_text(dgpx_out, prefix + wrapped + suffix)
    print(f"Wrote: {dgpx_out}")

# if __name__ == "__main__":
#     import argparse
#     ap = argparse.ArgumentParser(description="Rewrite DGPX <ClimateRecord>s from HCD hourly CSV.")
#     ap.add_argument("dgpx_in")
#     ap.add_argument("hcd_csv")
#     ap.add_argument("-o", "--out", default=None)
#     args = ap.parse_args()
#     out_path = args.out or (Path.cwd() / (Path(args.dgpx_in).stem + "_new.dgpx"))
#     rebuild(args.dgpx_in, args.hcd_csv, out_path)
