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
5. Change the base **Emission Factors** (g/km) for NOx and PM10, if necessary.
6. Select an output destination for your resulting GeoPackage that holds the line emissions with ENVI-met database item column to be gridded as model area sources with the Geodata2ENVI-met plugin.
7. Click **Start**. The plugin will generate the `.gpkg` map layer and output the `projectdatabase.edb` directly into the same folder.

## Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License
[GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0)