"""Acceptance tests for FRD requirements whose pass criteria had no test of their own.

Each test names its requirement and checks the FRD's acceptance criterion as written, on synthetic data.
"""
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

UTM = "EPSG:32632"          # Jos Plateau lies in UTM zone 32N
PX = 10.0                   # Sentinel-2 10 m grid


def _score_raster(path, arr, crs=UTM, transform=None):
    transform = transform or from_origin(480000, 1090000, PX, PX)
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                       dtype=arr.dtype, crs=crs, transform=transform) as d:
        d.write(arr, 1)
    return path


# ---------- FR-031: process in UTM, display in Web Mercator, export WGS 84; a round trip stays within half a pixel

def test_fr031_control_points_round_trip_within_half_a_pixel():
    from pyproj import Transformer
    rng = np.random.default_rng(0)
    x = rng.uniform(440000, 560000, 200); y = rng.uniform(1040000, 1100000, 200)   # control points over Jos
    to_merc = Transformer.from_crs(UTM, "EPSG:3857", always_xy=True)
    merc_to_wgs = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    wgs_to_utm = Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
    mx, my = to_merc.transform(x, y)                 # display
    lon, lat = merc_to_wgs.transform(mx, my)         # export
    bx, by = wgs_to_utm.transform(lon, lat)          # back to processing
    assert np.hypot(bx - x, by - y).max() < PX / 2


def test_fr031_exported_candidate_returns_to_its_pixels(tmp_path):
    """A candidate vectorised from a UTM score map and exported in WGS 84 maps back onto the same pixels."""
    from shapely.geometry import box
    from minefoot.vectorise import vectorise
    arr = np.zeros((200, 200), "uint8"); arr[40:120, 60:150] = 230
    g = vectorise(_score_raster(tmp_path / "s.tif", arr), threshold=0.5, mmu_ha=1)
    assert g.crs.to_epsg() == 4326 and len(g) == 1
    back = g.to_crs(UTM).geometry.iloc[0]
    truth = box(480000 + 60 * PX, 1090000 - 120 * PX, 480000 + 150 * PX, 1090000 - 40 * PX)
    assert back.hausdorff_distance(truth) < PX / 2


# ---------- FR-034: area on an equal-area or local UTM basis, within 0.5% of a known polygon

def test_fr034_area_of_known_polygon_within_half_a_percent(tmp_path):
    from pyproj import Geod
    from minefoot.vectorise import vectorise
    arr = np.zeros((300, 300), "uint8"); arr[50:150, 100:200] = 230      # 100 x 100 px = 100 ha
    g = vectorise(_score_raster(tmp_path / "s.tif", arr), threshold=0.5, mmu_ha=1)
    assert g.area_ha.iloc[0] == pytest.approx(100, rel=0.005)
    # The exported WGS 84 polygon gives the same area on the ellipsoid (the web app's export method).
    geod, _ = Geod(ellps="WGS84").geometry_area_perimeter(g.geometry.iloc[0])
    assert abs(geod) / 1e4 == pytest.approx(100, rel=0.005)


def test_fr034_area_from_a_geographic_score_map_uses_local_utm(tmp_path):
    """A score map in degrees must not give areas in square degrees, and the unit must be applied in hectares."""
    from pyproj import Geod
    from shapely.geometry import box
    from minefoot.vectorise import vectorise
    d = 0.0001                                                            # ~11 m at 9.8 N
    arr = np.zeros((300, 300), "uint8"); arr[50:150, 100:200] = 230
    arr[200:203, 200:203] = 230                                           # ~0.1 ha: below a 1 ha unit
    g = vectorise(_score_raster(tmp_path / "s.tif", arr, crs="EPSG:4326",
                                transform=from_origin(8.80, 9.85, d, d)), threshold=0.5, mmu_ha=1)
    assert len(g) == 1
    lon0, lat1 = 8.80 + 100 * d, 9.85 - 50 * d
    truth, _ = Geod(ellps="WGS84").geometry_area_perimeter(box(lon0, lat1 - 100 * d, lon0 + 100 * d, lat1))
    assert g.area_ha.iloc[0] == pytest.approx(abs(truth) / 1e4, rel=0.005)


# ---------- FR-078, FR-100, FR-081 on one tiny trained run

@pytest.fixture(scope="module")
def tiny_runs(tmp_path_factory):
    pytest.importorskip("torch"); pytest.importorskip("segmentation_models_pytorch")
    import geopandas as gpd
    from shapely.geometry import box
    from minefoot import g2
    from minefoot.preprocess import prepare_scene
    from minefoot.synthetic import make_scene
    tmp = tmp_path_factory.mktemp("acc")
    ids = {"train": ["s-tr1", "s-tr2"], "val": ["s-va"], "test": ["s-te"]}
    rows = []
    for i, (k, sids) in enumerate(ids.items()):
        for j, sid in enumerate(sids):
            raw = make_scene(tmp / "raw", size=128, seed=20 + 3 * i + j, scene_id=sid)
            prepare_scene(raw, tmp / "prep" / sid)
            (tmp / "labels" / sid).mkdir(parents=True)
            (tmp / "labels" / sid / "labels.geojson").write_text((raw / "truth.geojson").read_text())
            rows.append({"site_id": sid, "split": k, "mine_scale": "Artisanal", "mine_type": "Surface",
                         "dataset": "synthetic", "geometry": box(10 * len(rows), 0, 10 * len(rows) + 1, 1)})
    gpd.GeoDataFrame(rows, crs=4326).to_file(tmp / "split.gpkg", layer="sites", driver="GPKG")
    # Make the validation and test scenes much brighter, so statistics fitted on them would be obvious.
    for sid in ids["val"] + ids["test"]:
        p = tmp / "prep" / sid / "reflectance.tif"
        with rasterio.open(p, "r+") as s:
            s.write(s.read() * 3 + 0.5)
    seen = []
    real_fit = g2.fit_normalisation

    def spy(paths, **kw):
        seen.append([str(prep) for _, prep, _ in paths])
        return real_fit(paths, **kw)

    kw = dict(log=lambda *_: None, device="cpu", epochs=1, chip=64, batch=4, chips_per_site=4, workers=0,
              encoder="resnet18", encoder_weights="none")
    g2.fit_normalisation = spy
    try:
        a = g2.run_g2(ids, tmp / "split.gpkg", tmp / "prep", tmp / "labels", tmp / "a", **kw)
    finally:
        g2.fit_normalisation = real_fit
    # FR-100: repeat the run from nothing but its stored configuration.
    cfg = json.loads((tmp / "a" / "config.json").read_text())
    b = g2.run_g2(cfg["sites"], tmp / "split.gpkg", tmp / "prep", tmp / "labels", tmp / "b", seed=cfg["seed"],
                  log=lambda *_: None, device="cpu", **cfg["params"])
    return {"tmp": tmp, "ids": ids, "a": a, "b": b, "seen": seen}


def test_fr078_normalisation_fitted_on_training_sites_only(tiny_runs):
    t = tiny_runs
    assert len(t["seen"]) == 1
    used = {s.rsplit("/", 1)[-1] for s in t["seen"][0]}
    assert used == set(t["ids"]["train"])                           # code path: only training sites reach the fit
    norm = json.loads((t["tmp"] / "a" / "normalisation.json").read_text())
    with rasterio.open(t["tmp"] / "prep" / "s-va" / "reflectance.tif") as s:
        val_mean = np.nanmean(s.read(1))
    assert norm["mean"][0] < val_mean - 0.4                         # values: the bright held-out scenes are not in it
    assert norm["source"].startswith(f"{len(t['ids']['train'])} training sites")
    assert (t["tmp"] / "a" / "normalisation.json").exists()          # stored with the model


def test_fr100_run_repeats_from_its_stored_configuration(tiny_runs):
    import torch
    t = tiny_runs
    ca = json.loads((t["tmp"] / "a" / "config.json").read_text())
    cb = json.loads((t["tmp"] / "b" / "config.json").read_text())
    assert ca["params"] == cb["params"] and ca["sites"] == cb["sites"] and ca["seed"] == cb["seed"]
    ha = (t["tmp"] / "a" / "history.csv").read_text().splitlines()
    hb = (t["tmp"] / "b" / "history.csv").read_text().splitlines()
    loss = lambda rows: [tuple(r.split(",")[1:3]) for r in rows[1:]]
    assert loss(ha) == loss(hb)
    wa = torch.load(t["tmp"] / "a" / "unet_best.pt"); wb = torch.load(t["tmp"] / "b" / "unet_best.pt")
    assert all(torch.equal(wa[k], wb[k]) for k in wa)
    ma, mb = (t[k]["models"]["unet"]["test"]["strata"]["all"] for k in ("a", "b"))
    assert ma == mb


def test_fr081_per_pixel_scores_reproducible_on_reference_area(tiny_runs):
    from minefoot.g2 import load_run_model, predict_tile
    t = tiny_runs
    with rasterio.open(t["tmp"] / "prep" / "s-te" / "reflectance.tif") as s:
        refl = s.read()
    runs = []
    for _ in range(2):   # load the approved run afresh each time, as a detection job does
        model, norm, _, _, dev = load_run_model(t["tmp"] / "a", device="cpu")
        runs.append(predict_tile(model, refl, norm, dev, chip=64, overlap=16, batch=4))
    assert runs[0].shape == refl.shape[1:]
    assert np.abs(runs[0] - runs[1]).max() <= 1e-6                 # tolerance: identical to 1e-6
    model_b, norm_b, _, _, dev = load_run_model(t["tmp"] / "b", device="cpu")
    assert np.abs(predict_tile(model_b, refl, norm_b, dev, chip=64, overlap=16, batch=4) - runs[0]).max() <= 1e-6


# ---------- traceability: every MVP requirement in the FRD is referenced by code or a test

def test_every_mvp_requirement_is_traced():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    frd = (root / "docs" / "FRD.md").read_text()
    mvp = set(re.findall(r"^\| (FR-\d{3}) \|[^\n]*\| MVP \|", frd, flags=re.M))
    assert len(mvp) > 50
    text = "".join(p.read_text(errors="ignore") for d in ("src", "tests", "web/backend", "web/frontend/src")
                   for p in (root / d).rglob("*") if p.suffix in (".py", ".ts", ".tsx") and "node_modules" not in p.parts)
    missing = sorted(r for r in mvp if r not in text)
    assert not missing, f"MVP requirements with no code or test reference: {missing}"
