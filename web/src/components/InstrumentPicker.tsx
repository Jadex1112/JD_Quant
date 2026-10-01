import { useEffect, useId, useState } from "react";
import type { Instrument } from "../api";

/**
 * A type-to-search instrument box. A broker connection can add thousands of instruments (every NSE stock on
 * Fyers), which a plain dropdown cannot handle, so this filters as you type: "reli" finds NSE:RELIANCE-EQ.
 * The value only changes when the text matches an instrument; anything else reverts on blur.
 */
export function InstrumentPicker({
  instruments,
  value,
  onChange,
  id,
  label = "Instrument",
}: {
  instruments: Instrument[];
  value: string;
  onChange: (instrumentId: string) => void;
  id?: string;
  label?: string;
}) {
  const listId = useId();
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);

  const byId = new Map(instruments.map((i) => [i.instrument_id.toUpperCase(), i.instrument_id]));
  const pick = (raw: string) => {
    setText(raw);
    const match = byId.get(raw.trim().toUpperCase());
    if (match && match !== value) onChange(match);
  };
  return (
    <>
      <input
        id={id}
        list={listId}
        value={text}
        aria-label={id ? undefined : label}
        placeholder="Type to search, e.g. RELIANCE"
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => pick(e.target.value)}
        onFocus={(e) => e.target.select()}
        onBlur={() => setText(value)}
      />
      <datalist id={listId}>
        {instruments.map((i) => (
          <option key={i.instrument_id} value={i.instrument_id}>
            {`${i.symbol} · ${i.venue} · ${i.asset_class.replaceAll("_", " ").toLowerCase()}`}
          </option>
        ))}
      </datalist>
    </>
  );
}
