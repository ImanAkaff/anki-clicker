// Small hand-built SVG charts. No chart library: these two shapes are all the
// stats screen needs, and building them keeps the add-on small.
//
// Both charts follow the same recipe:
//   1. "scales" map data values to pixel positions (x for minutes, y for %).
//   2. Marks (the line, the bars) are drawn from those positions.
//   3. Hovering finds the nearest data point and shows a tooltip.
import React, { useRef, useState } from "react";

// Charts draw in a fixed coordinate space (viewBox) that the browser scales to
// fit. A full-width card uses a wider space, so its text isn't scaled up 2x.
const size = (wide) => (wide ? { W: 900, H: 240 } : { W: 440, H: 210 });
const M = { top: 14, right: 14, bottom: 30, left: 44 }; // margins for axis labels

// Round axis ends to "nice" numbers (0, 10, 20… or 0, 0.5, 1…) so the
// tick labels read cleanly instead of landing on values like 19 or -2.
function niceTicks(lo, hi, count = 3) {
  const raw = (hi - lo) / count || 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const a = Math.floor(lo / step) * step;
  const b = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = a; v <= b + step / 2; v += step) ticks.push(+v.toFixed(2));
  return ticks;
}

function Tooltip({ tip }) {
  if (!tip) return null;
  return (
    <div className="tooltip" style={{ left: tip.x, top: tip.y, transform: "translate(-50%, -110%)" }}>
      {tip.content}
    </div>
  );
}

// Line chart: x = minutes into a session, y = % slower than your warm-up pace.
export function LineChart({ points, xKey, yKey, xLabel, refY, markX, tooltip, wide }) {
  const { W, H } = size(wide);
  const [tip, setTip] = useState(null);
  const box = useRef(null);
  const xs = points.map((p) => p[xKey]);
  const ys = points.map((p) => p[yKey]);
  const xMax = Math.max(...xs, 10);
  // Small dips below 0 (a little faster than your start) don't deserve their own axis range.
  const lo = Math.min(...ys) < -5 ? Math.min(...ys) : 0;
  const yTicks = niceTicks(lo, Math.max(...ys, (refY ?? 0) * 1.5, 5));
  const yMin = yTicks[0];
  const yMax = yTicks[yTicks.length - 1];
  const x = (v) => M.left + (v / xMax) * (W - M.left - M.right);
  const y = (v) => M.top + (1 - (v - yMin) / (yMax - yMin)) * (H - M.top - M.bottom);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(p[xKey])},${y(p[yKey])}`).join(" ");

  function onMove(e) {
    const r = box.current.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    let best = points[0];
    for (const p of points) if (Math.abs(x(p[xKey]) - px) < Math.abs(x(best[xKey]) - px)) best = p;
    setTip({
      x: (x(best[xKey]) / W) * r.width,
      y: (y(best[yKey]) / H) * r.height,
      content: tooltip(best),
      px: x(best[xKey]),
    });
  }

  return (
    <div className="chart" ref={box} onMouseMove={onMove} onMouseLeave={() => setTip(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img">
        {yTicks.map((t) => (
          <g key={t}>
            <line className="gridline" x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
            <text x={M.left - 6} y={y(t) + 4} textAnchor="end">{t}%</text>
          </g>
        ))}
        {refY != null && (
          <line x1={M.left} x2={W - M.right} y1={y(refY)} y2={y(refY)}
                stroke="var(--text-3)" strokeDasharray="4 4" strokeWidth="1" />
        )}
        {markX != null && (
          <line x1={x(markX)} x2={x(markX)} y1={M.top} y2={H - M.bottom}
                stroke="var(--text-2)" strokeWidth="1" />
        )}
        <path d={path} fill="none" stroke="var(--series)" strokeWidth="2" strokeLinejoin="round" />
        {points.map((p) => (
          <circle key={p[xKey]} cx={x(p[xKey])} cy={y(p[yKey])} r="4"
                  fill="var(--series)" stroke="var(--surface)" strokeWidth="2" />
        ))}
        {tip && <line x1={tip.px} x2={tip.px} y1={M.top} y2={H - M.bottom} stroke="var(--text-3)" strokeWidth="1" />}
        {xs.map((v) => (
          <text key={v} x={x(v)} y={H - M.bottom + 16} textAnchor="middle">{v}</text>
        ))}
        <text x={W - M.right} y={H - 2} textAnchor="end">{xLabel}</text>
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

// Bar chart: one bar per category (buttons, times of day).
// Bars with no value draw as an empty dashed slot labelled "–".
export function BarChart({ items, labelKey, valueKey, unit, highlightKey, tooltip, wide }) {
  const { W, H } = size(wide);
  const [tip, setTip] = useState(null);
  const box = useRef(null);
  const vals = items.map((d) => d[valueKey] ?? 0);
  const yTicks = niceTicks(0, Math.max(...vals, 0.1), 2);
  const yMax = yTicks[yTicks.length - 1];
  const band = (W - M.left - M.right) / items.length;
  const bw = Math.min(56, band * 0.55);
  const y = (v) => M.top + (1 - v / yMax) * (H - M.top - M.bottom);
  const base = y(0);

  return (
    <div className="chart" ref={box} onMouseLeave={() => setTip(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img">
        {yTicks.map((t) => (
          <g key={t}>
            <line className="gridline" x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
            <text x={M.left - 6} y={y(t) + 4} textAnchor="end">{t}{unit}</text>
          </g>
        ))}
        {items.map((d, i) => {
          const cx = M.left + band * i + band / 2;
          const v = d[valueKey];
          const top = v == null ? base : y(v);
          const hl = highlightKey && d[labelKey] === highlightKey;
          return (
            <g key={d[labelKey]}
               onMouseEnter={(e) => {
                 const r = box.current.getBoundingClientRect();
                 setTip({ x: (cx / W) * r.width, y: (top / H) * r.height, content: tooltip(d) });
               }}>
              {/* invisible hit area, bigger than the bar, so hovering is easy */}
              <rect x={cx - band / 2} y={M.top} width={band} height={H - M.top - M.bottom} fill="transparent" />
              {v == null ? (
                <rect x={cx - bw / 2} y={base - 24} width={bw} height={24} rx="4"
                      fill="none" stroke="var(--border)" strokeDasharray="3 3" />
              ) : (
                // Rounded top corners only: a path keeps the bar flat on the baseline.
                <path
                  d={`M${cx - bw / 2},${base} V${top + 4} Q${cx - bw / 2},${top} ${cx - bw / 2 + 4},${top}
                      H${cx + bw / 2 - 4} Q${cx + bw / 2},${top} ${cx + bw / 2},${top + 4} V${base} Z`}
                  fill={hl ? "var(--series)" : "var(--series-soft)"}
                />
              )}
              <text x={cx} y={v == null ? base - 30 : top - 6} textAnchor="middle"
                    style={{ fill: "var(--text-2)" }}>
                {v == null ? "–" : `${v}${unit}`}
              </text>
              <text x={cx} y={H - M.bottom + 16} textAnchor="middle">{d[labelKey]}</text>
            </g>
          );
        })}
        <line className="axis" x1={M.left} x2={W - M.right} y1={base} y2={base} />
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

// Day-by-day chart: one slot per calendar day, x is just the day's position.
// Days with no reviews have no value; the line breaks there instead of
// drawing a misleading straight line across the gap.
export function DayChart({ points, yKey, unit, tooltip }) {
  const { W, H } = size(true);
  const [tip, setTip] = useState(null);
  const box = useRef(null);
  const vals = points.map((p) => p[yKey]).filter((v) => v != null);
  const yTicks = niceTicks(0, Math.max(...vals, unit === "%" ? 10 : 1), 3);
  const yMax = yTicks[yTicks.length - 1];
  const n = points.length;
  const x = (i) => M.left + (n === 1 ? 0.5 : i / (n - 1)) * (W - M.left - M.right);
  const y = (v) => M.top + (1 - v / yMax) * (H - M.top - M.bottom);

  // Build the line in pieces, starting a new piece after each empty day.
  let d = "", pen = false;
  points.forEach((p, i) => {
    const v = p[yKey];
    if (v == null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(i)},${y(v)} `;
    pen = true;
  });
  // About 7 date labels, whatever the range, so they never overlap.
  const every = Math.max(1, Math.ceil(n / 7));

  function onMove(e) {
    const r = box.current.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - M.left) / (W - M.left - M.right)) * (n - 1))));
    const p = points[i];
    const v = p[yKey];
    setTip({
      x: (x(i) / W) * r.width,
      y: ((v == null ? y(0) : y(v)) / H) * r.height,
      content: tooltip(p),
      px: x(i),
    });
  }

  return (
    <div className="chart" ref={box} onMouseMove={onMove} onMouseLeave={() => setTip(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img">
        {yTicks.map((t) => (
          <g key={t}>
            <line className="gridline" x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
            <text x={M.left - 6} y={y(t) + 4} textAnchor="end">{t}{unit}</text>
          </g>
        ))}
        {tip && <line x1={tip.px} x2={tip.px} y1={M.top} y2={H - M.bottom} stroke="var(--text-3)" strokeWidth="1" />}
        <path d={d} fill="none" stroke="var(--series)" strokeWidth="2" strokeLinejoin="round" />
        {points.map((p, i) => p[yKey] != null && (
          <circle key={p.date} cx={x(i)} cy={y(p[yKey])} r={n > 40 ? 2.5 : 4}
                  fill="var(--series)" stroke="var(--surface)" strokeWidth="2" />
        ))}
        {points.map((p, i) => (n - 1 - i) % every === 0 && (
          <text key={p.date} x={x(i)} y={H - M.bottom + 16}
                textAnchor={i === n - 1 ? "end" : i === 0 ? "start" : "middle"}>{p.label}</text>
        ))}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}
