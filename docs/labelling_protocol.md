# Labelling protocol v0.1 (draft for the founder to review)

**Label what is visible on the stated image date only.** Record protocol version, image date, scene ID, annotator and evidence grade (E0 to E3) on each label.

| Feature | Label |
| --- | --- |
| Pits and excavations, waste dumps, tailings, mine ponds, heap leach pads, processing plants | Mining disturbance |
| Access roads | Not mining (provisional, FRD section 3) |
| Inactive workings still visible | Mining disturbance, with activity left unknown |
| Revegetated workings no longer distinguishable | Not labelled; note if known |
| Cloud, shadow, or unsure | Ignore class, excluded from training loss |

**Hard negatives, tagged by type:** quarry, borrow pit, farmland, riverbank, burn scar, settlement, dry pond, construction, bare ground.

`bare_ground` (bare rock, bare slopes, dry stream beds that are not mining) was added on 2026-11-07. In the G1 baselines most false candidates fell on bare fields and bare slopes (`docs/g1_results.md`).

To digitise hard negatives, run `minefoot negatives-template data/labels/hard_negatives.gpkg`, open the `.gpkg` in QGIS, and load the `.qml` next to it as the layer style. `neg_type` and `evidence_grade` then become drop-down lists. Draw on the Sentinel-2 scene named in `scene_id`. Check the file with `minefoot negatives-check data/labels/hard_negatives.gpkg`, which counts features per type and names any problems (FR-074).

A quarry may count as mining in some definitions. This draft treats it as a hard negative until the founder decides.

Minimum polygon: 0.5 ha for display, 1 ha for evaluation (provisional). Two annotators label a sample, and their agreement is reported. Do not label from a later base map without checking the Sentinel-2 date.
