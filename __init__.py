def classFactory(iface):
    # Importing main plugin class and passing the QGIS interface (iface) to it
    from .main_plugin import Traffic2ENVIPlugin
    return Traffic2ENVIPlugin(iface)