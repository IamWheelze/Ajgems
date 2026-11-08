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


def _site_table(points, dataset="a", scene=None, size_km=2.0):
    """Square site footprints of size_km around (lon, lat) points, in the common schema."""
    import geopandas as gpd
    from shapely.geometry import box
    d = size_km / 111.32 / 2
    rows = []
    for i, (lon, lat) in enumerate(points):
        dl = d / max(np.cos(np.radians(lat)), 0.1)
        rows.append({"dataset": dataset, "dataset_version": "1", "licence": "test", "source": "test",
                     "site_id": f"{dataset}-{i}", "scene_id": scene or f"{dataset}-scene-{i}", "acquired": "2020-01-01",
                     "mgrs_tile": "32PMR", "source_split": None, "mine_type": "Surface", "mine_scale": "Artisanal",
                     "label_source": "test", "geometry": box(lon - dl, lat - d, lon + dl, lat + d)})
    return gpd.GeoDataFrame(rows, crs=4326)


def test_overlaps_flag_seeded_duplicates_across_datasets():
    import geopandas as gpd, pandas as pd
    from shapely.geometry import box
    from minefoot.splits import overlap_report
    a = _site_table([(8.0, 9.0), (20.0, -5.0), (30.0, 10.0)], "a")
    b = _site_table([(8.0, 9.0), (20.005, -5.0), (100.0, 40.0)], "b")   # b-0 duplicates a-0; b-1 overlaps a-1
    b.loc[2, "scene_id"] = a.loc[2, "scene_id"]                         # b-2 reuses a-2's scene
    sites = pd.concat([a, b], ignore_index=True)
    labels = gpd.GeoDataFrame({"site_id": ["a-1", "b-1", "a-2"]},
                              geometry=[box(19.99, -5.0, 20.01, -4.99), box(20.0, -5.0, 20.02, -4.99), box(30, 10, 30.01, 10.01)], crs=4326)
    r = overlap_report(gpd.GeoDataFrame(sites, crs=4326), labels)
    got = {(x.site_a, x.site_b, x.kind) for x in r.itertuples()}
    assert ("a-0", "b-0", "footprint_overlap") in got
    assert ("a-1", "b-1", "footprint_overlap") in got and ("a-1", "b-1", "label_overlap") in got
    assert ("a-2", "b-2", "same_scene") in got
    dup = r[(r.site_a == "a-0") & (r.site_b == "b-0")].iloc[0]
    assert dup.cross_dataset and dup.iou == pytest.approx(1.0, abs=1e-6)
    assert not any(x.site_a == "a-0" and x.site_b == "a-1" for x in r.itertuples())


def _cluster_points(seed=0, n=60):
    rng = np.random.default_rng(seed)
    centres = [(-2.0, 6.0), (8.8, 9.8), (30.0, -15.0), (120.0, -25.0), (-70.0, -20.0), (60.0, 55.0)]
    return [(cx + rng.normal(0, 0.6), cy + rng.normal(0, 0.6)) for cx, cy in centres for _ in range(n // len(centres))]


def test_split_has_no_leakage_and_is_deterministic():
    from minefoot.splits import assign_splits, check_split, overlap_report
    sites = _site_table(_cluster_points())
    ov = overlap_report(sites)
    s1, summ = assign_splits(sites, ov, (0.6, 0.2, 0.2), block_km=50, buffer_km=10, seed=1)
    s2, _ = assign_splits(sites, ov, (0.6, 0.2, 0.2), block_km=50, buffer_km=10, seed=1)
    assert s1["split"].tolist() == s2["split"].tolist()
    assert set(s1["split"]) == {"train", "val", "test"}
    assert check_split(s1, 10, ov)["ok"]
    assert summ["sites"] == len(sites) == sum(summ["by_split"].values())


def test_check_split_catches_leaks():
    from minefoot.splits import check_split
    sites = _site_table([(8.0, 9.0), (8.05, 9.0), (40.0, 9.0)])   # first two about 5.5 km apart
    sites["split"] = ["train", "test", "val"]
    r = check_split(sites, buffer_km=10)
    assert not r["ok"] and len(r["sites_within_buffer_across_splits"]) == 1
    assert check_split(sites, buffer_km=2)["ok"]


def test_block_ids_are_roughly_square():
    from minefoot.splits import block_id
    assert block_id(8.80, 9.80, 50) == block_id(8.81, 9.81, 50)
    assert block_id(8.80, 9.80, 50) != block_id(9.80, 9.80, 50)
    # At 60 degrees a 50 km block spans about 0.9 degrees of longitude, not 0.45.
    assert block_id(10.0, 60.1, 50) == block_id(10.8, 60.1, 50) or block_id(10.0, 60.1, 50) == block_id(9.2, 60.1, 50)


def test_locked_test_set_cannot_change(tmp_path):
    from minefoot.splits import assign_splits, check_split, lock_test, read_lock
    sites = _site_table(_cluster_points())
    s, _ = assign_splits(sites, None, (0.6, 0.2, 0.2), block_km=50, buffer_km=10, seed=2)
    lock = lock_test(s, tmp_path / "lock.json")
    with pytest.raises(FileExistsError):
        lock_test(s, tmp_path / "lock.json")
    # New sites arrive, one right next to a locked test site.
    t0 = s[s.split == "test"].geometry.iloc[0].centroid
    extra = _site_table([(t0.x + 0.01, t0.y), (150.0, -30.0)], "new")
    import geopandas as gpd, pandas as pd
    both = gpd.GeoDataFrame(pd.concat([sites, extra], ignore_index=True), crs=4326)
    s2, summ = assign_splits(both, None, (0.6, 0.2, 0.2), block_km=50, buffer_km=10, seed=99, lock=read_lock(tmp_path / "lock.json"))
    assert set(s2.loc[s2.split == "test", "site_id"]) == set(lock["site_ids"])
    assert s2.loc[s2.site_id == "new-0", "split"].item() == "held_out_near_locked_test"
    assert s2.loc[s2.site_id == "new-1", "split"].item() in ("train", "val")
    assert check_split(s2, 10, lock=lock)["ok"]
    # Editing the lock file is detected.
    d = json.loads((tmp_path / "lock.json").read_text()); d["site_ids"] = d["site_ids"][1:]
    (tmp_path / "edited.json").write_text(json.dumps(d))
    with pytest.raises(ValueError, match="edited"):
        read_lock(tmp_path / "edited.json")


def test_stratified_split_balances_mix():
    from minefoot.splits import assign_splits, check_split
    sites = _site_table(_cluster_points(n=120))
    rng = np.random.default_rng(0)
    sites["mine_scale"] = np.where(rng.random(len(sites)) < 0.25, "Artisanal", "Industrial")
    s, summ = assign_splits(sites, None, (0.6, 0.2, 0.2), block_km=50, buffer_km=0, seed=0, stratify="mine_scale")
    share = s.groupby("split")["mine_scale"].apply(lambda v: (v == "Artisanal").mean())
    assert share.max() - share.min() < 0.1
    assert check_split(s, 0)["ok"] and "by_split_and_stratum" in summ


def test_view_package_has_cogs_and_qgis_project(tmp_path):
    import xml.etree.ElementTree as ET
    from minefoot.cli import main
    raw = make_scene(tmp_path / "raw", size=128); d = tmp_path / "p" / raw.name; prepare_scene(raw, d)
    lr = tmp_path / "labels"; (lr / raw.name).mkdir(parents=True)
    (lr / raw.name / "labels.geojson").write_text((raw / "truth.geojson").read_text())
    out = tmp_path / "pkg"
    assert main(["view", str(d), "--labels-root", str(lr), "--out", str(out)]) == 0
    g = out / "gis" / raw.name
    for f in ("true_colour.tif", "reflectance.tif", "scl.tif", "quality.tif"):
        with rasterio.open(g / f) as s:
            assert s.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG", f
    root = ET.parse(out / "project.qgs").getroot()
    sources = [e.text for e in root.iter("datasource")]
    assert all(not src.startswith("/") for src in sources)          # relative paths only
    for src in sources:
        if src.startswith("./"):
            assert (out / src.split("|")[0]).exists(), src
    assert any(r.get("attr") == "mask_status" for r in root.iter("renderer-v2"))
    assert any(r.get("type") == "paletted" for r in root.iter("rasterrenderer"))
    assert {e.get("id") for e in root.iter("layer-tree-layer")} == {e.text for e in root.iter("id")}
    for f in ("scl_classes.qml", "polygons_by_mask_status.qml", "reflectance_true_colour.qml"):
        ET.parse(out / "styles" / f)
    # Every layer the project declares must be in the CRS its file actually has.
    import geopandas as gpd
    assert gpd.read_file(g / "labels.gpkg").crs.to_epsg() == 32632
    for ml in root.iter("maplayer"):
        src, auth = ml.find("datasource").text, ml.find("srs/spatialrefsys/authid")
        if src.startswith("./") and auth is not None:
            f = out / src.split("|")[0]
            epsg = gpd.read_file(f).crs.to_epsg() if f.suffix == ".gpkg" else rasterio.open(f).crs.to_epsg()
            assert auth.text == f"EPSG:{epsg}", src
    assert root.find("projectCrs/spatialrefsys/wkt").text.startswith("PROJCRS")


def _qgis_python():
    """A Python interpreter with QGIS bindings, or None. Set MINEFOOT_QGIS_PYTHON to choose one."""
    import os, shutil, subprocess
    cands = [os.environ.get("MINEFOOT_QGIS_PYTHON"), "python3", "/usr/bin/python3", "/usr/bin/python3.12"]
    for c in filter(None, cands):
        exe = shutil.which(c) or (c if os.path.exists(c) else None)
        if exe and subprocess.run([exe, "-c", "import qgis.core"], capture_output=True,
                                  env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}).returncode == 0:
            return exe
    return None


def test_qgis_opens_project(tmp_path):
    """Loads the generated project in real QGIS when bindings are installed; skipped otherwise."""
    import os, subprocess
    from pathlib import Path
    exe = _qgis_python()
    if not exe:
        pytest.skip("no Python with QGIS bindings")
    raw = make_scene(tmp_path / "raw", size=128); d = tmp_path / "p" / raw.name; prepare_scene(raw, d)
    build_gallery([d], tmp_path / "pkg", {raw.name: raw / "truth.geojson"})
    script = Path(__file__).parents[1] / "scripts" / "check_qgis_project.py"
    r = subprocess.run([exe, str(script), str(tmp_path / "pkg" / "project.qgs")], capture_output=True, text=True,
                       env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert r.returncode == 0, r.stdout + r.stderr


def test_g1_features_and_index_score():
    from minefoot.baseline import FEATURES, features, index_score
    refl = np.full((10, 2, 2), 0.1, "float32")
    refl[BANDS.index("B08")] = 0.4; refl[BANDS.index("B04")] = 0.1
    f = features(refl)
    assert f.shape == (len(FEATURES), 2, 2)
    assert f[FEATURES.index("NDVI")][0, 0] == pytest.approx(0.6)
    assert index_score(f, "NDVI", -1)[0, 0] == pytest.approx(0.2)   # high NDVI: low disturbance score


def test_g1_threshold_and_counts():
    from minefoot.baseline import best_threshold, counts_at, histograms, prf
    lv = np.array([[10, 200], [220, 30]], "uint8")
    valid = np.ones((2, 2), bool); label = np.array([[0, 1], [1, 0]], bool)
    h = histograms(lv, valid, label)
    t, f1 = best_threshold([h])
    assert 31 <= t <= 200 and f1 == 1.0
    c = counts_at(h, t); assert c == {"tp": 2, "fp": 0, "fn": 0, "tn": 2}
    assert prf(c)["iou"] == 1.0


def test_g1_object_metrics():
    from minefoot.baseline import object_metrics
    label = np.zeros((60, 60), bool); label[5:20, 5:20] = True; label[40:55, 40:55] = True   # two 225-px footprints
    pred = np.zeros_like(label); pred[5:8, 5:8] = True                                       # touches the first only
    pred[30:45, 5:20] = True                                                                 # 225-px false candidate
    valid = np.ones_like(label)
    m = object_metrics(pred, valid, label, mmu=100)
    assert m["label_footprints"] == 2 and m["found_any_overlap"] == 1 and m["covered_25pct"] == 0
    assert m["false_candidates"] == 1
    assert m["false_candidates_detached"] == 1 and m["false_candidate_ha"] == pytest.approx(2.25)
    # A large false area attached to a labelled mine: missed by the old rule, counted by the new one.
    blob = pred.copy(); blob[5:20, 5:60] = True        # labelled footprint plus 600 unlabelled pixels, one patch
    mb = object_metrics(blob, valid, label, mmu=100)
    assert mb["false_candidates_detached"] == 1       # only the old detached patch
    assert mb["false_candidates"] == 2                 # the attached blob is mostly unlabelled, so it counts too
    valid[40:55, 40:55] = False                       # the missed footprint was never seen
    assert object_metrics(pred, valid, label, mmu=100)["label_footprints"] == 1


def test_g1_summary_has_site_bootstrap_interval():
    from minefoot.baseline import summarise
    rows = [{"tp": 50 + i, "fp": 10, "fn": 20, "tn": 1000, "label_footprints": 2, "found_any_overlap": 1,
             "covered_25pct": 1, "false_candidates": 1, "valid_km2": 10.0, "label_pixels": 70,
             "label_pixels_masked": 5} for i in range(8)]
    s = summarise(rows, n_boot=200)
    lo, hi = s["ci95"]["f1"]
    assert lo <= s["f1"] <= hi and s["sites"] == 8
    assert s["false_candidates_per_1000km2"] == pytest.approx(100.0)
    assert s["false_alarm_km2_per_1000km2"] == pytest.approx(1000 * 10 * 1e-4 / 10.0)
    assert s["label_pixels_masked_share"] == pytest.approx(5 / 70)


def test_g1_end_to_end_on_synthetic_sites(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box
    from minefoot.baseline import run_g1
    ids = {"train": ["s-tr1", "s-tr2"], "val": ["s-va"], "test": ["s-te"]}
    rows = []
    for i, (k, sids) in enumerate(ids.items()):
        for j, sid in enumerate(sids):
            raw = make_scene(tmp_path / "raw", size=96, seed=10 + 3 * i + j, scene_id=sid)
            prepare_scene(raw, tmp_path / "prep" / sid)
            (tmp_path / "labels" / sid).mkdir(parents=True)
            (tmp_path / "labels" / sid / "labels.geojson").write_text((raw / "truth.geojson").read_text())
            rows.append({"site_id": sid, "split": k, "mine_scale": "Artisanal", "mine_type": "Surface",
                         "dataset": "synthetic", "geometry": box(10 * len(rows), 0, 10 * len(rows) + 1, 1)})
    gpd.GeoDataFrame(rows, crs=4326).to_file(tmp_path / "split.gpkg", layer="sites", driver="GPKG")
    m = run_g1(ids, tmp_path / "split.gpkg", tmp_path / "prep", tmp_path / "labels", tmp_path / "run", log=lambda *_: None)
    assert set(m["models"]) == {m["best_index"], "random_forest"}
    t = m["models"]["random_forest"]["test"]["strata"]["all"]
    assert 0 <= t["f1"] <= 1 and t["sites"] == 1
    cfg = json.loads((tmp_path / "run" / "config.json").read_text())
    assert cfg["sites"] == {k: sorted(v) for k, v in ids.items()} and cfg["split_file_sha256"]
    assert (tmp_path / "run" / "error_maps" / "s-te__random_forest.tif").exists()
    from minefoot.viewer import error_gallery
    g = error_gallery(tmp_path / "run", "random_forest", tmp_path / "prep", tmp_path / "eg.html").read_text()
    assert "s-te" in g and "false alarm" in g and "data:image/jpeg" in g
    bad = {**ids, "test": ["s-va"]}                       # a validation site listed as test is refused
    with pytest.raises(ValueError):
        run_g1(bad, tmp_path / "split.gpkg", tmp_path / "prep", tmp_path / "labels", tmp_path / "run2", log=lambda *_: None)


def _rows(tp_list, fp=10):
    return [{"site_id": f"s{i}", "tp": tp, "fp": fp, "fn": 100 - tp, "tn": 1000 - fp, "label_footprints": 2,
             "found_any_overlap": 1, "covered_25pct": 1, "false_candidates": 1, "valid_km2": 10.0,
             "label_pixels": 60, "label_pixels_masked": 0} for i, tp in enumerate(tp_list)]


def test_paired_compare_detects_consistent_gain_and_rejects_mismatch():
    from minefoot.baseline import paired_compare
    rng = np.random.default_rng(0)
    base = list(rng.integers(20, 60, 20))
    a = _rows(base); b = _rows([t + 15 for t in base])                     # B better on every site
    r = paired_compare(a, b, n_boot=300)
    assert r["metrics"]["f1"]["verdict"] == "b better" and r["metrics"]["f1"]["ci95"][0] > 0
    same = paired_compare(a, _rows(base), n_boot=300)
    assert same["metrics"]["f1"]["verdict"] == "no clear difference"
    fewer_fc = _rows(base); [x.update(false_candidates=0) for x in fewer_fc]
    assert paired_compare(a, fewer_fc, n_boot=300)["metrics"]["false_candidates_per_1000km2"]["verdict"] == "b better"
    with pytest.raises(ValueError):
        paired_compare(a, a[:-1], n_boot=10)


def test_proxy_type_map_excludes_near_labels_and_finds_banks():
    from minefoot.negatives import WC, proxy_type_map, false_alerts_by_type, pooled_rates
    wc = np.full((40, 40), WC["crop"], "uint8"); wc[:, 20:] = WC["bare"]; wc[30:, :] = WC["water"]
    label = np.zeros((40, 40), bool); label[0:3, 0:3] = True
    t = proxy_type_map(wc, label, buffer_px=5, bank_px=2)
    assert t[2, 2] == 0 and t[7, 2] == 0                # inside and near the label: excluded
    assert t[15, 10] == 1 and t[15, 30] == 4            # farmland, bare ground
    assert t[28, 10] == 3 and t[35, 10] == 0            # bank above the water; water itself excluded
    pred = np.zeros_like(label); pred[10:20, 25:35] = True
    r = false_alerts_by_type(pred, np.ones_like(label), t)
    assert r["bare_ground"]["predicted"] == 100 and r["farmland"]["predicted"] == 0
    p = pooled_rates([r, r], n_boot=50)
    assert p["farmland"]["rate"] == 0 and p["bare_ground"]["rate"] > 0 and p["settlement"]["rate"] is None


def test_negatives_template_and_check(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box
    from minefoot.negatives import check_negatives, write_template
    p = write_template(tmp_path / "neg.gpkg")
    assert p.with_suffix(".qml").exists() and "ValueMap" in p.with_suffix(".qml").read_text()
    assert check_negatives(p) == {"features": 0, "by_type": {t: 0 for t in check_negatives(p)["by_type"]}, "problems": []}
    rows = [{"neg_type": "farmland", "protocol_version": "0.1", "scene_id": "S2", "image_date": "2026-01-03",
             "annotator": "A", "evidence_grade": "E1", "notes": "", "geometry": box(8.80, 9.70, 8.81, 9.71)},
            {"neg_type": "tin mine", "protocol_version": "0.1", "scene_id": "S2", "image_date": "2026-01-03",
             "annotator": "", "evidence_grade": "E9", "notes": "", "geometry": box(8.82, 9.70, 8.8201, 9.7001)}]
    gpd.GeoDataFrame(rows, crs=4326).to_file(p, layer="hard_negatives", driver="GPKG")
    r = check_negatives(p)
    assert r["by_type"]["farmland"] == 1
    joined = " ".join(r["problems"])
    assert "tin mine" in joined and "annotator" in joined and "evidence_grade" in joined and "smaller than" in joined


def test_training_sample_adds_typed_negatives(tmp_path):
    import rasterio
    from minefoot.baseline import sample_training_pixels
    from minefoot.negatives import WC
    raw = make_scene(tmp_path / "raw", size=128, scene_id="s1"); prepare_scene(raw, tmp_path / "prep" / "s1")
    with rasterio.open(tmp_path / "prep" / "s1" / "scl.tif") as s:
        prof = {**s.profile, "dtype": "uint8", "nodata": 0}
    wc = np.full((128, 128), WC["crop"], "uint8"); wc[:, 64:] = WC["built"]
    (tmp_path / "wc").mkdir()
    with rasterio.open(tmp_path / "wc" / "s1.tif", "w", **prof) as d:
        d.write(wc, 1)
    site = [("s1", tmp_path / "prep" / "s1", raw / "truth.geojson")]
    X0, y0, _ = sample_training_pixels(site, per_class=200)
    X1, y1, n = sample_training_pixels(site, per_class=200,
                                       hard_negatives={"worldcover_root": tmp_path / "wc", "per_type": 50, "buffer_px": 2})
    assert n[("s1", "farmland")] == 50 and n[("s1", "settlement")] == 50 and n[("s1", "riverbank")] == 0
    assert len(y1) == len(y0) + 100 and y1.sum() == y0.sum()
    assert np.array_equal(X1[:len(X0)], X0)            # same base sample, negatives added on top
    _, y2, n2 = sample_training_pixels(site, per_class=200, hard_negatives={"worldcover_root": tmp_path / "wc",
                                       "per_type": 50, "buffer_px": 2, "types": [1]})
    assert n2[("s1", "farmland")] == 50 and ("s1", "settlement") not in n2 and len(y2) == len(y0) + 50


def test_paired_compare_refuses_different_data():
    from minefoot.baseline import paired_compare
    a = _rows([30, 40, 50]); b = _rows([30, 40, 50]); b[1]["label_pixels"] = 61
    with pytest.raises(ValueError, match="not scored on the same data"):
        paired_compare(a, b, n_boot=10)


def test_g2_predict_tile_blends_and_respects_mask():
    torch = pytest.importorskip("torch"); pytest.importorskip("segmentation_models_pytorch")
    from minefoot.g2 import build_model, predict_tile
    refl = np.random.default_rng(0).random((10, 100, 90)).astype("float32") * 0.3
    refl[:, :5, :] = np.nan
    norm = {"mean": [0.15] * 10, "std": [0.1] * 10}
    m = build_model("resnet18", "none")
    s = predict_tile(m, refl, norm, torch.device("cpu"), chip=64, overlap=16, batch=4)
    assert s.shape == (100, 90) and (s[:5] == 0).all() and ((s >= 0) & (s <= 1)).all()


def test_g2_end_to_end_on_synthetic_sites(tmp_path):
    pytest.importorskip("torch"); pytest.importorskip("segmentation_models_pytorch")
    import geopandas as gpd
    from shapely.geometry import box
    from minefoot.g2 import run_g2
    ids = {"train": ["s-tr1", "s-tr2"], "val": ["s-va"], "test": ["s-te"]}
    rows = []
    for i, (k, sids) in enumerate(ids.items()):
        for j, sid in enumerate(sids):
            raw = make_scene(tmp_path / "raw", size=128, seed=10 + 3 * i + j, scene_id=sid)
            prepare_scene(raw, tmp_path / "prep" / sid)
            (tmp_path / "labels" / sid).mkdir(parents=True)
            (tmp_path / "labels" / sid / "labels.geojson").write_text((raw / "truth.geojson").read_text())
            rows.append({"site_id": sid, "split": k, "mine_scale": "Artisanal", "mine_type": "Surface",
                         "dataset": "synthetic", "geometry": box(10 * len(rows), 0, 10 * len(rows) + 1, 1)})
    gpd.GeoDataFrame(rows, crs=4326).to_file(tmp_path / "split.gpkg", layer="sites", driver="GPKG")
    m = run_g2(ids, tmp_path / "split.gpkg", tmp_path / "prep", tmp_path / "labels", tmp_path / "run",
               log=lambda *_: None, device="cpu", epochs=2, chip=64, batch=4, chips_per_site=4, workers=0,
               encoder="resnet18", encoder_weights="none")
    t = m["models"]["unet"]["test"]["strata"]["all"]
    assert 0 <= t["f1"] <= 1 and t["sites"] == 1
    for f in ("config.json", "normalisation.json", "history.csv", "unet_best.pt", "metrics.json"):
        assert (tmp_path / "run" / f).exists(), f
    cfg = json.loads((tmp_path / "run" / "config.json").read_text())
    assert cfg["gate"] == "G2" and cfg["params"]["epochs"] == 2
    kw = dict(log=lambda *_: None, device="cpu", chip=64, batch=4, chips_per_site=4, workers=0,
              encoder="resnet18", encoder_weights="none")
    with pytest.raises(FileExistsError):                  # never silently overwrite a run
        run_g2(ids, tmp_path / "split.gpkg", tmp_path / "prep", tmp_path / "labels", tmp_path / "run", epochs=2, **kw)
    with pytest.raises(ValueError, match="cannot resume"):  # resuming with other settings is refused
        run_g2(ids, tmp_path / "split.gpkg", tmp_path / "prep", tmp_path / "labels", tmp_path / "run", epochs=3,
               resume=True, **kw)
    # Interrupted after epoch 1 of 2: resuming finishes the remaining epoch only.
    r1 = run_g2(ids, tmp_path / "split.gpkg", tmp_path / "prep", tmp_path / "labels", tmp_path / "r1", epochs=2, **kw)
    import torch
    ck = torch.load(tmp_path / "r1" / "last.pt", weights_only=False)
    ck["epoch"], ck["history"] = 1, ck["history"][:1]
    torch.save(ck, tmp_path / "r1" / "last.pt")
    r2 = run_g2(ids, tmp_path / "split.gpkg", tmp_path / "prep", tmp_path / "labels", tmp_path / "r1", epochs=2,
                resume=True, **kw)
    assert r2["epochs"] == 2 and len(json.loads((tmp_path / "r1" / "config.json").read_text())["resumed"]) == 1
    assert sum(1 for _ in open(tmp_path / "r1" / "history.csv")) == 3   # header + 2 epochs
    assert "training sites" in json.loads((tmp_path / "run" / "normalisation.json").read_text())["source"]

    # Hard-negative mining on training sites, then fine-tuning from the checkpoint with those negatives.
    from minefoot.g2 import mine_negatives, rasterize_negatives
    summ = mine_negatives(tmp_path / "run", ids["train"], tmp_path / "prep", tmp_path / "labels", tmp_path / "neg",
                          buffer_px=2, mmu=4, log=lambda *_: None)
    assert set(summ["sites"]) == set(ids["train"]) and (tmp_path / "neg" / "summary.json").exists()
    assert all((tmp_path / "neg" / f"{i}_neg.tif").exists() for i in ids["train"])
    import geopandas as gpd, rasterio
    from shapely.geometry import box
    from minefoot.negatives import write_template
    with rasterio.open(tmp_path / "prep" / "s-tr1" / "reflectance.tif") as src:
        b = src.bounds; crs = src.crs
    gpkg = write_template(tmp_path / "hn.gpkg")
    gpd.GeoDataFrame([{"neg_type": "riverbank", "protocol_version": "0.1", "scene_id": "S", "image_date": "2020-01-01",
                       "annotator": "A", "evidence_grade": "E1", "notes": "",
                       "geometry": box(b.left, b.bottom, b.left + 300, b.bottom + 300)}], crs=crs).to_file(
        gpkg, layer="hard_negatives", driver="GPKG")
    rr = rasterize_negatives(gpkg, ids["train"], tmp_path / "prep", tmp_path / "neg")
    assert rr["sites_with_negatives"] >= 1 and rr["by_type"]["riverbank"] == 1
    ft = run_g2(ids, tmp_path / "split.gpkg", tmp_path / "prep", tmp_path / "labels", tmp_path / "ft", epochs=1,
                negatives_root=tmp_path / "neg", init_from=tmp_path / "run" / "unet_best.pt", **kw)
    fcfg = json.loads((tmp_path / "ft" / "config.json").read_text())
    assert fcfg["hard_negatives"]["fraction"] == 0.25 and fcfg["init_from"]["sha256"]
    assert json.loads((tmp_path / "ft" / "normalisation.json").read_text()) == \
        json.loads((tmp_path / "run" / "normalisation.json").read_text())
    assert "unet" in ft["models"]


def test_chips_centre_on_hard_negatives(tmp_path):
    import rasterio
    from minefoot.g2 import ChipDataset
    raw = make_scene(tmp_path / "raw", size=128, scene_id="s1"); prepare_scene(raw, tmp_path / "p" / "s1")
    with rasterio.open(tmp_path / "p" / "s1" / "scl.tif") as s:
        prof = {**s.profile, "dtype": "uint8", "nodata": None}
    lab = np.zeros((128, 128), "uint8"); lab[:16, :16] = 1
    neg = np.zeros((128, 128), "uint8"); neg[100:120, 100:120] = 1
    for name, arr in (("lab.tif", lab), ("neg.tif", neg)):
        with rasterio.open(tmp_path / name, "w", **prof) as d:
            d.write(arr, 1)
    norm = {"mean": [0.1] * 10, "std": [0.1] * 10}
    ds = ChipDataset([tmp_path / "p" / "s1"], [tmp_path / "lab.tif"], norm, 32, 400, 0.0, 0, augment=False,
                     negative_paths=[tmp_path / "neg.tif"], negative_fraction=1.0)
    x, y, v = ds[0]
    assert x.shape == (10, 32, 32) and y.sum() == 0
    assert ds._negatives(0).size > 0
    assert all(ds[k][1].sum() == 0 for k in range(20))   # chips centred on the negative patch hold no mining label
    pos = ChipDataset([tmp_path / "p" / "s1"], [tmp_path / "lab.tif"], norm, 32, 400, 1.0, 0, augment=False)
    assert all(pos[k][1].sum() > 0 for k in range(20))   # chips centred on mining hold mining label


def test_compare_to_expected_split(tmp_path):
    import pandas as pd
    from minefoot.splits import compare_to_expected
    pd.DataFrame({"site_id": ["a", "b", "c"], "split": ["train", "val", "test"]}).to_csv(tmp_path / "e.csv", index=False)
    same = pd.DataFrame({"site_id": ["a", "b", "c"], "split": ["train", "val", "test"]})
    assert compare_to_expected(same, tmp_path / "e.csv") == []
    moved = pd.DataFrame({"site_id": ["a", "b", "d"], "split": ["train", "test", "val"]})
    d = compare_to_expected(moved, tmp_path / "e.csv")
    assert "missing site c" in d and "extra site d" in d and "b: test here, val expected" in d
