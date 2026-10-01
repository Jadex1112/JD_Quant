// TradingView's own chart, embedded as a cross-origin frame. Its prices come from TradingView, not from
// this app's brokers, and it cannot place orders here. TradingView's script never runs inside this app's
// page (so it cannot reach the signed-in API); only its frame does, under TradingView's own origin.

const WIDGET = "https://www.tradingview-widget.com/embed-widget/advanced-chart/?locale=en#";

const FUTURE = /^(MCX|NSE):([A-Z][A-Z0-9&_-]*?)\d{2}[A-Z]{3}FUT$/;
const INDEX: Record<string, string> = {
  "NSE:NIFTY50-INDEX": "NSE:NIFTY",
  "NSE:NIFTYBANK-INDEX": "NSE:BANKNIFTY",
};

/** This app's instrument id as a TradingView symbol (best effort; TradingView's search can fix the rest). */
export function tradingViewSymbol(instrumentId: string): string {
  if (INDEX[instrumentId]) return INDEX[instrumentId];
  const future = FUTURE.exec(instrumentId);
  if (future) return `${future[1]}:${future[2]}1!`; // the continuous front-month contract
  const [venue, symbol = ""] = instrumentId.split(":");
  switch (venue) {
    case "NSE":
    case "BSE":
      return `${venue}:${symbol.replace(/-(EQ|BE|BZ|SM|ST)$/, "")}`;
    case "OANDA":
      return `OANDA:${symbol.replace("_", "")}`;
    case "ALPACA":
      return symbol;
    default:
      return `${venue}:${symbol.replaceAll("/", "")}`;
  }
}

export function tradingViewUrl(instrumentId: string): string {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tradingViewSymbol(instrumentId))}`;
}

export function TradingViewChart({ instrumentId, height = 480 }: { instrumentId: string; height?: number }) {
  const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const config = {
    autosize: true,
    symbol: tradingViewSymbol(instrumentId),
    interval: "15",
    timezone: "Asia/Kolkata",
    theme,
    style: "1",
    locale: "en",
    allow_symbol_change: true,
    withdateranges: true,
    hide_side_toolbar: false,
    details: true,
    calendar: false,
    support_host: "https://www.tradingview.com",
  };
  return (
    <iframe
      key={`${instrumentId}-${theme}`}
      title={`TradingView chart for ${config.symbol}`}
      src={WIDGET + encodeURIComponent(JSON.stringify(config))}
      style={{ width: "100%", height, border: 0, display: "block" }}
      referrerPolicy="origin"
      sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-forms"
      allow="fullscreen; clipboard-write"
    />
  );
}
