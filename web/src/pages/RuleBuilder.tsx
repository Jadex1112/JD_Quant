import { useState } from "react";
import { get } from "../api";
import { useData } from "../components/ui";

// A no-code editor for the strategy lab's rule spec: pick indicators, comparisons and risk settings from
// lists. It produces the same JSON the AI writes; the server validates it before anything runs.

interface Vocabulary {
  indicators: Record<string, { params: Record<string, { default: number; min: number; max: number }>; choice: { key: string; options: string[] } | null }>;
  ops: string[];
}

type Operand = Record<string, unknown>; // {"value": n} or {"ind": name, ...params, "offset": n}
interface Row {
  left: Operand;
  op: string;
  right: Operand;
  bars: number;
}
interface SideRules {
  mode: "all" | "any";
  rows: Row[];
}
export interface BuilderState {
  name: string;
  sides: Record<string, SideRules>;
  stop_loss_pct: string;
  take_profit_pct: string;
  trailing_stop_pct: string;
  size_pct: string;
  exit_after_bars: string;
  flat_at_cutoff: boolean;
}

const SIDE_LABELS: [string, string][] = [
  ["long_entry", "Buy (go long) when"],
  ["long_exit", "Sell the long position when"],
  ["short_entry", "Sell short when"],
  ["short_exit", "Buy back the short when"],
];

const OP_LABELS: Record<string, string> = {
  ">": "is above",
  "<": "is below",
  ">=": "is at or above",
  "<=": "is at or below",
  crosses_above: "crosses above",
  crosses_below: "crosses below",
  rising: "is rising for",
  falling: "is falling for",
};

const IND_LABELS: Record<string, string> = {
  close: "Close", open: "Open", high: "High", low: "Low", volume: "Volume", sma: "SMA", ema: "EMA", rsi: "RSI",
  macd: "MACD", bb: "Bollinger band", atr: "ATR", highest: "Highest", lowest: "Lowest", roc: "Rate of change",
  stoch: "Stochastic", zscore: "Z-score", supertrend: "Supertrend", vwap: "VWAP",
};

const emptyRow = (): Row => ({ left: { ind: "close" }, op: ">", right: { ind: "ema", period: 50 }, bars: 3 });

export function emptyBuilder(): BuilderState {
  return {
    name: "My strategy",
    sides: {
      long_entry: { mode: "all", rows: [{ left: { ind: "ema", period: 20 }, op: "crosses_above", right: { ind: "ema", period: 50 }, bars: 3 }] },
      long_exit: { mode: "any", rows: [{ left: { ind: "ema", period: 20 }, op: "crosses_below", right: { ind: "ema", period: 50 }, bars: 3 }] },
      short_entry: { mode: "all", rows: [] },
      short_exit: { mode: "any", rows: [] },
    },
    stop_loss_pct: "2",
    take_profit_pct: "",
    trailing_stop_pct: "",
    size_pct: "100",
    exit_after_bars: "",
    flat_at_cutoff: false,
  };
}

const isComparison = (c: unknown): c is { left: Operand; op: string; right?: Operand; bars?: number } =>
  !!c && typeof c === "object" && "op" in (c as object) && "left" in (c as object);

/** Load a spec into the builder; null when it uses nested groups or "not", which only the JSON editor shows. */
export function fromSpec(spec: Record<string, unknown>): BuilderState | null {
  const state = emptyBuilder();
  state.name = String(spec.name ?? "Typed strategy");
  for (const [side] of SIDE_LABELS) {
    const cond = spec[side] as Record<string, unknown> | null | undefined;
    if (!cond) {
      state.sides[side] = { mode: side.endsWith("exit") ? "any" : "all", rows: [] };
      continue;
    }
    const mode = "all" in cond ? "all" : "any" in cond ? "any" : null;
    const items = mode ? (cond[mode] as unknown[]) : [cond];
    if (!items.every(isComparison)) return null;
    state.sides[side] = {
      mode: mode ?? "all",
      rows: items.map((c) => ({ left: c.left, op: c.op, right: c.right ?? { value: 0 }, bars: c.bars ?? 3 })),
    };
  }
  const num = (k: string) => (spec[k] == null ? "" : String(spec[k]));
  state.stop_loss_pct = num("stop_loss_pct");
  state.take_profit_pct = num("take_profit_pct");
  state.trailing_stop_pct = num("trailing_stop_pct");
  state.size_pct = num("size_pct") || "100";
  state.exit_after_bars = num("exit_after_bars");
  state.flat_at_cutoff = Boolean(spec.flat_at_cutoff);
  return state;
}

export function toSpec(state: BuilderState): Record<string, unknown> {
  const spec: Record<string, unknown> = { name: state.name };
  for (const [side] of SIDE_LABELS) {
    const { mode, rows } = state.sides[side];
    if (!rows.length) continue;
    const conds = rows.map((r) =>
      r.op === "rising" || r.op === "falling" ? { left: r.left, op: r.op, bars: r.bars } : { left: r.left, op: r.op, right: r.right },
    );
    spec[side] = conds.length === 1 ? conds[0] : { [mode]: conds };
  }
  for (const k of ["stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "size_pct", "exit_after_bars"] as const) {
    if (state[k] !== "") spec[k] = Number(state[k]);
  }
  if (state.flat_at_cutoff) spec.flat_at_cutoff = true;
  return spec;
}

function OperandEditor({
  value,
  vocab,
  allowNumber,
  onChange,
  label,
}: {
  value: Operand;
  vocab: Vocabulary;
  allowNumber: boolean;
  onChange: (o: Operand) => void;
  label: string;
}) {
  const isValue = "value" in value;
  const ind = isValue ? "value" : String(value.ind);
  const meta = isValue ? null : vocab.indicators[ind];
  return (
    <span className="row" style={{ gap: 4 }}>
      <select
        aria-label={`${label} indicator`}
        value={ind}
        onChange={(e) => {
          const next = e.target.value;
          if (next === "value") return onChange({ value: 0 });
          const m = vocab.indicators[next];
          const o: Operand = { ind: next };
          Object.entries(m.params).forEach(([k, p]) => (o[k] = p.default));
          if (m.choice) o[m.choice.key] = m.choice.options[0];
          onChange(o);
        }}
      >
        {allowNumber && <option value="value">Number</option>}
        {Object.keys(vocab.indicators).map((k) => (
          <option key={k} value={k}>{IND_LABELS[k] ?? k}</option>
        ))}
      </select>
      {isValue ? (
        <input aria-label={`${label} number`} type="number" step="any" style={{ width: 90 }} value={String(value.value)} onChange={(e) => onChange({ value: Number(e.target.value) })} />
      ) : (
        <>
          {meta &&
            Object.entries(meta.params).map(([k, p]) => (
              <label key={k} className="field inline small" title={`${p.min}–${p.max}`}>
                {k}
                <input
                  type="number"
                  step="any"
                  min={p.min}
                  max={p.max}
                  style={{ width: 64 }}
                  value={String(value[k] ?? p.default)}
                  onChange={(e) => onChange({ ...value, [k]: Number(e.target.value) })}
                />
              </label>
            ))}
          {meta?.choice && (
            <select aria-label={`${label} ${meta.choice.key}`} value={String(value[meta.choice.key] ?? meta.choice.options[0])} onChange={(e) => onChange({ ...value, [meta.choice!.key]: e.target.value })}>
              {meta.choice.options.map((o) => <option key={o}>{o}</option>)}
            </select>
          )}
          <label className="field inline small" title="Compare with the value this many bars ago">
            bars ago
            <input type="number" min={0} max={200} style={{ width: 52 }} value={String(value.offset ?? 0)} onChange={(e) => onChange({ ...value, offset: Math.max(0, Math.round(Number(e.target.value))) })} />
          </label>
        </>
      )}
    </span>
  );
}

export function RuleBuilder({ state, onChange }: { state: BuilderState; onChange: (s: BuilderState) => void }) {
  const vocab = useData(() => get<Vocabulary>("/strategy-lab/vocabulary"), []);
  const [open, setOpen] = useState<Record<string, boolean>>({ long_entry: true, long_exit: true });
  const v = vocab.data;
  if (!v) return <p className="muted small">Loading indicators…</p>;
  const setSide = (side: string, rules: SideRules) => onChange({ ...state, sides: { ...state.sides, [side]: rules } });
  const setRow = (side: string, i: number, row: Row) => {
    const rows = [...state.sides[side].rows];
    rows[i] = row;
    setSide(side, { ...state.sides[side], rows });
  };
  const field = (k: "stop_loss_pct" | "take_profit_pct" | "trailing_stop_pct" | "size_pct" | "exit_after_bars", label: string) => (
    <label className="field">
      {label}
      <input type="number" step="any" min={0} value={state[k]} onChange={(e) => onChange({ ...state, [k]: e.target.value })} placeholder="off" />
    </label>
  );
  return (
    <div className="stack">
      <label className="field">
        Name
        <input value={state.name} onChange={(e) => onChange({ ...state, name: e.target.value })} />
      </label>
      {SIDE_LABELS.map(([side, label]) => {
        const rules = state.sides[side];
        const shown = open[side] || rules.rows.length > 0;
        return (
          <fieldset key={side} className="card" style={{ boxShadow: "none", margin: 0 }}>
            <legend className="small"><strong>{label}</strong></legend>
            {!shown ? (
              <button type="button" className="small" onClick={() => setOpen({ ...open, [side]: true })}>+ Add a rule</button>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {rules.rows.length > 1 && (
                  <div className="segmented" role="group" aria-label={`${label}: combine rules`} style={{ alignSelf: "flex-start" }}>
                    <button type="button" className="small" aria-pressed={rules.mode === "all"} onClick={() => setSide(side, { ...rules, mode: "all" })}>all of these</button>
                    <button type="button" className="small" aria-pressed={rules.mode === "any"} onClick={() => setSide(side, { ...rules, mode: "any" })}>any of these</button>
                  </div>
                )}
                {rules.rows.map((row, i) => (
                  <div key={i} className="row" style={{ borderLeft: "2px solid var(--border)", paddingLeft: 8 }}>
                    <OperandEditor label={`${label} rule ${i + 1} left`} value={row.left} vocab={v} allowNumber={false} onChange={(left) => setRow(side, i, { ...row, left })} />
                    <select aria-label={`${label} rule ${i + 1} comparison`} value={row.op} onChange={(e) => setRow(side, i, { ...row, op: e.target.value })}>
                      {v.ops.map((op) => <option key={op} value={op}>{OP_LABELS[op] ?? op}</option>)}
                    </select>
                    {row.op === "rising" || row.op === "falling" ? (
                      <label className="field inline small">
                        <input type="number" min={1} max={200} style={{ width: 56 }} value={row.bars} onChange={(e) => setRow(side, i, { ...row, bars: Math.max(1, Math.round(Number(e.target.value))) })} />
                        bars
                      </label>
                    ) : (
                      <OperandEditor label={`${label} rule ${i + 1} right`} value={row.right} vocab={v} allowNumber onChange={(right) => setRow(side, i, { ...row, right })} />
                    )}
                    <button type="button" className="small" aria-label={`Remove ${label} rule ${i + 1}`} onClick={() => setSide(side, { ...rules, rows: rules.rows.filter((_, j) => j !== i) })}>✕</button>
                  </div>
                ))}
                <div>
                  <button type="button" className="small" onClick={() => setSide(side, { ...rules, rows: [...rules.rows, emptyRow()] })}>+ Add a rule</button>
                </div>
              </div>
            )}
          </fieldset>
        );
      })}
      <div className="form-grid">
        {field("stop_loss_pct", "Stop-loss %")}
        {field("take_profit_pct", "Take-profit %")}
        {field("trailing_stop_pct", "Trailing stop %")}
        {field("size_pct", "Position size % of capital")}
        {field("exit_after_bars", "Exit after N bars")}
      </div>
      <label className="field inline">
        <input type="checkbox" checked={state.flat_at_cutoff} onChange={(e) => onChange({ ...state, flat_at_cutoff: e.target.checked })} />
        Close positions at the market's intraday cutoff
      </label>
    </div>
  );
}
