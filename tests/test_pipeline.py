import json, numpy as np, pytest, rasterio
from minefoot.preprocess import iter_chips, prepare_scene, to_model_array
from minefoot.quality import Outcome, coverage, tile_outcome, usable_mask
from minefoot.reflectance import MissingRadiometry, Radiometry, resolve, to_reflectance
from minefoot.schema import BANDS, RasterFacts, SchemaError, validate_raw, validate_reflectance
from minefoot.synthetic import make_scene
from minefoot.viewer import build_gallery, outline

R4 = Radiometry(-1000, 10000.0, "t", "05.10")

@pytest.mark.parametrize("props,asset,offset,rule", [
    ({"s2:processing_baseline": "05.10"}, None, -1000, "processing_baseline"),
    ({"s2:processing_baseline": "04.00"}, None, -1000, "processing_baseline"),
    ({"s2:processing_baseline": "03.01"}, None, 0, "processing_baseline"),
    ({"s2:processing_baseline": "05.10", "earthsearch:boa_offset_applied": True}, None, 0, "provider_offset_already_applied"),
    ({"s2:processing_baseline": "03.01"}, {"raster:bands": [{"scale": 0.0001, "offset": -0.1}]}, -1000, "asset_raster_bands"),
])
def test_offset_rules(props, asset, offset, rule):
    r = resolve(props, asset); assert (r.offset_dn, r.rule) == (offset, rule)

def test_old_date_reprocessed_uses_baseline_not_date():
    assert resolve({"datetime": "2018-01-01T00:00:00Z", "s2:processing_baseline": "05.00"}).offset_dn == -1000

def test_missing_radiometry_refused():
    with pytest.raises(MissingRadiometry): resolve({})

def test_reflectance_values_and_nodata():
    out = to_reflectance(np.array([[0, 1000, 3000]], "uint16"), R4)
    assert np.isnan(out[0, 0]) and out[0, 1] == 0 and abs(out[0, 2] - .2) < 1e-6

def test_baselines_agree(tmp_path):
    a = make_scene(tmp_path, baseline="05.10", offset=-1000); b = make_scene(tmp_path, baseline="03.01", offset=0)
    prepare_scene(a, tmp_path / "pa"); prepare_scene(b, tmp_path / "pb")
    with rasterio.open(tmp_path / "pa/reflectance.tif") as s: x = s.read()
    with rasterio.open(tmp_path / "pb/reflectance.tif") as s: y = s.read()
    assert np.nanmax(np.abs(x - y)) < 1e-5

def test_preprocessing_is_deterministic(tmp_path):
    a = make_scene(tmp_path, seed=1)
    prepare_scene(a, tmp_path / "1"); prepare_scene(a, tmp_path / "2")
    assert (tmp_path / "1/reflectance.tif").read_bytes() == (tmp_path / "2/reflectance.tif").read_bytes()

def test_unapplied_scale_rejected():
    with pytest.raises(SchemaError): validate_reflectance(np.full((10, 4, 4), 3000.0))

@pytest.mark.parametrize("change,word", [
    (dict(band_names=("B02",)), "missing"), (dict(processing_level="L1C"), "L2A"), (dict(has_crs=False), "CRS"),
    (dict(crs_is_projected=False), "projected"), (dict(pixel_size=20.0), "pixel"), (dict(dtype="float32"), "integer")])
def test_schema_named_reasons(change, word):
    base = dict(band_names=(*BANDS, "SCL"), dtype="uint16", pixel_size=10.0, crs_is_projected=True, has_crs=True, processing_level="L2A")
    validate_raw(RasterFacts(**base))
    with pytest.raises(SchemaError, match=word): validate_raw(RasterFacts(**{**base, **change}))

def test_cloud_tile_never_says_no_candidate():
    assert tile_outcome(np.full((50, 50), 9, "uint8"), 0) is Outcome.INSUFFICIENT
    assert tile_outcome(np.full((50, 50), 4, "uint8"), 0) is Outcome.NO_CANDIDATE
    assert tile_outcome(np.full((50, 50), 4, "uint8"), 3) is Outcome.CANDIDATE_FOUND
    assert tile_outcome(None, None) is Outcome.NOT_PROCESSED

def test_coverage_counts_and_snow_kept():
    scl = np.array([[4, 4, 9, 11]], "uint8")
    c = coverage(scl); assert c.usable_fraction == .75 and c.decision == "warn" and c.suspect_bright_fraction == .25
    assert usable_mask(scl)[0, 3]

def test_masked_pixels_are_nan():
    dn = np.full((len(BANDS), 2, 2), 3000, "uint16"); scl = np.array([[4, 9], [3, 0]], "uint8")
    out = to_model_array(dn, scl, {b: R4 for b in BANDS})
    assert np.isfinite(out[:, 0, 0]).all() and np.isnan(out[:, 0, 1]).all() and np.isnan(out[:, 1, 0]).all()

def test_chips_cover_all():
    seen = np.zeros((1000, 777), bool)
    for r, c in iter_chips(1000, 777): seen[r:r + 512, c:c + 512] = True
    assert seen.all()

def test_outline_empty_centre():
    m = np.zeros((9, 9), bool); m[2:7, 2:7] = True; e = outline(m)
    assert e[2, 2] and not e[4, 4] and not e[0, 0]

def test_gallery_and_gis(tmp_path):
    raw = make_scene(tmp_path / "raw", size=128); d = tmp_path / "p" / raw.name; prepare_scene(raw, d)
    page = build_gallery([d], tmp_path / "v", {raw.name: raw / "truth.geojson"})
    t = page.read_text(); assert "Synthetic test scene" in t and "classed as snow" in t and "Polygons on imagery" in t
    for f in ("true_colour.tif", "swir_false_colour.tif", "quality.tif", "labels.gpkg"):
        assert (tmp_path / "v/gis" / raw.name / f).exists()
    with rasterio.open(tmp_path / "v/gis" / raw.name / "true_colour.tif") as s: assert s.crs.to_epsg() == 32632
    assert json.loads((tmp_path / "v/summary.json").read_text())[0]["coverage"]["decision"] in ("pass", "warn")

def test_labels_align_with_grid(tmp_path):
    import geopandas as gpd
    from minefoot.labels import rasterize_to
    raw = make_scene(tmp_path, size=128); gdf = gpd.read_file(raw / "truth.geojson")
    m = rasterize_to(raw / "raw_dn.tif", gdf); assert m.sum() > 0
    gdf2 = gdf.to_crs(4326); m2 = rasterize_to(raw / "raw_dn.tif", gdf2)
    assert (m != m2).mean() < .01

def _local_item(tmp_path, offset_applied=False):
    """A fake STAC item backed by local files at true 10 m and 20 m resolution."""
    import types
    from rasterio.transform import from_origin
    from minefoot.catalog import PROVIDERS
    from minefoot.schema import BANDS_10M
    assets = {}
    for b, asset in PROVIDERS["planetary"]["assets"].items():
        px = 10 if b in BANDS_10M else 20
        n = 1000 // (px // 10)
        arr = np.full((n, n), 5 if b == "SCL" else 3000, "uint16")
        # A bright marker square at a known ground position: x 601000-601200, y 1099000-1098800.
        col, row = (1000 // px), (1000 // px)
        k = 200 // px
        if b != "SCL": arr[row:row + k, col:col + k] = 6000
        path = tmp_path / f"{b}.tif"
        with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=1, dtype="uint16", crs="EPSG:32632",
                           transform=from_origin(600000, 1100000, px, px), nodata=0) as d: d.write(arr, 1)
        assets[asset] = types.SimpleNamespace(href=str(path), to_dict=lambda: {})
    return types.SimpleNamespace(id="LOCAL", assets=assets, properties={"s2:processing_baseline": "05.10", "datetime": "2026-01-14T10:00:00Z", "platform": "t", "eo:cloud_cover": 1.0})

def test_fetch_window_alignment_and_prepare(tmp_path):
    from rasterio.warp import transform_bounds
    from minefoot.catalog import fetch_scene
    item = _local_item(tmp_path)
    bbox = transform_bounds("EPSG:32632", "EPSG:4326", 600500, 1098500, 601500, 1099500)
    raw = fetch_scene(item, "planetary", bbox, tmp_path / "out")
    meta = json.loads((raw / "scene.json").read_text())
    assert meta["radiometry"]["B04"]["offset_dn"] == -1000 and meta["sha256_raw"]
    prepare_scene(raw, tmp_path / "p")
    with rasterio.open(tmp_path / "p/reflectance.tif") as s:
        assert s.res == (10.0, 10.0) and s.crs.to_epsg() == 32632
        b = list(s.descriptions)
        for band in ("B04", "B11"):   # 10 m and 20 m bands must land in the same place
            a = s.read(b.index(band) + 1)
            r, c = rasterio.transform.rowcol(s.transform, 601100, 1098900)
            assert abs(float(a[r, c]) - .5) < 1e-3
            r2, c2 = rasterio.transform.rowcol(s.transform, 600800, 1099300)
            assert abs(float(a[r2, c2]) - .2) < 1e-3
            ys, xs = np.where(a > .35)
            left = s.transform.c + xs.min() * 10
            assert abs(left - 601000) <= 20, (band, left)


def test_copernicus_notice_has_acquisition_year():
    from minefoot.catalog import copernicus_notice
    assert copernicus_notice("2026-01-03T09:58:12Z") == "Contains modified Copernicus Sentinel data 2026"
    assert copernicus_notice(None) == "Contains modified Copernicus Sentinel data"


def _fake_global_ml_ready(path):
    """Tiny GeoPackage with the layer structure of Global ML-ready v1.0.0."""
    import geopandas as gpd
    from shapely.geometry import box
    sid = "S2B_MSIL2A_20191004T074749_R135_T35KQR_20201003T223418"
    rows = [  # tile_id, lon, split, source_dataset, preferred, scene, has_label
        (1, -1.9, "train", "both", "maus", sid, True),       # Africa
        (2, 25.0, "test", "maus", "maus", sid, True),        # Africa
        (3, 140.0, "val", "tang", "tang", sid, True),
        (4, -60.0, "train", "both", "tang", sid, True),
        (5, 10.0, None, "rejected", "none", sid, False),     # rejected by authors
        (6, 11.0, None, "both", "maus", sid, True),          # no split
        (7, 12.0, "train", "both", "maus", "not-an-id", True),
        (8, 13.0, "train", "both", "maus", sid, False),      # empty label
    ]
    tiles = gpd.GeoDataFrame({"tile_id": [r[0] for r in rows], "s2_tile_id": [r[5] for r in rows],
                              "split": [r[2] for r in rows], "source_dataset": [r[3] for r in rows],
                              "preferred_dataset": [r[4] for r in rows], "minetype1": "Surface",
                              "minetype2": ["Artisanal", "Industrial"] * 4, "comment": "", "timestamp": "2024-07-04"},
                             geometry=[box(r[1], 5, r[1] + 0.2, 5.2) for r in rows], crs=4326)
    tiles.to_file(path, layer="tiles", driver="GPKG")
    labs = gpd.GeoDataFrame({"tile_id": [r[0] for r in rows]},
                            geometry=[box(r[1] + .05, 5.05, r[1] + .1, 5.1) if r[6] else None for r in rows], crs=4326)
    labs.to_file(path, layer="preferred_polygons", driver="GPKG")
    return path


def test_parse_s2_id():
    from minefoot.datasets import parse_s2_id
    assert parse_s2_id("S2B_MSIL2A_20191004T074749_R135_T35KQR_20201003T223418") == ("2019-10-04", "35KQR")
    with pytest.raises(ValueError):
        parse_s2_id("S2B_T32PMR_20260103T094921_L2A")


def test_global_ml_ready_adapter_counts_every_drop(tmp_path):
    from minefoot.datasets import load_global_ml_ready, write_ingest
    sites, labels, rep = load_global_ml_ready(_fake_global_ml_ready(tmp_path / "g.gpkg"))
    assert rep["tiles_in"] == 8 and rep["sites_out"] == 4
    assert rep["dropped"] == {"rejected_by_dataset_authors": 1, "no_source_split": 1,
                              "unparseable_scene_id": 1, "no_label_geometry": 1}
    assert rep["tiles_in"] == rep["sites_out"] + sum(rep["dropped"].values())
    assert rep["schema_problems"] == []
    assert set(labels.site_id) == set(sites.site_id)
    assert (sites.licence == "CC BY-SA 4.0").all() and (sites.dataset_version == "1.0.0").all()
    assert sites.loc[sites.site_id == "gmlr-1", "acquired"].item() == "2019-10-04"
    path = write_ingest(sites, labels, rep, tmp_path / "out")
    assert json.loads((tmp_path / "out" / "ingest_report.json").read_text())["sites_out"] == 4
    assert path.exists()


def test_check_sites_names_problems(tmp_path):
    from minefoot.datasets import check_sites, load_global_ml_ready
    sites, _, _ = load_global_ml_ready(_fake_global_ml_ready(tmp_path / "g.gpkg"))
    dup = sites.copy(); dup.loc[1, "site_id"] = dup.loc[0, "site_id"]
    assert "duplicate site_id" in check_sites(dup)
    assert "missing column licence" in check_sites(sites.drop(columns="licence"))


def test_select_sites_deterministic_and_africa_weighted(tmp_path):
    from minefoot.datasets import load_global_ml_ready, select_sites
    sites, _, _ = load_global_ml_ready(_fake_global_ml_ready(tmp_path / "g.gpkg"))
    a, b = select_sites(sites, 2, seed=3), select_sites(sites, 2, seed=3)
    assert a.site_id.tolist() == b.site_id.tolist()
    assert set(a.site_id) & {"gmlr-1", "gmlr-2"}  # half from Africa
    assert a.crs.to_epsg() == 4326 and len(a) == 2


def test_inspect_dataset_expects_columns_per_layer(tmp_path):
    from minefoot.labels import inspect_dataset
    rep = inspect_dataset(_fake_global_ml_ready(tmp_path / "g.gpkg"))
    assert rep["layers"]["tiles"]["missing_expected"] == []
    assert rep["layers"]["preferred_polygons"]["missing_expected"] == []


def test_mask_rates_inside_vs_outside():
    from minefoot.quality import mask_rates
    scl = np.full((10, 10), 4, "uint8"); inside = np.zeros((10, 10), bool); inside[:5] = True
    scl[:2] = 8  # 20 of 50 labelled pixels flagged as medium cloud
    r = mask_rates(scl, inside)
    assert r["inside_cloud"] == pytest.approx(0.4) and r["outside_cloud"] == 0
    assert r["inside_usable"] == pytest.approx(0.6) and r["outside_usable"] == 1


def test_polygon_coverage_status_and_fractions():
    from minefoot.quality import polygon_coverage
    v = np.array([4] * 90 + [8] * 8 + [3] * 2, "uint8")
    r = polygon_coverage(v)
    assert r["pixels"] == 100 and r["masked_fraction"] == pytest.approx(0.10)
    assert r["cloud_fraction"] == pytest.approx(0.08) and r["cloud_medium_fraction"] == pytest.approx(0.08)
    assert r["shadow_fraction"] == pytest.approx(0.02) and r["mask_status"] == "partly_masked"
    assert polygon_coverage(np.full(10, 9, "uint8"))["mask_status"] == "mostly_masked"
    assert polygon_coverage(np.full(10, 5, "uint8"))["mask_status"] == "assessed"
    snow = polygon_coverage(np.full(10, 11, "uint8"))  # snow is kept, so it is not masked
    assert snow["snow_fraction"] == 1 and snow["mask_status"] == "assessed"
    assert polygon_coverage(np.array([], "uint8"))["mask_status"] == "outside_image"


def _scene_polygons(tmp_path):
    """Prepared synthetic scene plus polygons over cloud, clear ground, outside, and sub-pixel."""
    import geopandas as gpd
    from shapely.geometry import box
    raw = make_scene(tmp_path / "raw", size=128); d = tmp_path / "p" / raw.name; prepare_scene(raw, d)
    with rasterio.open(d / "scl.tif") as s:
        scl, t, crs = s.read(1), s.transform, s.crs
    def px_box(r0, c0, r1, c1):
        x0, y0 = t * (c0, r1); x1, y1 = t * (c1, r0); return box(x0, y0, x1, y1)
    cloudy = np.isin(scl, (8, 9))
    rr, cc = np.nonzero(cloudy); r, c = int(np.median(rr)), int(np.median(cc))
    clear = np.argwhere(~np.isin(scl, (0, 1, 3, 8, 9, 10)))
    cr, ccl = clear[len(clear) // 2]
    x, y = t * (int(ccl) + 0.5, int(cr) + 0.5)
    polys = [px_box(r - 2, c - 2, r + 3, c + 3),                     # over cloud
             px_box(int(cr), int(ccl), int(cr) + 1, int(ccl) + 1),   # one clear pixel
             px_box(-50, -50, -40, -40),                             # outside the image
             box(x - 1, y - 1, x + 1, y + 1)]                        # smaller than a pixel
    gdf = gpd.GeoDataFrame({"name": ["cloud", "clear", "outside", "tiny"]}, geometry=polys, crs=crs)
    return raw, d, gdf, cloudy[r - 2:r + 3, c - 2:c + 3].mean()


def test_polygon_mask_report_per_polygon(tmp_path):
    from minefoot.labels import polygon_mask_report, summarise_mask_report
    _, d, gdf, cloud_share = _scene_polygons(tmp_path)
    r = polygon_mask_report(d, gdf.to_crs(4326)).set_index("name")
    assert r.loc["cloud", "pixels"] == 25
    assert r.loc["cloud", "cloud_fraction"] == pytest.approx(cloud_share)
    assert r.loc["cloud", "mask_status"] in ("partly_masked", "mostly_masked")
    assert r.loc["clear", "mask_status"] == "assessed" and r.loc["clear", "masked_fraction"] == 0
    assert r.loc["outside", "mask_status"] == "outside_image" and r.loc["outside", "pixels"] == 0
    assert r.loc["tiny", "pixels"] >= 1
    assert (r["scene_id"] == json.loads((d / "prepared.json").read_text())["scene_id"]).all()
    sm = summarise_mask_report(r)
    assert sm["polygons"] == 4 and sm["by_status"]["outside_image"] == 1


def test_gallery_warns_when_polygons_masked(tmp_path):
    import geopandas as gpd
    raw, d, gdf, _ = _scene_polygons(tmp_path)
    gdf.to_file(tmp_path / "polys.geojson", driver="GeoJSON")
    page = build_gallery([d], tmp_path / "v", {d.name: tmp_path / "polys.geojson"}).read_text()
    assert "were not assessed" in page and "Masking inside polygons" in page
    g = gpd.read_file(tmp_path / "v/gis" / d.name / "labels.gpkg")
    assert {"mask_status", "masked_fraction", "cloud_fraction"} <= set(g.columns)
    s = json.loads((tmp_path / "v/summary.json").read_text())[0]
    assert s["polygon_masking"]["polygons"] == 4


def test_cli_mask_report(tmp_path):
    from minefoot.cli import main
    _, d, gdf, _ = _scene_polygons(tmp_path)
    gdf.to_file(tmp_path / "polys.geojson", driver="GeoJSON")
    assert main(["mask-report", str(d), "--polygons", str(tmp_path / "polys.geojson"),
                 "--out", str(tmp_path / "r" / "mask.gpkg")]) == 0
    assert (tmp_path / "r" / "mask.gpkg").exists() and "mask_status" in (tmp_path / "r" / "mask.csv").read_text()
