// The stats screen. It receives one JSON object from Python (analytics.py)
// and only draws it: no maths happens here beyond formatting.
import React, { useEffect, useState } from "react";
import { BarChart, LineChart } from "./charts.jsx";

function useAnkiData() {
  const [data, setData] = useState(window.__ankiClickerData || null);
  const [, force] = useState(0);
  useEffect(() => {
    const on = () => { setData(window.__ankiClickerData || null); force((n) => n + 1); };
    window.addEventListener("ankiclicker:data", on);
    return () => window.removeEventListener("ankiclicker:data", on);
  }, []);
  return data;
}

const fmtMin = (m) => (m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`);

function Tile({ label, value, unit }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value ?? "–"}{value != null && unit ? <span className="unit">{unit}</span> : null}</div>
    </div>
  );
}

// Shown in place of an insight until there's enough data to trust it.
function Waiting({ needs, have, total }) {
  const pct = total ? Math.min(100, Math.round((have / total) * 100)) : 0;
  return (
    <div className="waiting">
      Unlocks after {needs}. You have {have} so far.
      <div className="bar-track"><div className="bar-fill" style={{ width: `${pct}%` }} /></div>
    </div>
  );
}
const target = (needs) => parseInt(needs, 10) || 1;

function Fatigue({ f }) {
  if (!f.ready) return <Waiting needs={f.needs} have={f.have} total={target(f.needs)} />;
  const pts = f.points;
  const at = f.slowdown_minute;
  const start = pts.find((p) => p.minute === 0);
  const peak = pts.reduce((a, b) => (b.slowdown_pct > a.slowdown_pct ? b : a), pts[0]);
  return (
    <>
      <LineChart
        points={pts} xKey="minute" yKey="slowdown_pct" xLabel="minutes into session"
        refY={f.threshold_pct} markX={at} wide
        tooltip={(p) => (
          <>
            <div><strong>Minute {p.minute}–{p.minute + 5}</strong></div>
            <div>{p.slowdown_pct > 0 ? `${p.slowdown_pct}% slower` : `${Math.abs(p.slowdown_pct)}% faster`} than your start</div>
            <div className="muted">Again rate {p.again_rate_pct}% · {p.n} cards</div>
          </>
        )}
      />
      <p className="callout">
        {at != null ? (
          <>You start slowing down around <strong>minute {at}</strong>, reaching {peak.slowdown_pct}% slower by minute {peak.minute}.
            {" "}Try sessions of about <strong>{Math.max(15, at - 5)}–{at} minutes</strong> with a short break.
            {start && peak.again_rate_pct > start.again_rate_pct + 2 &&
              <> Your Again rate also rises from {start.again_rate_pct}% to {peak.again_rate_pct}%.</>}</>
        ) : (
          <>No clear slowdown yet across {f.sessions_used} long sessions. You hold your pace well,
            or sessions haven't run long enough to show it.</>
        )}
      </p>
    </>
  );
}

function Shaky({ s }) {
  if (!s.ready) return <Waiting needs={s.needs} have={s.have} total={target(s.needs)} />;
  if (!s.cards.length) return <p className="waiting">No shaky cards right now. Your Good answers are confident.</p>;
  return (
    <>
      <table>
        <thead>
          <tr><th>Card</th><th className="num">Hesitated</th><th className="num">Reviews</th></tr>
        </thead>
        <tbody>
          {s.cards.map((c) => (
            <tr key={c.card_id}>
              <td>{c.text}{c.deck && <div className="deck">{c.deck}</div>}</td>
              <td className="num">{c.hesitation_s}s before {c.button}</td>
              <td className="num">{c.times_reviewed}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="callout">
        {s.count} card{s.count === 1 ? "" : "s"} where you pressed Good or Easy but hesitated {s.cutoff_s}s or more,
        your slowest 20%. Worth a closer look before they slip. The top {s.cards.length} are shown.
      </p>
    </>
  );
}

function Predicts({ p }) {
  if (!p.ready) return <Waiting needs={p.needs} have={p.have} total={target(p.needs)} />;
  const max = Math.max(p.slow_forgot_pct, p.fast_forgot_pct, 1);
  const diff = p.slow_forgot_pct - p.fast_forgot_pct;
  return (
    <>
      <div className="compare">
        <span className="lbl">Slow Good (≥{p.split_s}s)</span>
        <div className="hbar" style={{ width: `${(p.slow_forgot_pct / max) * 100}%` }} />
        <span className="val">{p.slow_forgot_pct}%</span>
      </div>
      <div className="compare">
        <span className="lbl">Fast Good (&lt;{p.split_s}s)</span>
        <div className="hbar soft" style={{ width: `${(p.fast_forgot_pct / max) * 100}%` }} />
        <span className="val">{p.fast_forgot_pct}%</span>
      </div>
      <p className="callout">
        {diff >= 3 ? (
          <>Yes, for you: after a <strong>slow</strong> Good you pressed Again next time {p.slow_forgot_pct}% of the time,
            vs {p.fast_forgot_pct}% after a fast one. Hesitation is a real warning sign.</>
        ) : (
          <>Not much difference so far ({p.slow_forgot_pct}% vs {p.fast_forgot_pct}%). Your hesitation doesn't
            predict forgetting yet.</>
        )}
        {" "}Based on {p.pairs} cards you saw again{p.pairs < 150 ? ", so treat it as early evidence" : ""}.
      </p>
    </>
  );
}

function TimeOfDay({ t }) {
  if (!t.blocks.some((b) => b.enough)) {
    const most = Math.max(...t.blocks.map((b) => b.reviews));
    return <Waiting needs={`${t.min_per_block} reviews in one time of day`} have={most} total={t.min_per_block} />;
  }
  const items = t.blocks.map((b) => ({ ...b, value: b.enough ? b.again_rate_pct : null }));
  return (
    <>
      <BarChart
        items={items} labelKey="block" valueKey="value" unit="%" highlightKey={t.best}
        tooltip={(b) => b.enough ? (
          <><div><strong>{b.block}</strong></div><div>Again rate {b.again_rate_pct}%</div>
            <div className="muted">{b.median_s_per_card}s per card · {b.reviews} cards</div></>
        ) : (
          <><div><strong>{b.block}</strong></div><div className="muted">{b.reviews} of {t.min_per_block} cards needed</div></>
        )}
      />
      <p className="callout">
        {t.best ? <>Fewest mistakes in the <strong>{t.best.toLowerCase()}</strong>. Lower is better here.</>
                : <>Needs {t.min_per_block}+ reviews in at least two times of day to compare.</>}
      </p>
    </>
  );
}

function ByButton({ b }) {
  return (
    <>
      <BarChart
        items={b} labelKey="label" valueKey="median_hesitation_s" unit="s" wide
        tooltip={(d) => (
          <><div><strong>{d.label}</strong></div>
            <div>{d.median_hesitation_s ?? "–"}s typical hesitation</div>
            <div className="muted">{d.count} presses</div></>
        )}
      />
      <p className="callout">Typical pause between seeing the answer and pressing each button.
        Long pauses before Hard or Again are normal; long pauses before Good are the shaky ones.</p>
    </>
  );
}

export default function App() {
  const data = useAnkiData();
  useEffect(() => {
    if (data?.meta) document.documentElement.dataset.theme = data.meta.night_mode ? "dark" : "light";
  }, [data]);

  if (!data && window.__ankiClickerTimeout) {
    return (
      <div className="empty">
        Couldn't load your stats.<br />
        Close this window and open Tools → Anki Clicker → Stats again. If it keeps happening,
        send Azeem this: <code>bridge={String(window.__ankiClickerTimeout.hasBridge)}</code>
      </div>
    );
  }
  if (!data) return <div className="empty">Loading your stats…</div>;
  const o = data.overview;
  const m = data.meta || {};
  if (!o.reviews) return <div className="empty">No reviews logged yet. Do a few reviews and come back.</div>;

  return (
    <div className="wrap">
      <header>
        <h1>Your study insights</h1>
        <span className="pill">
          {m.connected ? (m.unsynced ? `${m.unsynced} waiting to upload` : "All synced") : "Not connected"}
        </span>
      </header>

      <section className="tiles">
        <Tile label="Reviews" value={o.reviews.toLocaleString()} />
        <Tile label="Study time" value={fmtMin(o.study_minutes)} />
        <Tile label="Typical think time" value={o.median_think_s} unit="s" />
        <Tile label="Typical hesitation" value={o.median_hesitation_s} unit="s" />
        <Tile label="Again rate" value={o.again_rate_pct} unit="%" />
      </section>

      <section className="card">
        <h2>Your fatigue curve</h2>
        <p className="sub">How much slower you get as a session goes on, compared with your first 10 minutes.
          Dashed line: {data.fatigue.threshold_pct ?? 10}% slower.</p>
        <Fatigue f={data.fatigue} />
      </section>

      <section className="card">
        <h2>Shaky cards</h2>
        <p className="sub">Right answers that took you a long time to commit to.</p>
        <Shaky s={data.shaky} />
      </section>

      <div className="grid2">
        <section className="card">
          <h2>Does hesitating predict forgetting?</h2>
          <p className="sub">How often you pressed Again the next time you saw a card.</p>
          <Predicts p={data.predicts} />
        </section>
        <section className="card">
          <h2>Best time of day</h2>
          <p className="sub">Again rate by when you studied.</p>
          <TimeOfDay t={data.time_of_day} />
        </section>
      </div>

      <section className="card">
        <h2>Hesitation by button</h2>
        <ByButton b={data.by_button} />
      </section>

      <footer>
        Computed on this computer from {o.sessions} session{o.sessions === 1 ? "" : "s"}. Card text never leaves it; only timings are uploaded.
        {m.last_sync ? ` Last sync: ${m.last_sync}` : ""}
      </footer>
    </div>
  );
}
