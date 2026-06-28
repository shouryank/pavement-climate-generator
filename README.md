# Pavement Climate Generator

## Overview

Pavement Climate Generator is a desktop Tkinter application for preparing climate inputs used in pavement and ME Design workflows. The application currently provides four main tools:

1. Create future weather files (`.hcd`) for a selected city, scenario, and model.
2. Run future weather file generation in batch mode for many cities or many source HCD files.
3. Modify ME Design climate files (`.dgpx`) using generated HCD data.
4. Calculate climate index parameters for a latitude/longitude point using nearby grid stations and climate model data.

The application is organized around a main menu and a shared Settings page that controls the input and output locations used by all modules.

## Main Modules

### Main Menu

The main menu contains these actions:

- `Create Future Weather File (.hcd)`
- `Create Future Weather File (Batch mode)`
- `Modify Climate in ME Design File (.dgpx)`
- `Modify Climate in ME Design File (Batch mode)`
- `Index Parameter Calculator`
- `Settings`

### Shared Data Sources

The app relies on these configured paths:

- `closest_stations.csv`
- `us_grid_list.xlsx`
- `Scenario root`
- `HCD input root`
- `Output HCD base`
- `Output DGPX base`
- `Index parameter analysis folder`

## Required Folder and File Structure

### Closest Stations CSV

`closest_stations.csv` is used for city-based workflows. It should contain at least:

- `City`
- `Latitude`
- `Longitude`
- `Closest Station`

If available, the app also reads:

- `Station Latitude`
- `Station Longitude`
- `State`

### Full Station Grid Workbook

`us_grid_list.xlsx` is used by the Index Parameter Calculator to find the nearest grid stations for a user-entered latitude and longitude. This workbook is treated as the exhaustive station list.

Expected columns in the first three worksheet columns are:

- Station ID
- Latitude
- Longitude

### Scenario Root

The Scenario root folder must contain at least:

- `RCP45`
- `RCP85`

Each scenario folder should contain model subfolders. The Index Parameter module also uses a `historical` scenario folder for baseline calculations.

### HCD Input Root

This folder stores the source HCD files used by the Future Weather File generator.

### Output HCD Base

Generated future HCD files are written here.

### Output DGPX Base

Generated DGPX files are written here.

### Index Parameter Analysis Folder

Index Parameter summary CSVs, text summaries, and station comparison outputs are written here.

## Settings Page

Use the Settings screen before running the tools for the first time or after moving data folders.

### Settings Fields

- `Closest stations CSV`
  - Path to the city-to-station mapping file used by city workflows.
- `Scenario root`
  - Path to the climate model folder structure.
- `HCD input root`
  - Path to the input HCD files used for future weather generation.
- `Output HCD base`
  - Destination folder for generated HCD files.
- `Output DGPX base`
  - Destination folder for generated DGPX files.
- `Index parameter analysis folder`
  - Destination folder for index parameter results.

### Save

`Save` validates the configured paths and reloads the city mapping from the selected CSV.

### Delete Generated Files

This action removes generated outputs:

- HCD folders under the output HCD base, except `RCP45-Pre loaded` and `RCP85-Pre loaded`
- All items under the DGPX output base
- All items under the index analysis output folder

Use this carefully. It is intended for cleanup between runs.

## Workflow 1: Create Future Weather File (.hcd)

### Purpose

Generates a future climate HCD file for one city, scenario, and model.

### Inputs on Screen

- `City`
  - Single-select searchable dropdown.
  - The selected city determines the mapped closest station and displayed coordinates.
- `Closest Station`
  - Read-only field populated from `closest_stations.csv`.
- `Latitude`
  - Read-only field from the selected city record.
- `Longitude`
  - Read-only field from the selected city record.
- `Scenario`
  - Scenario folder chosen from the Scenario root.
- `Model`
  - Model folder under the selected scenario.

### Special Models

The generator highlights three special models:

- `Hot — GFDL-CM3`
- `Medium — HadGEM2-CC`
- `Cold — MRI-CGCM3`

These are convenience labels only. The underlying model folder names are the model names shown above.

### What Happens During Generation

The tool:

1. Looks up the city’s mapped station.
2. Opens the source HCD file from the HCD input root.
3. Reads climate model data from the selected scenario and model under Scenario root.
4. Writes the generated future HCD file under the output HCD base.

### Typical Walkthrough

1. Open `Create Future Weather File (.hcd)`.
2. Select a city.
3. Confirm the closest station and coordinates.
4. Select a scenario.
5. Select a model.
6. Click `Generate`.
7. Wait for progress to complete.
8. Check the generated HCD file in the output HCD base.

## Workflow 2: Create Future Weather File (Batch mode)

### Purpose

Runs HCD generation for many jobs at once.

### Operating Modes

- `Select cities`
  - Generates files for selected cities, scenarios, and models.
- `Use HCD folder`
  - Processes every `.hcd` file in a selected folder against the selected scenarios and models.

### Inputs on Screen

- `Cities`
  - Multi-select searchable dropdown.
- `HCD Folder`
  - Folder containing source `.hcd` files when using folder mode.
- `Scenarios`
  - Multi-select scenario list.
- `Models`
  - Multi-select model list.
- `Output Base`
  - Read-only destination path for generated HCDs.

### Typical Walkthrough for City Mode

1. Open `Create Future Weather File (Batch mode)`.
2. Leave the mode on `Select cities`.
3. Select one or more cities.
4. Select one or more scenarios.
5. Select one or more models.
6. Click `Run Batch`.
7. Monitor the progress bar and log.

### Typical Walkthrough for Folder Mode

1. Open `Create Future Weather File (Batch mode)`.
2. Switch mode to `Use HCD folder`.
3. Browse to a folder containing `.hcd` files.
4. Select one or more scenarios.
5. Select one or more models.
6. Click `Run Batch`.
7. Monitor the progress bar and log.

## Workflow 3: Modify Climate in ME Design File (.dgpx)

### Purpose

Updates a DGPX file’s climate station block and rebuilds its climate records using a generated HCD file.

### Inputs on Screen

- `DGPX file`
  - Input `.dgpx` file to modify.
- `City`
  - City whose climate station mapping should be used.
- `Scenario`
  - Scenario to match the generated HCD file.
- `Model`
  - Model to match the generated HCD file.
- `Elevation`
  - Elevation written into the DGPX climate station block.
- `Output file name`
  - File name for the new DGPX.
- `Will save to`
  - Read-only output path preview.

### Important Dependency

This workflow expects the required HCD file to already exist in the HCD output structure. If the required HCD is missing, the app prompts you to generate it first.

### Typical Walkthrough

1. Open `Modify Climate in ME Design File (.dgpx)`.
2. Browse to the source DGPX file.
3. Select a city.
4. Select a scenario.
5. Select a model.
6. Enter elevation.
7. Review the output file name and output path preview.
8. Click `Build DGPX`.
9. Open the generated DGPX from the DGPX output base.

## Workflow 4: Modify Climate in ME Design File (Batch mode)

### Purpose

Creates many DGPX outputs from one source DGPX using multiple city, scenario, and model combinations.

### Inputs on Screen

- `Input DGPX (single file)`
- `Elevation (ft)`
- `Cities`
- `Scenarios`
- `Models`
- `DGPX Output Base`

### Typical Walkthrough

1. Open `Modify Climate in ME Design File (Batch mode)`.
2. Select the input DGPX file.
3. Enter the elevation.
4. Select one or more cities.
5. Select one or more scenarios.
6. Select one or more models.
7. Click `Run DGPX Batch`.
8. Review the progress bar and log.

## Workflow 5: Index Parameter Calculator

### Purpose

Calculates temperature, precipitation, and PG-style climate metrics for a user-specified latitude and longitude.

### Baseline and Future Data Sources

The current implementation uses:

- `historical` model data under Scenario root for the baseline
- selected future scenario/model data under Scenario root for future values

The module does not currently require `_US.hcd` files for baseline computation.

### Station Selection Logic

For a user-entered latitude and longitude, the tool:

1. Loads the full station list from `us_grid_list.xlsx`.
2. Computes the distance from the input point to each station using the haversine formula.
3. Selects the nearest `1`, `4`, or `9` stations based on the selected station grid.

### Inputs on Screen

- `Latitude`
  - Latitude of the location of interest.
- `Longitude`
  - Longitude of the location of interest.
- `Analysis Name`
  - Name for the result set and output folder.
- `Scenarios`
  - Multi-select list of future scenarios.
- `Models`
  - Multi-select list of models available under the selected scenarios.

### Year Range Fields

- `Baseline Start`
  - Beginning year of the historical baseline period.
- `End`
  - Ending year of the historical baseline period.
- `Future Start`
  - Beginning year of the future analysis period.
- `End`
  - Ending year of the future analysis period.

### PG Reliability

Available options:

- `50%`
- `98%`

This affects PG high and PG low calculations by changing the reliability factor used in the PG formulas.

### Heatwave Threshold

`Heatwave Threshold (°F)` defines the threshold used by the Heatwave Duration metric. The current default is `95°F`.

### Station Grid

- `1x1`
  - Use the nearest 1 station.
- `2x2`
  - Use the nearest 4 stations.
- `3x3`
  - Use the nearest 9 stations.

The selected stations are distance-weighted when summary values are aggregated.

### Parameter Output Types

Each parameter row has two checkboxes:

- `Time Series`
  - Writes year-by-year output files for the selected parameter.
- `Summary`
  - Includes the parameter in the summary display and summary exports.

### Parameter Definitions

#### Temperature-Based

- `Heatwave Duration`
  - Longest annual run of days above the heatwave threshold.

#### Temperature Extremes

- `Annual Extreme Heat`
  - Hottest temperature observed in each year.
- `Seasonal Extreme Heat`
  - Seasonal extreme heat summaries by season.
- `Extreme Cold`
  - Coldest temperature observed in each year.

#### Precipitation

- `Mean Annual Precipitation`
  - Annual total precipitation.
- `Max 24-hour Precipitation`
  - Maximum daily precipitation within a year.
- `Std Dev of Extreme Rainfall`
  - Standard deviation of annual maximum daily precipitation.
- `Return Period Rainfall (100-yr)`
  - Approximate precipitation return period metric.

#### Climate Change (PG)

- `PG High Temperature`
  - PG high temperature statistic.
- `PG Low Temperature`
  - PG low temperature statistic.
- `PG Change Factor (High)`
  - Change factor between historical and future PG high.
- `PG Change Factor (Low)`
  - Change factor between historical and future PG low.
- `Final Projected PG (High)`
  - Final projected PG high after applying the change factor to the baseline.
- `Final Projected PG (Low)`
  - Final projected PG low after applying the change factor to the baseline.

### Future Period Rule for Precipitation

If the future period is less than 30 years:

- precipitation analysis is blocked
- the app warns that the future period is too short for precipitation analysis

This matters for:

- `Mean Annual Precipitation`
- `Max 24-hour Precipitation`
- `Std Dev of Extreme Rainfall`
- `Return Period Rainfall (100-yr)`

### Typical Walkthrough

1. Open `Index Parameter Calculator`.
2. Enter latitude and longitude.
3. Enter an analysis name.
4. Select one or more scenarios.
5. Select one or more models.
6. Set baseline and future year ranges.
7. Choose the PG reliability level.
8. Set the heatwave threshold if needed.
9. Choose the station grid.
10. Select the parameters to calculate.
11. Choose `Summary`, `Time Series`, or both for each parameter.
12. Click `Calculate Index Parameters`.
13. Review the selected stations shown in the output summary.
14. Open the generated CSV and TXT outputs in the index analysis folder.

## Output Locations

### Generated HCD Files

Written under the configured HCD output base.

### Generated DGPX Files

Written under the configured DGPX output base.

### Index Parameter Files

Written under the configured index parameter analysis folder. Outputs can include:

- per-scenario summary CSVs
- a root `summary.csv`
- a root `summary.txt`
- station comparison CSVs

## Interpreting Progress and Logs

Most long-running screens include:

- a progress bar
- a progress label
- a log area

The log is the best place to diagnose missing files, skipped jobs, invalid selections, or path issues.

## Troubleshooting

### Closest Station CSV Will Not Load

Check that the CSV exists and has the required city, latitude, longitude, and station columns.

### Scenario or Model Lists Are Empty

Check the Scenario root path and confirm that the expected scenario folders and model subfolders exist.

### Generator Cannot Find an HCD Input

Check the HCD input root and confirm the source station HCD file exists.

### DGPX Builder Says HCD Is Missing

Run the HCD generation workflow first for that city, scenario, and model.

### Index Parameter Run Fails on Station Lookup

Check that `us_grid_list.xlsx` is available and contains station IDs with latitude and longitude values.

### Index Parameter Run Produces Unexpected Results

Check these items:

- baseline and future year ranges
- selected station grid
- selected models
- PG reliability level
- heatwave threshold
- whether you selected summary output, time series output, or both

## Recommended First-Time Setup

1. Open `Settings`.
2. Set all paths.
3. Save the configuration.
4. Run one single-city HCD generation job.
5. Run one single DGPX build.
6. Run one simple Index Parameter calculation using `1x1` station grid and summary-only output.
7. Review the output folders before starting large batch jobs.

## Known Implementation Notes

- The Index Parameter Calculator currently uses model `historical` data as baseline.
- Latitude/longitude station selection uses `us_grid_list.xlsx`, not only the city CSV.
- DGPX batch mode expects generated HCD files to exist before rebuilding climate records.
- Special models are exposed with user-friendly labels in some screens.

## Suggested Validation Workflow

For a new dataset or path configuration:

1. Validate `Settings`.
2. Run a single HCD generation job.
3. Confirm the output HCD exists.
4. Run a single DGPX build using that HCD.
5. Run a single Index Parameter summary.
6. Compare the summary outputs against known reference values before launching batch jobs.
