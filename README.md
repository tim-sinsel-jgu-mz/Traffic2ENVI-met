# Traffic to ENVI-met (QGIS Plugin)

![QGIS Version](https://img.shields.io/badge/QGIS-3.x-green)
![License](https://img.shields.io/badge/License-GPLv3-blue.svg)

A QGIS plugin that converts raw traffic trajectory data and OSM street networks into ENVI-met JSON line emitters (`projectdatabase.edb`). 

This tool is designed for urban climatologists and environmental modelers who need to translate real-world traffic flows into high-resolution spatial emission sources (NOx and PM10) for microclimate simulations in ENVI-met.

## Features
* **Spatial Intersection:** Matches vehicle trajectories to adjacent OSM street segments.
* **Smart Segmentation:** Splits long street geometries and merges neighboring segments based on similarity tolerances to optimize simulation processing time.
* **Emission Calculations:** Automatically calculates emission factors (NO, NO2, PM10, PM2.5) based on user-defined inputs and ratios.
* **Direct Export:** Generates ENVI-met ready JSON database files (`.edb`) alongside a spatial `.gpkg` for verification in QGIS.
* **Thread-Safe Processing:** Heavy spatial operations run in the background, keeping QGIS responsive.

## Installation

### Via QGIS Plugin Repository (Recommended)
1. Open QGIS.
2. Go to **Plugins** -> **Manage and Install Plugins...**
3. Search for **Traffic to ENVI-met**.
4. Click **Install Plugin**.

### Manual Installation (From GitHub)
1. Download this repository as a `.zip` file.
2. Open QGIS and navigate to **Plugins** -> **Manage and Install Plugins...** -> **Install from ZIP**.
3. Select the downloaded `.zip` file and install.

## Usage

1. **Prepare your inputs:** You need a street layer (Line geometry, ideally filtered OSM data) and a trajectory layer (Line geometry with timestamp and unique Trip ID fields).
2. Click the **Traffic to ENVI-met** icon in your QGIS toolbar.
3. Select your input layers from the dropdowns. The plugin will attempt to auto-detect your Datetime and Trip ID fields.
4. Adjust the **Search Radius**, **Segment Split Sizes**, and **Scaling Factors** to fit your dataset.
5. Set the **Time Offset** from the trajectory clock to the ENVI-met model clock. ENVI-met applies the emission profile on its fixed model time zone (e.g. UTC+1) without daylight saving time, so trajectories in local clock time need `-1` for a summer simulation (CEST) and `0` in winter (CET). Summer and winter simulations therefore need separate databases.
6. Change the base **Emission Factors** (g/km) for NOx (as NO2-equivalent) and PM10 (total, including PM2.5) and the **Split Ratios**, if necessary.
7. Select an output destination for your resulting GeoPackage that holds the line emissions with ENVI-met database item column to be gridded as model area sources with the Geodata2ENVI-met plugin.
8. Click **Execute**. The plugin will generate the `.gpkg` map layer and output the `projectdatabase.edb` directly into the same folder.

### Input requirements and outputs
* The street layer needs a projected CRS in metres (e.g. UTM); trajectories in another CRS are reprojected to it. Layer filters set in QGIS are respected. Temporary (memory) layers have to be saved to a file first.
* Streets are filtered to the road classes primary, secondary, residential and their links if the layer has a Geofabrik-style `fclass` field; otherwise all lines are used.
* Where parallel street lines lie within twice the search radius of each other (dual carriageways mapped as two lines, service roads), a trip is counted only on the line it passes closest to, so both directions are not counted on both lines. Crossing streets are not affected.
* The trip time field may hold seconds after midnight, a time or a date-time. Trips without a time are skipped; trips starting after 24:00 are wrapped into the daily profile.
* The GeoPackage `hour_XX` fields hold the vehicles per hour of the interval `[XX:00, XX+1:00)` on the model clock. ENVI-met interpolates the emission profile linearly between full hours, so the database value at `XX:00` is the mean of the intervals before and after it; the daily total is unchanged.
* An existing `projectdatabase.edb` in the output folder is updated: emitters from an earlier run of this plugin are replaced, all other emitters and database items are kept. An existing GeoPackage keeps its other layers.

## Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License
[GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0)