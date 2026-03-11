def classFactory(iface):
    # This imports your main plugin class and passes the QGIS interface (iface) to it
    from .main_plugin import Traffic2ENVIPlugin
    return Traffic2ENVIPlugin(iface)