import os
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon

# Importing dialog class
from .traffic2envimet_dialog import Traffic2ENVIMetDialog

class Traffic2ENVIPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def initGui(self):
        """Called when QGIS loads the plugin. Sets up the UI."""
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        self.action = QAction(QIcon(icon_path), "Traffic Data to ENVI-met", self.iface.mainWindow())
        self.action.triggered.connect(self.run)

        # 1. Add toolbar button
        self.iface.addToolBarIcon(self.action)

        # 2. Add plugin to the Vector Menu
        self.iface.addPluginToVectorMenu("Traffic Data to ENVI-met", self.action)

    def unload(self):
        """Called when QGIS is closed or the plugin is disabled."""
        # Remove toolbar button
        self.iface.removeToolBarIcon(self.action)

        # Remove plugin from the Vector Menu
        self.iface.removePluginVectorMenu("Traffic Data to ENVI-met", self.action)

    def run(self):
        """Called when the user clicks the plugin button."""
        # Instantiate the dialog if it hasn't been created yet
        if not self.dialog:
            self.dialog = Traffic2ENVIMetDialog(self.iface.mainWindow())
        
        # Show the dialog to the user
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()