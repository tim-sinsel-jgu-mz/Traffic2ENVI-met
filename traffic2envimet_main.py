import os
import json
import datetime
import processing
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QMessageBox, QProgressBar, QDialogButtonBox
from qgis.core import (
    QgsProject, QgsFeature, QgsGeometry, QgsSpatialIndex, QgsField, 
    QgsVectorLayer, QgsVectorFileWriter, QgsTask, QgsApplication, 
    QgsProcessingContext, QgsMapLayerProxyModel, Qgis
)
from qgis.gui import QgsFileWidget
from PyQt5.QtCore import QVariant, QMetaType

# Load the UI file
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'Traffic2ENVI-met.ui'))

class TrafficEnviTask(QgsTask):
    """Background task to calculate traffic trajectories and emissions."""
    
    def __init__(self, description, params, on_finished_callback):
        super().__init__(description, QgsTask.CanCancel)
        self.params = params
        self.on_finished_callback = on_finished_callback
        self.exception = None
        self.output_gpkg = None
        self.layer_name = None

    def run(self):
        try:
            print("--- Traffic2ENVI-met Started ---")
            osm_source = self.params['osm_source']
            traj_source = self.params['traj_source']
            crs_str = self.params['crs_str']
            datetime_field = self.params['datetime_field']
            unique_id_field = self.params['unique_id_field']
            search_radius = self.params['search_radius']
            split_length = self.params['split_length']
            similarity_tolerance = self.params['similarity_tolerance']
            scaling_factor = self.params['scaling_factor']
            ef_nox = self.params['ef_nox']
            ef_pm10 = self.params['ef_pm10']
            v_ratio_no = self.params['v_ratio_no']
            v_ratio_pm = self.params['v_ratio_pm']
            
            self.output_gpkg = self.params['output_file']
            output_dir = os.path.dirname(self.output_gpkg)
            output_edb_path = os.path.join(output_dir, 'projectdatabase.edb')

            osm_layer = QgsVectorLayer(osm_source, "OSM", "ogr")
            traj_layer = QgsVectorLayer(traj_source, "Traj", "ogr")

            self.setProgress(5.0)

            # --- STEP 1 ---
            print("Step 1/7: Filtering and splitting OSM lines...")
            context = QgsProcessingContext()
            expression = "\"fclass\" IN ('primary', 'primary_link', 'residential', 'secondary', 'secondary_link')"
            filtered_osm = processing.run("native:extractbyexpression", {
                'INPUT': osm_layer, 'EXPRESSION': expression, 'OUTPUT': 'memory:'
            }, context=context)['OUTPUT']

            self.setProgress(10.0)

            split_osm = processing.run("native:splitlinesbylength", {
                'INPUT': filtered_osm, 'LENGTH': split_length, 'OUTPUT': 'memory:'
            }, context=context)['OUTPUT']

            if self.isCanceled(): return False
            self.setProgress(15.0)

            # --- STEP 2 ---
            print("Step 2/7: Preparing memory layer and spatial index...")
            memory_layer = QgsVectorLayer(f"LineString?crs={crs_str}", "Temp_Counts", "memory")
            provider = memory_layer.dataProvider()
            provider.addAttributes([QgsField("tempID", QMetaType.Type.Int)])
            for h in range(24): provider.addAttributes([QgsField(f"h_{h:02d}", QMetaType.Type.Int)])
            memory_layer.updateFields()

            new_features = []
            for i, feat in enumerate(split_osm.getFeatures()):
                new_feat = QgsFeature(memory_layer.fields())
                new_feat.setGeometry(feat.geometry())
                new_feat.setAttribute("tempID", i)
                for h in range(24): new_feat.setAttribute(f"h_{h:02d}", 0)
                new_features.append(new_feat)
            provider.addFeatures(new_features)

            self.setProgress(20.0)

            traj_index = QgsSpatialIndex(traj_layer.getFeatures())
            traj_data = {}
            for feat in traj_layer.getFeatures():
                start_hour = int(feat[datetime_field] // 3600)
                if 0 <= start_hour <= 23:
                    traj_data[feat.id()] = {'geom': feat.geometry(), 'hour': start_hour, 'group_id': feat[unique_id_field]}

            if self.isCanceled(): return False
            self.setProgress(30.0)

            # --- STEP 3 ---
            print("Step 3/7: Counting unique trajectories per segment...")
            update_map = {}
            h_indices = {h: memory_layer.fields().indexOf(f"h_{h:02d}") for h in range(24)}

            total_segs = memory_layer.featureCount()
            for idx, seg_feat in enumerate(memory_layer.getFeatures()):
                if self.isCanceled(): return False
                
                # Update progress smoothly, forcing float for QgsTask
                if idx % 100 == 0 and total_segs > 0:
                    self.setProgress(float(30 + (idx / total_segs) * 30))

                seg_geom = seg_feat.geometry()
                search_rect = seg_geom.boundingBox()
                search_rect.grow(search_radius)
                candidate_ids = traj_index.intersects(search_rect)
                
                unique_counts = {h: set() for h in range(24)}
                for c_id in candidate_ids:
                    if c_id not in traj_data: continue
                    if seg_geom.distance(traj_data[c_id]['geom']) <= search_radius:
                        unique_counts[traj_data[c_id]['hour']].add(traj_data[c_id]['group_id'])
                        
                attr_update = {}
                for h in range(24): attr_update[h_indices[h]] = len(unique_counts[h])
                update_map[seg_feat.id()] = attr_update

            provider.changeAttributeValues(update_map)

            if self.isCanceled(): return False
            self.setProgress(60.0)

            # --- STEP 4 ---
            print("Step 4/7: Merging similar adjacent segments...")
            mem_index = QgsSpatialIndex(memory_layer.getFeatures())
            mem_features = {f.id(): f for f in memory_layer.getFeatures()}
            visited, groups = set(), []

            total_mem = len(mem_features)
            processed = 0

            for f_id, f in mem_features.items():
                if self.isCanceled(): return False
                
                processed += 1
                if processed % 100 == 0 and total_mem > 0:
                    self.setProgress(float(60 + (processed / total_mem) * 20))

                if f_id in visited: continue
                current_group, queue = [f_id], [f_id]
                visited.add(f_id)
                
                while queue:
                    curr_id = queue.pop(0)
                    curr_feat = mem_features[curr_id]
                    bbox = curr_feat.geometry().boundingBox()
                    bbox.grow(0.01) 
                    candidates = mem_index.intersects(bbox)
                    
                    for cand_id in candidates:
                        if cand_id in visited: continue
                        cand_feat = mem_features[cand_id]
                        if curr_feat.geometry().distance(cand_feat.geometry()) < 0.01:
                            seed_feat = mem_features[current_group[0]]
                            is_similar = True
                            for h in range(24):
                                if abs(seed_feat[f"h_{h:02d}"] - cand_feat[f"h_{h:02d}"]) > similarity_tolerance:
                                    is_similar = False
                                    break
                            if is_similar:
                                visited.add(cand_id)
                                queue.append(cand_id)
                                current_group.append(cand_id)
                groups.append(current_group)

            self.setProgress(80.0)

            # --- STEP 5 ---
            print("Step 5/7: Applying scaling factor...")
            final_layer = QgsVectorLayer(f"MultiLineString?crs={crs_str}", "Final_Merged_Counts", "memory")
            final_prov = final_layer.dataProvider()
            final_prov.addAttributes([QgsField("enviID", QMetaType.Type.String, len=6)])
            for h in range(24): final_prov.addAttributes([QgsField(f"h_{h:02d}", QMetaType.Type.Int)])
            final_layer.updateFields()

            final_feats = []
            envi_id_counter = 1
            for grp in groups:
                geoms = [mem_features[fid].geometry() for fid in grp]
                merged_geom = QgsGeometry.unaryUnion(geoms)
                new_feat = QgsFeature(final_layer.fields())
                new_feat.setGeometry(merged_geom)
                new_feat.setAttribute("enviID", f"{envi_id_counter:06d}")
                for h in range(24):
                    avg_raw_count = sum([mem_features[fid][f"h_{h:02d}"] for fid in grp]) / len(grp)
                    new_feat.setAttribute(f"h_{h:02d}", round(avg_raw_count * scaling_factor))
                final_feats.append(new_feat)
                envi_id_counter += 1

            final_prov.addFeatures(final_feats)

            if self.isCanceled(): return False
            self.setProgress(90.0)

            # --- STEP 6 ---
            print("Step 6/7: Generating ENVI-met JSON database...")
            ef_no = ef_nox * (1 - v_ratio_no)
            ef_no2 = ef_nox * v_ratio_no
            ef_pm25 = ef_pm10 * v_ratio_pm

            json_db = {
                "envimetDatafile": {
                    "header": {
                        "fileType": "databaseJSON",
                        "version": 1,
                        "revisionDate": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "remark": "Auto-generated by QGIS Trajectory Script",
                        "description": "Traffic Emission Line Sources"
                    },
                    "emitters": []
                }
            }

            for feat in final_layer.getFeatures():
                envi_id = feat["enviID"]
                em_usr, em_no, em_no2, em_o3, em_pm10, em_pm25 = [], [], [], [], [], []
                for h in range(24):
                    q = feat[f"h_{h:02d}"]
                    em_usr.append(0.0)
                    em_o3.append(0.0)
                    em_no.append(float((q * ef_no) / 3.6))
                    em_no2.append(float((q * ef_no2) / 3.6))
                    em_pm10.append(float((q * ef_pm10) / 3.6))
                    em_pm25.append(float((q * ef_pm25) / 3.6))

                json_db["envimetDatafile"]["emitters"].append({
                    "id": envi_id, "desc": f"Traffic Line {envi_id}", "col": "81E908",
                    "grp": "Emitters", "height": 0.5, "geom": "line",
                    "emissionUsr": em_usr, "emissionNO": em_no, "emissionNO2": em_no2,
                    "emissionO3": em_o3, "emissionPM10": em_pm10, "emissionPM25": em_pm25,
                    "cost": 0, "remark": "Generated Line Source"
                })

            with open(output_edb_path, 'w', encoding='utf-8') as f:
                json.dump(json_db, f, indent=4)

            self.setProgress(95.0)

            # --- STEP 7 ---
            print("Step 7/7: Saving output vectors to GeoPackage...")
            save_opts = QgsVectorFileWriter.SaveVectorOptions()
            save_opts.driverName = "GPKG"
            save_opts.layerName = "segment_counts"
            QgsVectorFileWriter.writeAsVectorFormatV3(final_layer, self.output_gpkg, QgsProject.instance().transformContext(), save_opts)
            
            self.layer_name = os.path.splitext(os.path.basename(self.output_gpkg))[0]
            
            self.setProgress(100.0)
            print("--- Traffic2ENVI-met Complete ---")
            return True

        except Exception as e:
            self.exception = e
            print(f"ERROR in Traffic2ENVI-met: {str(e)}")
            return False

    def finished(self, result):
        if result and self.output_gpkg:
            gpkg_layer = QgsVectorLayer(f"{self.output_gpkg}|layername=segment_counts", f"{self.layer_name} Merged Counts", "ogr")
            if gpkg_layer.isValid():
                QgsProject.instance().addMapLayer(gpkg_layer)
            QgsApplication.taskManager().window().statusBar().showMessage("Traffic processing finished successfully.")
        else:
            if self.exception:
                QgsApplication.taskManager().window().statusBar().showMessage("Traffic processing failed. Check Python Console.")
        
        if self.on_finished_callback:
            self.on_finished_callback(result, self.exception)


class Traffic2ENVIMetDialog(QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super(Traffic2ENVIMetDialog, self).__init__(parent)
        self.setupUi(self)
        self.active_task = None
        
        self.progressBar = QProgressBar()
        self.progressBar.setValue(0)
        self.verticalLayout.addWidget(self.progressBar)
        
        self.start_button = self.buttonBox.button(QDialogButtonBox.Ok)
        self.start_button.setText("Start")

        try:
            self.buttonBox.accepted.disconnect()
            self.buttonBox.rejected.disconnect()
        except TypeError:
            pass 

        self.buttonBox.accepted.connect(self.run_process)
        self.buttonBox.rejected.connect(self.cancel_or_close)

        # Set UI Filters and Defaults with Boundaries
        self.mMapLayerComboBox_Streets.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.mMapLayerComboBox_TrafficTrajectories.setFilters(QgsMapLayerProxyModel.LineLayer)

        self.mQgsDoubleSpinBox_SearchRadius.setValue(5.0)
        self.mQgsDoubleSpinBox_StreetSegmentSize.setValue(2.0)
        self.mQgsDoubleSpinBox_SimilarityTolerance.setValue(3.0)
        self.mQgsDoubleSpinBox_ScalingFactor.setValue(5.0)
        self.mQgsDoubleSpinBox_EmFacNOx.setValue(0.180)
        self.mQgsDoubleSpinBox_EmFacPM10.setValue(0.020)
        self.mQgsDoubleSpinBox_NORatio.setValue(0.5)
        self.mQgsDoubleSpinBox_PMRatio.setValue(0.5)


        self.mQgsDoubleSpinBox_SearchRadius.setRange(0.1, 100.0)
        self.mQgsDoubleSpinBox_SearchRadius.setValue(5.0)

        self.mQgsDoubleSpinBox_StreetSegmentSize.setRange(0.5, 50.0)
        self.mQgsDoubleSpinBox_StreetSegmentSize.setValue(2.0)

        self.mQgsDoubleSpinBox_SimilarityTolerance.setRange(0.0, 50.0)
        self.mQgsDoubleSpinBox_SimilarityTolerance.setValue(3.0)

        self.mQgsDoubleSpinBox_ScalingFactor.setRange(0.1, 1000.0)
        self.mQgsDoubleSpinBox_ScalingFactor.setValue(5.0)

        self.mQgsDoubleSpinBox_EmFacNOx.setRange(0.0, 100.0)
        self.mQgsDoubleSpinBox_EmFacNOx.setValue(0.180)

        self.mQgsDoubleSpinBox_EmFacPM10.setRange(0.0, 100.0)
        self.mQgsDoubleSpinBox_EmFacPM10.setValue(0.020)

        self.mQgsDoubleSpinBox_NORatio.setRange(0.0, 1.0)
        self.mQgsDoubleSpinBox_NORatio.setSingleStep(0.05) 
        self.mQgsDoubleSpinBox_NORatio.setValue(0.5)

        self.mQgsDoubleSpinBox_PMRatio.setRange(0.0, 1.0)
        self.mQgsDoubleSpinBox_PMRatio.setSingleStep(0.05)
        self.mQgsDoubleSpinBox_PMRatio.setValue(0.5)       


        self.mQgsDoubleSpinBox_SearchRadius.setToolTip("Distance in meters to search for trajectories around each street segment.")
        self.mQgsDoubleSpinBox_StreetSegmentSize.setToolTip("Length in meters to split the street lines for higher resolution spatial mapping.")
        self.mQgsDoubleSpinBox_SimilarityTolerance.setToolTip("Maximum allowed difference in hourly vehicle counts to merge adjacent street segments together.")
        self.mQgsDoubleSpinBox_ScalingFactor.setToolTip(
            "Multiplier to scale your sample trajectory counts up to real-world total traffic volumes. Default value of 5 hence would indicate that the trajectory dataset only contained 20 %% of real daily traffic volume."
            )
        self.mQgsDoubleSpinBox_EmFacNOx.setToolTip("Base emission factor for Nitrogen Oxides (NOx) in grams per kilometer (g/km).")
        self.mQgsDoubleSpinBox_EmFacPM10.setToolTip("Base emission factor for PM10 (including non-exhaust wear) in grams per kilometer (g/km).")
        self.mQgsDoubleSpinBox_NORatio.setToolTip("Fraction (0.0 to 1.0) of NOx that is emitted specifically as NO2.")
        self.mQgsDoubleSpinBox_PMRatio.setToolTip("Fraction (0.0 to 1.0) of PM10 that consists of PM2.5.")         

        self.mQgsFileWidget_OutputFile.setFilter("GeoPackage (*.gpkg)")
        self.mQgsFileWidget_OutputFile.setStorageMode(QgsFileWidget.SaveFile)
        self.mQgsFileWidget_OutputFile.setDialogTitle("Save Output GeoPackage")

        self.mMapLayerComboBox_TrafficTrajectories.layerChanged.connect(self.update_smart_fields)
        
        current_traj = self.mMapLayerComboBox_TrafficTrajectories.currentLayer()
        if current_traj:
            self.update_smart_fields(current_traj)

    def update_smart_fields(self, layer):
        if not layer:
            return
            
        self.mFieldComboBox_DateTime.setLayer(layer)
        self.mFieldComboBox_TripID.setLayer(layer)

        fields = [field.name() for field in layer.fields()]
        
        for f in fields:
            f_lower = f.lower()
            if any(keyword in f_lower for keyword in ['time', 'date', 'start']):
                self.mFieldComboBox_DateTime.setField(f)
                break
                
        for f in fields:
            f_lower = f.lower()
            if any(keyword in f_lower for keyword in ['id', 'trip', 'ident']):
                self.mFieldComboBox_TripID.setField(f)
                break

    def cancel_or_close(self):
        if self.active_task and self.active_task.isActive():
            self.active_task.cancel()
            self.progressBar.setValue(0)
            self.start_button.setEnabled(True)
            self.active_task = None
            print("--- Traffic2ENVI-met Cancelled by User ---")
            QMessageBox.information(self, "Cancelled", "Traffic processing was cancelled.")
        else:
            self.reject()

    def on_task_finished(self, result, exception):
        self.start_button.setEnabled(True)
        self.active_task = None
        if exception:
            QMessageBox.critical(self, "Error", f"An error occurred:\n{exception}")
        elif result:
            QMessageBox.information(self, "Success", "Processing complete! Outputs have been saved.")

    def run_process(self):
        osm_layer = self.mMapLayerComboBox_Streets.currentLayer()
        traj_layer = self.mMapLayerComboBox_TrafficTrajectories.currentLayer()
        output_file = self.mQgsFileWidget_OutputFile.filePath()
        
        if not osm_layer or not traj_layer:
            QMessageBox.warning(self, "Missing Inputs", "Please ensure both street and trajectory layers are selected.")
            return
            
        if not output_file:
            QMessageBox.warning(self, "Missing Output", "Please specify an output GeoPackage file.")
            return

        if not output_file.lower().endswith('.gpkg'):
            output_file += '.gpkg'

        params = {
            'osm_source': osm_layer.source(),
            'traj_source': traj_layer.source(),
            'crs_str': osm_layer.crs().toWkt(),
            'datetime_field': self.mFieldComboBox_DateTime.currentField(),
            'unique_id_field': self.mFieldComboBox_TripID.currentField(),
            'search_radius': self.mQgsDoubleSpinBox_SearchRadius.value(),
            'split_length': self.mQgsDoubleSpinBox_StreetSegmentSize.value(),
            'similarity_tolerance': self.mQgsDoubleSpinBox_SimilarityTolerance.value(),
            'scaling_factor': self.mQgsDoubleSpinBox_ScalingFactor.value(),
            'ef_nox': self.mQgsDoubleSpinBox_EmFacNOx.value(),
            'ef_pm10': self.mQgsDoubleSpinBox_EmFacPM10.value(),
            'v_ratio_no': self.mQgsDoubleSpinBox_NORatio.value(),
            'v_ratio_pm': self.mQgsDoubleSpinBox_PMRatio.value(),
            'output_file': output_file
        }

        self.start_button.setEnabled(False)
        self.progressBar.setValue(0)

        self.active_task = TrafficEnviTask("Calculating Traffic Trajectories & Emissions", params, self.on_task_finished)
        
        # Intercept the signal and force the float into an int before the progress bar gets it
        self.active_task.progressChanged.connect(lambda val: self.progressBar.setValue(int(val)))
        
        QgsApplication.taskManager().addTask(self.active_task)