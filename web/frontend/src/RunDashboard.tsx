import { useEffect, useMemo, useState } from "react";
import { api, CalibrationFit, ModelVersion, RunDashboard as Dash } from "./api";

// FR-130 run dashboard: training curves, per-stratum metrics with 95% intervals, precision-recall,
// reliability, and model comparison. Every value is read from the run's stored records (history.csv,
// metrics.json, curves.json); nothing is estimated in the browser.

// Categorical order, fixed per model by registration order (colour follows the model, never its rank).
// Validated with the dataviz palette checker against the light and dark panels.
const SLOTS = ["--series-1", "--series-2", "--series-3"];
const colourOf = (i: number) => `var(${SLOTS[i % SLOTS.length]})`;
const fmt = (v: number | null | undefined, d = 3) => (v === null || v === undefined ? "—" : v.toFixed(d));

type Pt = { x: number; y: number };
type Series = { key: string; label: string; colour: string; dash?: string; points: Pt[]; marker?: Pt & { label: string } };

const W = 560, H = 300, M = { l: 52, r: 14, t: 12, b: 42 };
const niceMax = (v: number, step = 0.1) => Math.ceil(v / step - 1e-9) * step;
const ticks = (lo: number, hi: number, n = 5) => Array.from({ length: n + 1 }, (_, i) => lo + ((hi - lo) * i) / n);

function XYChart({ title, series, x, y, xLabel, yLabel, diagonal, xFmt = (v) => fmt(v, 2), yFmt = (v) => fmt(v, 2) }: {
  title: string; series: Series[]; x: [number, number]; y: [number, number]; xLabel: string; yLabel: string;
  diagonal?: boolean; xFmt?: (v: number) => string; yFmt?: (v: number) => string;
}) {
  const [hx, setHx] = useState<number | null>(null);
  const sx = (v: number) => M.l + ((v - x[0]) / (x[1] - x[0] || 1)) * (W - M.l - M.r);
  const sy = (v: number) => H - M.b - ((v - y[0]) / (y[1] - y[0] || 1)) * (H - M.t - M.b);
  const path = (pts: Pt[]) => pts.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join("");
  const near = hx === null ? [] : series.map((s) => {
    const p = s.points.reduce<Pt | null>((b, q) => (b === null || Math.abs(q.x - hx) < Math.abs(b.x - hx) ? q : b), null);
    return p ? { s, p } : null;
  }).filter(Boolean) as { s: Series; p: Pt }[];
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < M.l || px > W - M.r) { setHx(null); return; }
    setHx(x[0] + ((px - M.l) / (W - M.l - M.r)) * (x[1] - x[0]));
  };
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <div className="chart-wrap">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} onMouseMove={onMove} onMouseLeave={() => setHx(null)}>
          {ticks(...y).map((t) => <g key={`y${t}`}><line x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} className="grid" />
            <text x={M.l - 6} y={sy(t) + 4} textAnchor="end" className="tick">{yFmt(t)}</text></g>)}
          {ticks(...x).map((t) => <text key={`x${t}`} x={sx(t)} y={H - M.b + 16} textAnchor="middle" className="tick">{xFmt(t)}</text>)}
          <line x1={M.l} x2={W - M.r} y1={H - M.b} y2={H - M.b} className="axis" />
          <text x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle" className="axis-label">{xLabel}</text>
          <text transform={`translate(13 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle" className="axis-label">{yLabel}</text>
          {diagonal && <line x1={sx(Math.max(x[0], y[0]))} y1={sy(Math.max(x[0], y[0]))} x2={sx(Math.min(x[1], y[1]))} y2={sy(Math.min(x[1], y[1]))} className="ref" />}
          {series.map((s) => <path key={s.key} d={path(s.points)} fill="none" stroke={s.colour} strokeWidth={2} strokeDasharray={s.dash} />)}
          {series.map((s) => s.marker && <g key={`m${s.key}`}>
            <circle cx={sx(s.marker.x)} cy={sy(s.marker.y)} r={5} fill={s.colour} stroke="var(--panel)" strokeWidth={2} />
            <text x={sx(s.marker.x) + 8} y={sy(s.marker.y) - 8} className="tick">{s.marker.label}</text></g>)}
          {hx !== null && near.length > 0 && <g pointerEvents="none">
            <line x1={sx(near[0].p.x)} x2={sx(near[0].p.x)} y1={M.t} y2={H - M.b} className="crosshair" />
            {near.map(({ s, p }) => <circle key={s.key} cx={sx(p.x)} cy={sy(p.y)} r={4} fill={s.colour} stroke="var(--panel)" strokeWidth={2} />)}
          </g>}
        </svg>
        {hx !== null && near.length > 0 && (
          <div className="chart-tip small" style={{ left: `${(sx(near[0].p.x) / W) * 100}%`, transform: sx(near[0].p.x) > W * 0.55 ? "translateX(calc(-100% - 8px))" : undefined }}>
            {near.map(({ s, p }) => <div key={s.key}><span className="swatch" style={{ background: s.colour }} /> {s.label}: {xLabel.toLowerCase()} {xFmt(p.x)}, {yLabel.toLowerCase()} {yFmt(p.y)}</div>)}
          </div>
        )}
      </div>
      <Legend items={series.map((s) => ({ label: s.label, colour: s.colour, dash: s.dash }))} />
    </figure>
  );
}

function Legend({ items }: { items: { label: string; colour: string; dash?: string }[] }) {
  if (items.length < 2) return null;
  return <div className="chart-legend small">{items.map((i) => (
    <span key={i.label}><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke={i.colour} strokeWidth="2" strokeDasharray={i.dash} /></svg> {i.label}</span>
  ))}</div>;
}

const METRICS: Record<string, { label: string; max?: number; d: number }> = {
  f1: { label: "Pixel F1", max: 1, d: 3 }, precision: { label: "Precision", max: 1, d: 3 }, recall: { label: "Recall", max: 1, d: 3 },
  footprint_recall_any: { label: "Footprints found", max: 1, d: 3 },
  false_alarm_km2_per_1000km2: { label: "False-alarm area, km² per 1,000 km²", d: 1 },
};

function StrataChart({ runs, split, metric }: { runs: { d: Dash; i: number }[]; split: "val" | "test"; metric: string }) {
  const strata = useMemo(() => {
    const keys = new Set<string>();
    runs.forEach(({ d }) => Object.keys(d.metrics?.strata[split] ?? {}).forEach((k) => keys.add(k)));
    return [...keys].sort((a, b) => (a === "all" ? -1 : b === "all" ? 1 : a.localeCompare(b)));
  }, [runs, split]);
  const [tip, setTip] = useState<string | null>(null);
  const spec = METRICS[metric];
  const vals = runs.flatMap(({ d }) => strata.flatMap((s) => {
    const r = d.metrics?.strata[split]?.[s]; return r ? [r[metric] as number, ...((r.ci95?.[metric] ?? []) as number[])] : [];
  })).filter((v) => v !== null && v !== undefined && Number.isFinite(v));
  const max = spec.max ?? niceMax(Math.max(1, ...vals) * 1.05, 10);
  const SW = 1000, rowH = 14 + runs.length * 10, h = M.t + strata.length * rowH + M.b, L = 190;
  const sx = (v: number) => L + (v / max) * (SW - L - M.r);
  return (
    <figure className="chart">
      <figcaption>{spec.label} by stratum, {split === "val" ? "validation" : "test"} sites, with 95% site-bootstrap intervals</figcaption>
      <div className="chart-wrap">
        <svg viewBox={`0 0 ${SW} ${h}`} role="img" aria-label={`${spec.label} by stratum`} className="wide-chart">
          {ticks(0, max).map((t) => <g key={t}><line x1={sx(t)} x2={sx(t)} y1={M.t} y2={h - M.b} className="grid" />
            <text x={sx(t)} y={h - M.b + 16} textAnchor="middle" className="tick">{fmt(t, spec.d === 1 ? 0 : 1)}</text></g>)}
          {strata.map((s, si) => {
            const y0 = M.t + si * rowH;
            const n = runs[0]?.d.metrics?.strata[split]?.[s]?.sites;
            return <g key={s}>
              <text x={L - 8} y={y0 + rowH / 2 + 4} textAnchor="end" className="tick">{s.replace("=", ": ")}{n ? ` (${n})` : ""}</text>
              {runs.map(({ d, i }, k) => {
                const r = d.metrics?.strata[split]?.[s]; const v = r?.[metric] as number | null | undefined;
                if (v === null || v === undefined) return null;
                const ci = r?.ci95?.[metric] as [number, number] | undefined; const y = y0 + 9 + k * 10;
                const text = `${d.name} · ${s}: ${fmt(v, spec.d)}${ci ? ` (${fmt(ci[0], spec.d)} to ${fmt(ci[1], spec.d)})` : ""} · ${r?.sites} sites`;
                return <g key={d.id} onMouseEnter={() => setTip(text)} onMouseLeave={() => setTip(null)}>
                  <rect x={L} y={y - 5} width={SW - L - M.r} height={10} fill="transparent" />
                  {ci && <line x1={sx(ci[0])} x2={sx(ci[1])} y1={y} y2={y} stroke={colourOf(i)} strokeWidth={2} />}
                  <circle cx={sx(v)} cy={y} r={4} fill={colourOf(i)} stroke="var(--panel)" strokeWidth={2} />
                </g>;
              })}
            </g>;
          })}
        </svg>
      </div>
      <p className="small muted chart-status">{tip ?? "Hover a row for its value and interval. Strata with few sites have wide intervals."}</p>
      <Legend items={runs.map(({ d, i }) => ({ label: d.name, colour: colourOf(i) }))} />
      <details className="small"><summary>Table</summary>
        <table className="table"><thead><tr><th>Stratum</th>{runs.map(({ d }) => <th key={d.id}>{d.name}</th>)}</tr></thead>
          <tbody>{strata.map((s) => <tr key={s}><td>{s}</td>{runs.map(({ d }) => {
            const r = d.metrics?.strata[split]?.[s]; const ci = r?.ci95?.[metric] as [number, number] | undefined;
            return <td key={d.id}>{fmt(r?.[metric] as number | undefined, spec.d)}{ci && <span className="muted"> ({fmt(ci[0], spec.d)}–{fmt(ci[1], spec.d)})</span>}</td>;
          })}</tr>)}</tbody></table>
      </details>
    </figure>
  );
}

export default function RunDashboard() {
  const [models, setModels] = useState<ModelVersion[] | null>(null);
  const [dash, setDash] = useState<Record<number, Dash>>({});
  const [chosen, setChosen] = useState<number[]>([]);
  const [split, setSplit] = useState<"val" | "test">("val");
  const [metric, setMetric] = useState("f1");
  const [error, setError] = useState("");

  useEffect(() => {
    api.models().then((ms) => { setModels(ms); setChosen(ms.map((m) => m.id)); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    chosen.filter((id) => !dash[id]).forEach((id) =>
      api.modelDashboard(id).then((d) => setDash((x) => ({ ...x, [id]: d }))).catch((e) => setError(e.message)));
  }, [chosen]);

  const order = (models ?? []).map((m) => m.id);
  const runs = chosen.filter((id) => dash[id]).sort((a, b) => order.indexOf(a) - order.indexOf(b))
    .map((id) => ({ d: dash[id], i: order.indexOf(id) }));
  const curveRuns = runs.filter(({ d }) => d.curves);
  // Same loss scale on every run so they compare; each run keeps its own epoch axis (fine-tunes are short).
  const lossMax = niceMax(Math.max(0.1, ...runs.flatMap(({ d }) => d.history.flatMap((h) => [h.train_loss, h.val_chip_loss]))));

  return (
    <div className="page wide">
      <div className="row gap"><h1 className="grow">Run dashboard</h1><a href="#/models" className="small">← Model registry</a></div>
      <p className="small muted">Internal research. Values are read from each run's stored records. Scores are model scores, not
        calibrated probabilities; the reliability chart shows how far they are from calibrated.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="filters row gap wrap">
        <fieldset className="row gap"><legend className="small muted">Models</legend>
          {(models ?? []).map((m, i) => <label key={m.id} className="small row gap-s">
            <input type="checkbox" checked={chosen.includes(m.id)} onChange={(e) => setChosen((c) => e.target.checked ? [...c, m.id] : c.filter((x) => x !== m.id))} />
            <span className="swatch" style={{ background: colourOf(i) }} /> {m.name}</label>)}
        </fieldset>
        <label className="small">Sites
          <select value={split} onChange={(e) => setSplit(e.target.value as "val" | "test")}>
            <option value="val">Validation (threshold chosen here)</option><option value="test">Test (locked, report only)</option>
          </select></label>
      </div>
      {runs.some(({ d }) => d.missing.length) && <p className="small badge warn">
        {runs.filter(({ d }) => d.missing.length).map(({ d }) => `${d.name}: no ${d.missing.join(", ")}`).join(" · ")}</p>}

      <section className="card">
        <h2>Training curves</h2>
        <p className="small muted">Loss per epoch: training (solid) and validation chips (dashed). The dot is the kept checkpoint, chosen by validation-chip loss.</p>
        <div className="chart-grid">
          {runs.filter(({ d }) => d.history.length).map(({ d, i }) => {
            const kept = d.history.filter((h) => h.checkpoint); const best = kept[kept.length - 1];
            return <XYChart key={d.id} title={d.name} x={[1, Math.max(2, d.history[d.history.length - 1].epoch)]} y={[0, lossMax]} xLabel="Epoch" yLabel="Loss" xFmt={(v) => fmt(v, 0)}
              series={[
                { key: "train", label: "Training", colour: colourOf(i), points: d.history.map((h) => ({ x: h.epoch, y: h.train_loss })) },
                { key: "val", label: "Validation chips", colour: colourOf(i), dash: "6 4", points: d.history.map((h) => ({ x: h.epoch, y: h.val_chip_loss })),
                  marker: best && { x: best.epoch, y: best.val_chip_loss, label: `kept: epoch ${best.epoch}` } },
              ]} />;
          })}
        </div>
      </section>

      <section className="card">
        <h2>Per-stratum metrics</h2>
        <label className="small">Metric <select value={metric} onChange={(e) => setMetric(e.target.value)}>
          {Object.entries(METRICS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></label>
        <StrataChart runs={runs} split={split} metric={metric} />
      </section>

      <section className="card">
        <h2>Precision-recall and reliability</h2>
        {curveRuns.length === 0 ? <p className="small muted">No curves stored yet. Run <code>minefoot curves &lt;run&gt; --prepared-root … --labels-root …</code>.</p> : (
          <div className="chart-grid">
            <XYChart title={`Precision-recall, pooled pixels, ${split === "val" ? "validation" : "test"}`} x={[0, 1]} y={[0, 1]} xLabel="Recall" yLabel="Precision"
              series={curveRuns.map(({ d, i }) => {
                const c = d.curves![split];
                const pts = c.pr.filter((p) => p.precision !== null).map((p) => ({ x: p.recall, y: p.precision as number }));
                const at = c.at_threshold;
                return { key: String(d.id), label: d.name, colour: colourOf(i), points: pts,
                  marker: at.precision !== null ? { x: at.recall as number, y: at.precision as number, label: `threshold ${fmt(d.metrics?.threshold_score, 2)}` } : undefined };
              })} />
            <XYChart title={`Reliability, ${split === "val" ? "validation" : "test"} (diagonal = calibrated)`} x={[0, 1]} y={[0, 1]} diagonal
              xLabel="Mean model score" yLabel="Share labelled mining"
              series={curveRuns.map(({ d, i }) => ({ key: String(d.id), label: d.name, colour: colourOf(i),
                points: d.curves![split].reliability.filter((b) => b.pixels > 0 && b.mean_score !== null)
                  .map((b) => ({ x: b.mean_score as number, y: b.labelled_fraction as number })) }))} />
          </div>
        )}
        {curveRuns.length > 0 && <details className="small"><summary>Reliability table</summary>
          <table className="table"><thead><tr><th>Score bin</th>{curveRuns.map(({ d }) => <th key={d.id}>{d.name}: pixels · share labelled</th>)}</tr></thead>
            <tbody>{curveRuns[0].d.curves![split].reliability.map((b, k) => <tr key={k}><td>{fmt(b.score_from, 1)}–{fmt(b.score_to, 1)}</td>
              {curveRuns.map(({ d }) => { const r = d.curves![split].reliability[k];
                return <td key={d.id}>{r.pixels.toLocaleString()} · {fmt(r.labelled_fraction)}</td>; })}</tr>)}</tbody></table>
        </details>}
      </section>

      <section className="card">
        <h2>Calibration study</h2>
        <p className="small muted">From <code>minefoot calibrate</code>: a map fitted on validation sites (Platt, fixed in advance; isotonic as a check),
          measured on test sites. ECE ≥ 0.1 counts only pixels predicted at 0.1 or above, so easy background cannot hide errors.
          Nothing applies these maps: outputs keep the model score.</p>
        {runs.filter(({ d }) => d.calibration).length === 0 ? <p className="small muted">No calibration study stored. Run <code>minefoot calibrate &lt;run&gt; --prepared-root … --labels-root …</code>.</p> :
          runs.filter(({ d }) => d.calibration).map(({ d }) => {
            const c = d.calibration!; const rows = c[split];
            const cell = (f: CalibrationFit) => <>{fmt(f.ece_active, 3)}{f.ci95 && <span className="muted"> ({fmt(f.ci95.ece_active[0], 3)}–{fmt(f.ci95.ece_active[1], 3)})</span>}</>;
            return <div key={d.id}>
              <h3 className="small">{d.name}: {split === "val" ? "validation (where the map was fitted)" : "test (held out)"}</h3>
              <p className="small muted">Platt: p = sigmoid({fmt(c.platt.a, 3)} · logit(score) {c.platt.b < 0 ? "−" : "+"} {fmt(Math.abs(c.platt.b), 3)}).
                At the run threshold (score {fmt(c.at_threshold.raw_score, 2)}) the Platt value is {fmt(c.at_threshold.platt, 2)}. {c.note}</p>
              <table className="table"><thead><tr><th>Stratum</th><th>Sites</th><th>ECE ≥ 0.1, raw</th><th>Platt</th><th>Isotonic</th><th>Brier raw → Platt</th></tr></thead>
                <tbody>{Object.entries(rows).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v.sites}</td><td>{cell(v.raw)}</td><td>{cell(v.platt)}</td>
                  <td>{cell(v.isotonic)}</td><td>{fmt(v.raw.brier, 3)} → {fmt(v.platt.brier, 3)}</td></tr>)}</tbody></table>
            </div>;
          })}
      </section>
    </div>
  );
}
