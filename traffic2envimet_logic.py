import os
import json
import math
import datetime
from collections import deque
import processing
from qgis.core import (
    QgsProject, QgsFeature, QgsGeometry, QgsSpatialIndex, QgsField,
    QgsVectorLayer, QgsVectorFileWriter, QgsTask, QgsProcessingContext, QgsFeatureRequest
)
from qgis.PyQt.QtCore import pyqtSignal, QVariant, QMetaType, QDateTime, QTime

# --- Universal Field Types (QGIS 3 & QGIS 4 Compatibility) ---
# QgsField takes QMetaType.Type from QGIS 3.38 on (and only that in QGIS 4); older
# versions expose QMetaType.Type through PyQt5 but accept only QVariant.Type.
try:
    QgsField("probe", QMetaType.Type.Int)
    FIELD_TYPE_STRING = QMetaType.Type.QString
    FIELD_TYPE_INT = QMetaType.Type.Int
except (AttributeError, TypeError):
    FIELD_TYPE_STRING = QVariant.String
    FIELD_TYPE_INT = QVariant.Int

try:
    TASK_CANCEL_FLAG = QgsTask.Flag.CanCancel
except AttributeError:
    TASK_CANCEL_FLAG = QgsTask.CanCancel

try:
    WRITER_NO_ERROR = QgsVectorFileWriter.WriterError.NoError
    OVERWRITE_LAYER = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
    OVERWRITE_FILE = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
except AttributeError:
    WRITER_NO_ERROR = QgsVectorFileWriter.NoError
    OVERWRITE_LAYER = QgsVectorFileWriter.CreateOrOverwriteLayer
    OVERWRITE_FILE = QgsVectorFileWriter.CreateOrOverwriteFile

ROAD_CLASSES = ('primary', 'primary_link', 'residential', 'secondary', 'secondary_link')

# Remark that marks the emitters this plugin writes, so a re-run replaces them in an existing database
GENERATED_REMARK = "Generated Line Source"

HOUR_FIELDS = [f"hour_{h:02d}" for h in range(24)]

# Temporary field that numbers the street lines before they are split into segments
LINE_ID_FIELD = "_t2e_line"

# Distance [m] within which two street lines count as equally near to a passing trip
NEAREST_TIE = 0.01

# Street lines within 30 degrees of each other count as parallel
PARALLEL_COS = math.cos(math.radians(30.0))


def direction_at(line_geom, near_geom):
    """Unit direction of the part of a line closest to the centre of another geometry."""
    _, _, after_vertex, _ = line_geom.closestSegmentWithContext(near_geom.centroid().asPoint())
    a, b = line_geom.vertexAt(after_vertex - 1), line_geom.vertexAt(after_vertex)
    dx, dy = b.x() - a.x(), b.y() - a.y()
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length else (0.0, 0.0)


def dot(u, v):
    return u[0] * v[0] + u[1] * v[1]


def seconds_after_midnight(value):
    """Trip start as seconds after midnight from a numeric, QTime or QDateTime field; None if unusable."""
    if value is None or (isinstance(value, QVariant) and value.isNull()):
        return None
    if isinstance(value, QDateTime):
        return value.time().msecsSinceStartOfDay() / 1000.0 if value.isValid() else None
    if isinstance(value, QTime):
        return value.msecsSinceStartOfDay() / 1000.0 if value.isValid() else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def trajectory_pieces(geom, extent, piece_length):
    """The part of a trajectory inside the extent, cut into pieces of at most piece_length.
    Short pieces have small bounding boxes, so the spatial index only returns trips that
    actually pass a street segment instead of every trip whose whole-trip box covers it."""
    clipped = geom.clipped(extent)
    if clipped.isNull() or clipped.isEmpty():
        return
    clipped = clipped.densifyByDistance(piece_length)
    parts = clipped.asMultiPolyline() if clipped.isMultipart() else [clipped.asPolyline()]
    for part in parts:
        start, length = 0, 0.0
        for i in range(1, len(part)):
            length += part[i - 1].distance(part[i])
            if length >= piece_length or i == len(part) - 1:
                yield QgsGeometry.fromPolylineXY(part[start:i + 1])
                start, length = i, 0.0


def emission_node_values(hourly_counts):
    """ENVI-met holds emission[h] at h:00 and interpolates linearly to the next full hour.
    A count for the interval [h, h+1) therefore belongs to h:30; the value at h:00 is the
    mean of the intervals before and after it. The daily total is unchanged."""
    return [(hourly_counts[h - 1] + hourly_counts[h]) / 2.0 for h in range(24)]


def write_project_database(path, emitters, log):
    """Write the emitters to projectdatabase.edb. An existing database keeps all its other items
    and emitters; emitters from an earlier run of this plugin are replaced."""
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8-sig') as f:
            db = json.load(f)
        root = db.get('envimetDatafile')
        if not isinstance(root, dict):
            raise ValueError(f"{path} is not an ENVI-met JSON database (no 'envimetDatafile' root).")
        kept = [e for e in root.get('emitters', []) if e.get('remark') != GENERATED_REMARK]
        new_ids = {e['id'] for e in emitters}
        clashes = sorted(e.get('id') for e in kept if e.get('id') in new_ids)
        if clashes:
            raise ValueError(f"{path} already holds emitters not written by this plugin with the IDs "
                             f"{', '.join(clashes[:5])}. Choose another output folder.")
        root['emitters'] = kept + emitters
        root.setdefault('header', {})['revisionDate'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        log(f"Updated the existing {os.path.basename(path)}: {len(emitters)} traffic emitters, "
            f"{len(kept)} other emitters and all other database items kept.")
    else:
        db = {
            "envimetDatafile": {
                "header": {
                    "fileType": "databaseJSON",
                    "version": 1,
                    "revisionDate": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "remark": "Auto-generated by QGIS Trajectory Script",
                    "description": "Traffic Emission Line Sources"
                },
                "emitters": emitters
            }
        }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(db, f, indent=4)


class TrafficEnviTask(QgsTask):
    """Background task to calculate traffic trajectories and emissions."""

    log_message = pyqtSignal(str)

    def __init__(self, description, params, on_finished_callback):
        super().__init__(description, TASK_CANCEL_FLAG)
        self.params = params
        self.on_finished_callback = on_finished_callback
        self.exception = None
        self.output_gpkg = None
        self.layer_name = None

    def open_layer(self, key, name):
        """Re-open an input layer in the task thread, with the filter it has in the project."""
        layer = QgsVectorLayer(self.params[f'{key}_source'], name, self.params[f'{key}_provider'])
        if self.params.get(f'{key}_subset'):
            layer.setSubsetString(self.params[f'{key}_subset'])
        if not layer.isValid():
            raise ValueError(f"The {name.lower()} layer could not be opened: {self.params[f'{key}_source']}")
        return layer

    def run(self):
        try:
            self.log_message.emit("--- Traffic2ENVI-met Started ---")
            datetime_field = self.params['datetime_field']
            unique_id_field = self.params['unique_id_field']
            search_radius = self.params['search_radius']
            split_length = self.params['split_length']
            similarity_tolerance = self.params['similarity_tolerance']
            scaling_factor = self.params['scaling_factor']
            hour_offset = self.params['hour_offset']
            ef_nox = self.params['ef_nox']
            ef_pm10 = self.params['ef_pm10']
            v_ratio_no2 = self.params['v_ratio_no2']
            v_ratio_pm = self.params['v_ratio_pm']

            self.output_gpkg = self.params['output_file']
            output_dir = os.path.dirname(self.output_gpkg)
            output_edb_path = os.path.join(output_dir, 'projectdatabase.edb')

            osm_layer = self.open_layer('osm', "Street")
            traj_layer = self.open_layer('traj', "Trajectory")

            crs = osm_layer.crs()
            if not crs.isValid() or crs.isGeographic():
                raise ValueError("The street layer needs a projected CRS in metres (e.g. UTM): search radius, "
                                 "segment length and emission rates per metre are all lengths.")

            self.setProgress(5.0)
            context = QgsProcessingContext()

            if traj_layer.crs() != crs:
                self.log_message.emit(f"Reprojecting trajectories from {traj_layer.crs().authid()} to {crs.authid()}...")
                traj_layer = processing.run("native:reprojectlayer", {
                    'INPUT': traj_layer, 'TARGET_CRS': crs, 'OUTPUT': 'memory:'
                }, context=context)['OUTPUT']

            # --- STEP 1 ---
            self.log_message.emit("Step 1/7: Filtering and splitting OSM lines...")
            if osm_layer.fields().indexOf('fclass') >= 0:
                expression = "\"fclass\" IN ({})".format(", ".join(f"'{c}'" for c in ROAD_CLASSES))
                street_lines = processing.run("native:extractbyexpression", {
                    'INPUT': osm_layer, 'EXPRESSION': expression, 'OUTPUT': 'memory:'
                }, context=context)['OUTPUT']
                self.log_message.emit(f"Kept road classes: {', '.join(ROAD_CLASSES)}")
            else:
                street_lines = osm_layer
                self.log_message.emit("No 'fclass' field in the street layer: all its lines are used.")

            self.setProgress(10.0)

            # number the street lines, so every segment knows the line it was cut from
            street_lines = processing.run("native:addautoincrementalfield", {
                'INPUT': street_lines, 'FIELD_NAME': LINE_ID_FIELD, 'START': 0, 'OUTPUT': 'memory:'
            }, context=context)['OUTPUT']
            line_geoms = {f[LINE_ID_FIELD]: f.geometry() for f in street_lines.getFeatures() if f.hasGeometry()}
            line_index = QgsSpatialIndex()
            for lid, geom in line_geoms.items():
                line_index.addFeature(lid, geom.boundingBox())

            split_osm = processing.run("native:splitlinesbylength", {
                'INPUT': street_lines, 'LENGTH': split_length, 'OUTPUT': 'memory:'
            }, context=context)['OUTPUT']

            segment_geoms, segment_lines = [], []
            for f in split_osm.getFeatures():
                if f.hasGeometry():
                    segment_geoms.append(f.geometry())
                    segment_lines.append(f[LINE_ID_FIELD])
            if not segment_geoms:
                raise ValueError("No street segments left after filtering and splitting.")

            if self.isCanceled(): return False
            self.setProgress(15.0)

            # --- STEP 2 ---
            self.log_message.emit("Step 2/7: Cutting trajectories and building the spatial index...")
            extent = split_osm.extent()
            extent.grow(search_radius)
            piece_length = max(10.0 * search_radius, 25.0)

            traj_index = QgsSpatialIndex()
            piece_geoms, piece_keys = [], []
            n_no_time = n_wrapped = 0
            request = QgsFeatureRequest().setFilterRect(extent)
            for feat in traj_layer.getFeatures(request):
                if self.isCanceled(): return False
                seconds = seconds_after_midnight(feat[datetime_field])
                if seconds is None or not feat.hasGeometry():
                    n_no_time += 1
                    continue
                if not 0 <= seconds < 86400:
                    n_wrapped += 1
                # trajectory clock -> ENVI-met model clock, wrapped into the 24 h profile
                hour = int(seconds // 3600 + hour_offset) % 24
                key = (feat[unique_id_field], hour)
                for piece in trajectory_pieces(feat.geometry(), extent, piece_length):
                    traj_index.addFeature(len(piece_geoms), piece.boundingBox())
                    piece_geoms.append(piece)
                    piece_keys.append(key)

            if n_no_time:
                self.log_message.emit(f"Warning: {n_no_time} trajectories without a usable time or geometry were skipped.")
            if n_wrapped:
                self.log_message.emit(f"{n_wrapped} trajectories start outside 0-24 h and were wrapped into the daily profile.")
            if hour_offset:
                self.log_message.emit(f"Trajectory hours shifted by {hour_offset:+d} h to the model clock.")

            if self.isCanceled(): return False
            self.setProgress(30.0)

            # --- STEP 3 ---
            self.log_message.emit("Step 3/7: Counting unique trajectories per segment...")
            segment_counts = []
            total_segs = len(segment_geoms)
            for idx, seg_geom in enumerate(segment_geoms):
                if self.isCanceled(): return False
                if idx % 500 == 0:
                    self.setProgress(float(30 + (idx / total_segs) * 30))

                own_line = line_geoms[segment_lines[idx]]
                # Parallel street lines (the other carriageway, a service road) that could be closer
                # to a trip within the search radius, so no further than twice the radius. Crossing
                # streets are not rivals: a trip crosses their line, which would always win.
                rival_rect = seg_geom.boundingBox()
                rival_rect.grow(2.0 * search_radius)
                rivals = []
                seg_direction = None
                for lid in line_index.intersects(rival_rect):
                    if lid == segment_lines[idx] or seg_geom.distance(line_geoms[lid]) > 2.0 * search_radius:
                        continue
                    if seg_direction is None:
                        seg_direction = direction_at(seg_geom, seg_geom)
                    if abs(dot(seg_direction, direction_at(line_geoms[lid], seg_geom))) >= PARALLEL_COS:
                        rivals.append(line_geoms[lid])

                search_rect = seg_geom.boundingBox()
                search_rect.grow(search_radius)
                found = set()
                for pid in traj_index.intersects(search_rect):
                    key = piece_keys[pid]
                    if key in found:
                        continue  # this trip is already counted for this hour
                    piece = piece_geoms[pid]
                    if seg_geom.distance(piece) > search_radius:
                        continue
                    if rivals:
                        # count the trip only on the street line nearest to where it passes
                        passing = piece.nearestPoint(seg_geom)
                        own_distance = passing.distance(own_line)
                        if any(passing.distance(line) < own_distance - NEAREST_TIE for line in rivals):
                            continue
                    found.add(key)

                counts = [0] * 24
                for _, hour in found:
                    counts[hour] += 1
                segment_counts.append(counts)

            if self.isCanceled(): return False
            self.setProgress(60.0)

            # --- STEP 4 ---
            self.log_message.emit("Step 4/7: Merging similar adjacent segments...")
            seg_index = QgsSpatialIndex()
            for sid, geom in enumerate(segment_geoms):
                seg_index.addFeature(sid, geom.boundingBox())

            visited, groups = set(), []
            for f_id in range(total_segs):
                if self.isCanceled(): return False
                if f_id % 500 == 0:
                    self.setProgress(float(60 + (f_id / total_segs) * 20))

                if f_id in visited: continue
                seed_counts = segment_counts[f_id]
                current_group, queue = [f_id], deque([f_id])
                visited.add(f_id)

                while queue:
                    curr_id = queue.popleft()
                    curr_geom = segment_geoms[curr_id]
                    bbox = curr_geom.boundingBox()
                    bbox.grow(0.01)
                    for cand_id in seg_index.intersects(bbox):
                        if cand_id in visited: continue
                        if curr_geom.distance(segment_geoms[cand_id]) >= 0.01: continue
                        cand_counts = segment_counts[cand_id]
                        if all(abs(a - b) <= similarity_tolerance for a, b in zip(seed_counts, cand_counts)):
                            visited.add(cand_id)
                            queue.append(cand_id)
                            current_group.append(cand_id)
                groups.append(current_group)

            self.setProgress(80.0)

            # --- STEP 5 ---
            self.log_message.emit("Step 5/7: Applying scaling factor...")
            final_layer = QgsVectorLayer(f"MultiLineString?crs={crs.toWkt()}", "Final_Merged_Counts", "memory")
            final_prov = final_layer.dataProvider()

            final_prov.addAttributes([
                QgsField("enviID", FIELD_TYPE_STRING, len=6),
                QgsField("total_24h", FIELD_TYPE_INT)
            ])
            for name in HOUR_FIELDS:
                final_prov.addAttributes([QgsField(name, FIELD_TYPE_INT)])
            final_layer.updateFields()

            final_feats, group_volumes = [], []
            n_zero = 0
            for envi_id_counter, grp in enumerate(groups, start=1):
                new_feat = QgsFeature(final_layer.fields())
                new_feat.setGeometry(QgsGeometry.unaryUnion([segment_geoms[fid] for fid in grp]))
                new_feat.setAttribute("enviID", f"{envi_id_counter:06d}")

                # full vehicle counts: the group mean, scaled and rounded
                hourly_volumes = [round(sum(segment_counts[fid][h] for fid in grp) / len(grp) * scaling_factor) for h in range(24)]
                for name, volume in zip(HOUR_FIELDS, hourly_volumes):
                    new_feat.setAttribute(name, volume)
                total_sum = sum(hourly_volumes)
                new_feat.setAttribute("total_24h", total_sum)
                if total_sum == 0:
                    n_zero += 1

                final_feats.append(new_feat)
                group_volumes.append((f"{envi_id_counter:06d}", hourly_volumes))

            final_prov.addFeatures(final_feats)
            if n_zero:
                self.log_message.emit(f"Warning: {n_zero} merged segments rounded to 0 vehicles across all hours.")

            if self.isCanceled(): return False
            self.setProgress(90.0)

            # --- STEP 6 ---
            self.log_message.emit("Step 6/7: Generating ENVI-met JSON database...")

            # Molar mass conversion for literal NO branch mapping
            M_NO, M_NO2 = 30.006, 46.006

            # ef_nox is NO2-equivalent and v_ratio_no2 a molar fraction. Convert NO back to literal mass.
            ef_no  = ef_nox * (1.0 - v_ratio_no2) * (M_NO / M_NO2)
            ef_no2 = ef_nox * v_ratio_no2

            # PM remains a mass fraction; ENVI-met takes emissionPM10 as total PM10 and subtracts PM2.5
            ef_pm25 = ef_pm10 * v_ratio_pm

            emitters = []
            for envi_id, hourly_volumes in group_volumes:
                # veh/h * g/km / 3.6 = ug/(m s)
                q = emission_node_values(hourly_volumes)
                emitters.append({
                    "id": envi_id, "desc": f"Traffic Line {envi_id}", "col": "81E908",
                    "grp": "Emitters", "height": 0.2, "geom": "line",
                    "emissionUsr": [0.0] * 24,
                    "emissionNO": [v * ef_no / 3.6 for v in q],
                    "emissionNO2": [v * ef_no2 / 3.6 for v in q],
                    "emissionO3": [0.0] * 24,
                    "emissionPM10": [v * ef_pm10 / 3.6 for v in q],
                    "emissionPM25": [v * ef_pm25 / 3.6 for v in q],
                    "cost": 0, "remark": GENERATED_REMARK
                })

            write_project_database(output_edb_path, emitters, self.log_message.emit)

            self.setProgress(95.0)

            # --- STEP 7 ---
            self.log_message.emit("Step 7/7: Saving output vectors to GeoPackage...")
            save_opts = QgsVectorFileWriter.SaveVectorOptions()
            save_opts.driverName = "GPKG"
            save_opts.layerName = "traffic_volume"
            # only replace the layer, not other layers in an existing GeoPackage
            save_opts.actionOnExistingFile = OVERWRITE_LAYER if os.path.exists(self.output_gpkg) else OVERWRITE_FILE
            result = QgsVectorFileWriter.writeAsVectorFormatV3(final_layer, self.output_gpkg, QgsProject.instance().transformContext(), save_opts)
            if result[0] != WRITER_NO_ERROR:
                raise RuntimeError(f"Writing {self.output_gpkg} failed: {result[1]}")

            self.layer_name = os.path.splitext(os.path.basename(self.output_gpkg))[0]

            self.setProgress(100.0)
            self.log_message.emit("--- Traffic2ENVI-met Complete ---")
            return True

        except Exception as e:
            self.exception = e
            self.log_message.emit(f"ERROR in Traffic2ENVI-met: {str(e)}")
            return False

    def finished(self, result):
        if result and self.output_gpkg:
            gpkg_layer = QgsVectorLayer(f"{self.output_gpkg}|layername=traffic_volume", f"{self.layer_name} Traffic Volume", "ogr")
            if gpkg_layer.isValid():
                QgsProject.instance().addMapLayer(gpkg_layer)
        else:
            if self.exception:
                self.log_message.emit(f"Task Failed: {self.exception}")

        if self.on_finished_callback:
            self.on_finished_callback(result, self.exception)
