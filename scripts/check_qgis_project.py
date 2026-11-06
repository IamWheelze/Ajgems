"""Open a minefoot project.qgs in real QGIS and check it (run with a Python that has QGIS bindings).

    QT_QPA_PLATFORM=offscreen python3 scripts/check_qgis_project.py path/to/project.qgs

Prints one line per problem and exits 1 if any: the project must load, have a
CRS, every local layer must be valid, every vector layer's CRS must match its
file, and a reflectance pixel identified in QGIS must equal the file's value.
"""
import os
import sys
from qgis.core import QgsApplication, QgsProject, QgsRaster, QgsVectorLayer, QgsRasterLayer


def main(path):
    app = QgsApplication([], False)
    app.initQgis()
    problems = []
    p = QgsProject.instance()
    if not p.read(path):
        problems.append("project did not load")
    if not p.crs().isValid():
        problems.append("project has no valid CRS")
    layers = list(p.mapLayers().values())
    local = [l for l in layers if l.providerType() != "wms"]  # skip the internet base map
    for l in local:
        if not l.isValid():
            problems.append(f"invalid layer: {l.name()} ({l.source()})")
        if isinstance(l, QgsVectorLayer):
            native = QgsVectorLayer(l.source(), "check", "ogr").crs().authid()
            if native != l.crs().authid():
                problems.append(f"vector CRS mismatch: {l.name()} declared {l.crs().authid()}, file {native}")
    for l in local:
        if isinstance(l, QgsRasterLayer) and l.source().endswith("reflectance.tif"):
            c = l.extent().center()
            vals = l.dataProvider().identify(c, QgsRaster.IdentifyFormatValue).results()
            ref = QgsRasterLayer(l.source(), "check", "gdal")  # keep a reference while sampling
            fresh = ref.dataProvider().sample(c, 1)[0]
            if abs(vals[1] - fresh) > 1e-6:
                problems.append(f"identify mismatch in {l.source()}")
    print(f"{len(layers)} layers, {len(local)} local, crs {p.crs().authid()}")
    for x in problems:
        print("PROBLEM:", x)
    p.clear()
    return 1 if problems else 0


if __name__ == "__main__":
    code = main(sys.argv[1])
    sys.stdout.flush()
    os._exit(code)  # QGIS can crash while shutting down; the checks are already done
