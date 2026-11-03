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
    t = page.read_text(); assert "Synthetic test scene" in t and "classed as snow" in t and "Check the fit" in t
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
