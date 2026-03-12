import os
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QMessageBox, QDialogButtonBox
from qgis.core import QgsApplication, QgsMapLayerProxyModel, QgsProject
from qgis.gui import QgsFileWidget

from .traffic2envimet_logic import TrafficEnviTask

# --- Universal Enums (QGIS 3 / PyQt5 & QGIS 4 / PyQt6 Compatibility) ---
try:
    BTN_OK = QDialogButtonBox.StandardButton.Ok
    BTN_CANCEL = QDialogButtonBox.StandardButton.Cancel
    BTN_CLOSE = QDialogButtonBox.StandardButton.Close
    FILTER_LINE = QgsMapLayerProxyModel.Filter.LineLayer
    STORAGE_SAVE = QgsFileWidget.StorageMode.SaveFile
except AttributeError:
    BTN_OK = QDialogButtonBox.Ok
    BTN_CANCEL = QDialogButtonBox.Cancel
    BTN_CLOSE = QDialogButtonBox.Close
    FILTER_LINE = QgsMapLayerProxyModel.LineLayer
    STORAGE_SAVE = QgsFileWidget.SaveFile

# Load the UI file
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'Traffic2ENVI-met.ui'))

class Traffic2ENVIMetDialog(QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super(Traffic2ENVIMetDialog, self).__init__(parent)
        self.setupUi(self)
        self.active_task = None
        
        self.progressBar_2.setValue(0)
        
        # Map the custom execute button
        self.start_button = self.pushButton_Execute_2
        self.start_button.setText("Execute")
        
        # Grab references to standard buttons in UI
        self.cancel_button = self.buttonBox.button(BTN_CANCEL)
        self.close_button = self.buttonBox.button(BTN_CLOSE)

        # Set initial states (Cancel is only active during processing)
        self.cancel_button.setEnabled(False)

        # Disconnect standard Qt behavior so Cancel doesn't instantly close the window
        try:
            self.buttonBox.accepted.disconnect()
            self.buttonBox.rejected.disconnect()
        except TypeError:
            pass 

        # Connect buttons to their respective functions
        self.start_button.clicked.connect(self.run_process)
        self.cancel_button.clicked.connect(self.cancel_task)
        self.close_button.clicked.connect(self.close_dialog)
        
        # Filters and Defaults
        self.mMapLayerComboBox_Streets.setFilters(FILTER_LINE)
        self.mMapLayerComboBox_TrafficTrajectories.setFilters(FILTER_LINE)

        # Boundaries & Defaults Setup
        self.mQgsDoubleSpinBox_SearchRadius.setRange(0.1, 100.0)
        self.mQgsDoubleSpinBox_SearchRadius.setValue(5.0)
        self.mQgsDoubleSpinBox_StreetSegmentSize.setRange(0.5, 1000.0)
        self.mQgsDoubleSpinBox_StreetSegmentSize.setValue(2.0)
        self.mQgsDoubleSpinBox_SimilarityTolerance.setRange(0.0, 1000.0)
        self.mQgsDoubleSpinBox_SimilarityTolerance.setValue(3.0)
        self.mQgsDoubleSpinBox_ScalingFactor.setRange(0.1, 10000.0)
        self.mQgsDoubleSpinBox_ScalingFactor.setValue(5.0)
        self.mQgsDoubleSpinBox_EmFacNOx.setRange(0.0, 100.0)
        self.mQgsDoubleSpinBox_EmFacNOx.setValue(0.180)
        self.mQgsDoubleSpinBox_EmFacNOx.setSingleStep(0.010)
        self.mQgsDoubleSpinBox_EmFacPM10.setRange(0.0, 100.0)
        self.mQgsDoubleSpinBox_EmFacPM10.setValue(0.020)
        self.mQgsDoubleSpinBox_EmFacPM10.setSingleStep(0.010)
        self.mQgsDoubleSpinBox_NORatio.setRange(0.0, 1.0)
        self.mQgsDoubleSpinBox_NORatio.setSingleStep(0.05) 
        self.mQgsDoubleSpinBox_NORatio.setValue(0.5)
        self.mQgsDoubleSpinBox_PMRatio.setRange(0.0, 1.0)
        self.mQgsDoubleSpinBox_PMRatio.setSingleStep(0.05)
        self.mQgsDoubleSpinBox_PMRatio.setValue(0.5)       

        # Tooltips
        self.mQgsDoubleSpinBox_SearchRadius.setToolTip("Distance in meters to search for trajectories around each street segment.")
        self.mQgsDoubleSpinBox_StreetSegmentSize.setToolTip("Length in meters to split the street lines for higher resolution spatial mapping.")
        self.mQgsDoubleSpinBox_SimilarityTolerance.setToolTip("Maximum allowed difference in hourly vehicle counts to merge adjacent street segments together.")
        self.mQgsDoubleSpinBox_ScalingFactor.setToolTip("Multiplier to scale your sample trajectory counts up to real-world total traffic volumes.")
        self.mQgsDoubleSpinBox_EmFacNOx.setToolTip("Base emission factor for Nitrogen Oxides (NOx) in grams per kilometer (g/km).")
        self.mQgsDoubleSpinBox_EmFacPM10.setToolTip("Base emission factor for PM10 (including non-exhaust wear) in grams per kilometer (g/km).")
        self.mQgsDoubleSpinBox_NORatio.setToolTip("Fraction (0.0 to 1.0) of NOx that is emitted specifically as NO2.")
        self.mQgsDoubleSpinBox_PMRatio.setToolTip("Fraction (0.0 to 1.0) of PM10 that consists of PM2.5.")         

        self.mQgsFileWidget_OutputFile.setFilter("GeoPackage (*.gpkg)")
        self.mQgsFileWidget_OutputFile.setStorageMode(STORAGE_SAVE)
        self.mQgsFileWidget_OutputFile.setDialogTitle("Save Output GeoPackage")

        # Smart Connections
        self.mMapLayerComboBox_TrafficTrajectories.layerChanged.connect(self.update_smart_fields)
        
        # Initial Auto-Selection Routines
        self.auto_select_layers()
        
        current_traj = self.mMapLayerComboBox_TrafficTrajectories.currentLayer()
        if current_traj:
            self.update_smart_fields(current_traj)

    def auto_select_layers(self):
        """Guesses the correct layer for streets and traffic based on layer names."""
        for layer in QgsProject.instance().mapLayers().values():
            # Ensure it's a vector layer 
            if layer.type() == 0:  
                name_lower = layer.name().lower()
                if 'street' in name_lower:
                    self.mMapLayerComboBox_Streets.setLayer(layer)
                elif any(kw in name_lower for kw in ['traffic', 'trajectory', 'traj']):
                    self.mMapLayerComboBox_TrafficTrajectories.setLayer(layer)

    def update_smart_fields(self, layer):
        if not layer:
            return
            
        self.mFieldComboBox_DateTime.setLayer(layer)
        self.mFieldComboBox_TripID.setLayer(layer)

        fields = [field.name() for field in layer.fields()]
        
        # 1. Datetime guesser
        for f in fields:
            if any(keyword in f.lower() for keyword in ['time', 'date', 'start']):
                self.mFieldComboBox_DateTime.setField(f)
                break
                
        # 2. Priority-based Trip ID guesser
        id_priorities = ['group_id', 'groupid', 'trip_id', 'tripid', 'id', 'trip', 'ident', 'fid']
        matched = False
        
        for priority_kw in id_priorities:
            for f in fields:
                if f.lower() == priority_kw:
                    self.mFieldComboBox_TripID.setField(f)
                    matched = True
                    break
            if matched:
                break
        
        # Fallback if no exact match is found, but the field contains 'id'
        if not matched:
            for f in fields:
                if 'id' in f.lower():
                    self.mFieldComboBox_TripID.setField(f)
                    break

    def toggle_ui_state(self, is_running):
        self.start_button.setEnabled(not is_running)
        self.close_button.setEnabled(not is_running)
        self.cancel_button.setEnabled(is_running)

    def append_log(self, text):
        self.textEdit_ProtocolLog.append(text)      

    def cancel_task(self):
        if self.active_task and self.active_task.isActive():
            self.active_task.cancel()
            self.progressBar_2.setValue(0)
            self.toggle_ui_state(is_running=False)
            self.active_task = None
            self.append_log("--- Traffic2ENVI-met Cancelled by User ---")
            QMessageBox.information(self, "Cancelled", "Traffic processing was cancelled.")

    def close_dialog(self):
        if self.active_task and self.active_task.isActive():
            self.active_task.cancel()
        self.reject()

    def on_task_finished(self, result, exception):
        self.toggle_ui_state(is_running=False)
        self.progressBar_2.setValue(0)
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

        self.toggle_ui_state(is_running=True)
        self.progressBar_2.setValue(0)

        self.tabWidget.setCurrentIndex(1)
        self.textEdit_ProtocolLog.clear()

        self.active_task = TrafficEnviTask("Calculating Traffic Trajectories & Emissions", params, self.on_task_finished)
        
        self.active_task.progressChanged.connect(lambda val: self.progressBar_2.setValue(int(val)))
        self.active_task.log_message.connect(self.append_log)
        
        QgsApplication.taskManager().addTask(self.active_task)