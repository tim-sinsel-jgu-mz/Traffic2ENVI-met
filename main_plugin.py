import os
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon

# This imports your class from the other file!
from .traffic2envimet_main import Traffic2ENVIMetDialog

class Traffic2ENVIPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def initGui(self):
        """Called when QGIS loads the plugin. Sets up the UI."""
        self.action = QAction("Traffic to ENVI-met", self.iface.mainWindow())
        self.action.triggered.connect(self.run)

        # Add toolbar button and menu item in QGIS
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Traffic to ENVI-met", self.action)

    def unload(self):
        """Called when QGIS is closed or the plugin is disabled."""
        self.iface.removePluginMenu("&Traffic to ENVI-met", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        """Called when the user clicks the plugin button."""
        # Instantiate the dialog if it hasn't been created yet
        if not self.dialog:
            self.dialog = Traffic2ENVIMetDialog(self.iface.mainWindow())
        
        # Show the dialog to the user
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()