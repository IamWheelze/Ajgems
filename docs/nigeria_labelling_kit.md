# Nigerian labelling kit: how to digitise and lock the test sites (step 7)

This kit is how the Nigerian test set gets made. Gate G3 tests the current best model (`g2_hn`) on these sites without training on them, which gives the first measure of Nigerian performance. Labels follow [`labelling_protocol.md`](labelling_protocol.md).

**Regions** ([`configs/nigeria_aois.yaml`](../configs/nigeria_aois.yaml)): Jos Plateau and Kogi/Kwara, one clear dry-season Sentinel-2 scene each. The scene IDs are pinned in that file, so every machine builds the same kit. Their dates and coverage are in [`evidence/nigeria_kit_v1.json`](evidence/nigeria_kit_v1.json).

## 1. Get the kit on your machine

The imagery is too large for the repository, so build it locally. It takes 10 to 20 minutes and about 2 GB of disk.

```bash
pip install -e ".[dev]"
minefoot labelling-kit configs/nigeria_aois.yaml --out data/labelling_kit/nigeria_v1
```

Open `data/labelling_kit/nigeria_v1/project.qgs` in QGIS 3.34 or later. You will see:

- **Mining labels (digitise here)** and **Hard negatives (digitise here)**: the two layers you draw in. Their fields are drop-down lists.
- One group per region: true colour (on), SWIR and NIR false colour, NDVI, NDWI, the quality mask, scene classes, and 10-band reflectance (use the Identify tool for pixel values).
- An OpenStreetMap layer (off; needs internet). It is for orientation only. **Never label from a base map**: label what is visible in the Sentinel-2 scene on its date.

## 1b. Start from the draft labels (optional, recommended)

`docs/evidence/nigeria_draft_labels_v1/` holds draft labels made from the two pinned scenes alone, with no model involved:
- 24 sites (23 Jos, 1 Kogi/Kwara), 126 mining polygons and 103 ignore areas, every 1 km window completed;
- 10 hard negatives.

Open `review.html` first: it shows every site with its outlines, its window and a confidence note. Then copy the drafts into the kit and edit them in QGIS:

```bash
cp docs/evidence/nigeria_draft_labels_v1/mining_labels.gpkg docs/evidence/nigeria_draft_labels_v1/hard_negatives.gpkg data/labelling_kit/nigeria_v1/labels/
```

Every draft polygon has `annotator = claude-draft` and `evidence_grade = E0`, and `lock-labels` refuses to lock while any remain. Review site by site:

1. **Make drafts stand out.** In QGIS, open the Layer Styling panel on *Mining labels*, choose *Categorized* by `annotator`, and classify. Drafts and your reviewed polygons now differ in colour, so progress is visible on the map.
2. **Go to a site.** Open the attribute table, filter `"site_id" = 'ng-jos-001'`, select all, then *Zoom to selection*. Turn on the SWIR false-colour layer too: spoil and tailings stand out from vegetation there.
3. **Judge each polygon against the scene, never the base map:**
   - mining, outline fine: keep it;
   - mining, outline wrong: use the Vertex tool (or Reshape) to fix it;
   - not mining (farm dam, quarry, construction): delete it, or change `label_class` to `ignore` if you can't tell;
   - missed mining within 1 km: add a polygon with the same `site_id` and `role = context`.
   - If you doubt the whole site, delete all its polygons.
4. **Take over what you checked.** Select the site's remaining polygons, then open the Field Calculator ("Update existing field", "Only update selected features"):
   - set `annotator` to `'your name'`;
   - set `evidence_grade` to `'E1'`;
   - set `mine_scale` to `'artisanal'` or `'industrial'` where you know it.
5. **Save edits**, then check progress:

```bash
minefoot labels-progress data/labelling_kit/nigeria_v1/labels/mining_labels.gpkg --kit data/labelling_kit/nigeria_v1/kit.json
```

It lists each site with its remaining drafts and ends with "Ready to lock" or the reasons it isn't. Do the hard negatives the same way (`hard_negatives.gpkg`). Kogi/Kwara has only one draft site, so add the sites you know there to reach 30 or more in total.

## 2. Digitise mining sites

Aim for **30 to 50 sites**, spread across both regions and across artisanal and industrial workings.

1. Select **Mining labels**, toggle editing, and add polygons around visible mining disturbance: pits, dumps, tailings, mine ponds, processing areas. Leave out access roads.
2. Fill the fields for each polygon:
   - `site_id`: one ID per site, shared by all polygons of that site, like `ng-jos-001` or `ng-kogi-001` (`ng-<region>-<3 digits>`).
   - `label_class`: `mining_disturbance`, or `ignore` for areas you cannot judge (cloud, shadow, unsure). Ignore areas are left out of scoring.
   - `mine_scale`: `artisanal`, `industrial` or `unknown`.
   - `scene_id` and `image_date`: copy them from the region's group name in the layer list.
   - `evidence_grade`: E1 for what you see in this imagery. Use E2 if you checked dated high-resolution imagery, and E3 only for a field visit.
3. Keep polygons at least 0.5 ha. Label only what is visible on this date.
4. **Label everything within 1 km of each site** under the same `site_id`, with `role = context` (the site's own polygons are `role = site`). G3 scores each site in a 1 km window, so unlabelled mining there would count as a false alarm. Use `ignore` where you cannot judge.

Never record mineral type, ownership, legality or whether a site is active (FR-110).

## 3. Digitise hard negatives

In **Hard negatives**, draw things that look like mining but are not. G1 and G2 failed most often on these:

- bare fallow fields and bare slopes (`bare_ground`)
- dry stream beds and riverbanks (`riverbank`)
- quarries and borrow pits, which this protocol treats as not mining (`quarry`, `borrow_pit`)
- settlements and construction sites (`settlement`, `construction`)
- dry ponds (`dry_pond`) and burn scars (`burn_scar`)

Use the same `scene_id`, `image_date` and `evidence_grade` rules as for mining labels.

## 4. Check, then lock

```bash
minefoot labels-check data/labelling_kit/nigeria_v1/labels/mining_labels.gpkg --kit data/labelling_kit/nigeria_v1/kit.json
minefoot negatives-check data/labelling_kit/nigeria_v1/labels/hard_negatives.gpkg
```

Fix anything they name. When the sites are final, lock them:

```bash
minefoot lock-labels data/labelling_kit/nigeria_v1/labels/mining_labels.gpkg --kit data/labelling_kit/nigeria_v1/kit.json --out docs/evidence/nigeria_test_lock_v1.json --note "founder, first 30-50 sites"
```

The lock records a SHA-256 hash of every site's geometry and attributes, and it refuses to overwrite an existing lock. From then on, `minefoot verify-lock <labels.gpkg> --lock docs/evidence/nigeria_test_lock_v1.json` names any site that was edited or removed. Sites added later are not in the test set.

**Rules for the locked set (ML-07):** these sites are for testing only. Never train on them, never choose thresholds on them, and keep a second annotator's check (FRD 8.2) separate from the locked copy.

## 5. Send the labels back

Commit the two label files and the lock, then push:

```bash
git add data/labelling_kit/nigeria_v1/labels/mining_labels.gpkg data/labelling_kit/nigeria_v1/labels/hard_negatives.gpkg docs/evidence/nigeria_test_lock_v1.json -f
git commit -m "Add locked Nigerian test labels v1"
git push
```

`data/` is in `.gitignore`, so `-f` is needed. The label files are small.

Then G3 runs with one command. It checks the lock first and refuses edited labels:

```bash
minefoot g3 --run data/runs/g2_hn --kit data/labelling_kit/nigeria_v1 --labels data/labelling_kit/nigeria_v1/labels/mining_labels.gpkg --lock docs/evidence/nigeria_test_lock_v1.json --negatives data/labelling_kit/nigeria_v1/labels/hard_negatives.gpkg --out data/runs/g3_v1
```

It reports:
- pixel and footprint metrics per region and mine scale, with site-bootstrap intervals, and the gap to global test performance;
- site recall against FRD objective O1 (sites of 1 ha or more found, target 0.70);
- the masking share per site, and per-type false-alert rates on your hard negatives;
- model flags outside the 1 km windows as **unverified candidates**, not errors.

The threshold stays the one chosen on global validation, and every run is appended to `docs/evidence/nigeria_test_access_log.csv` with the lock hash (ML-07).

The hard negatives can also join training (`minefoot rasterize-negatives`, then `g2 --negatives-root`), but only negatives **outside** the locked test sites' blocks, so test independence holds.
