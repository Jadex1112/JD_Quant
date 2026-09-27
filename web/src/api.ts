// Thin client for /api/v1. Sessions use an HttpOnly cookie; unsafe methods echo the CSRF token.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public detail: string,
    public body: Record<string, unknown> = {},
  ) {
    super(detail || code);
  }

  get fieldErrors(): { field: string; message: string }[] {
    return (this.body.errors as { field: string; message: string }[]) ?? [];
  }
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)jq_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

type Listener = (error: ApiError) => void;
const unauthenticatedListeners = new Set<Listener>();
export function onUnauthenticated(listener: Listener): () => void {
  unauthenticatedListeners.add(listener);
  return () => unauthenticatedListeners.delete(listener);
}

export async function api<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  const init: RequestInit = {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRF-Token": csrfToken() } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  };
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, init);
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "The server could not be reached.");
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!response.ok) {
    const error = new ApiError(
      response.status,
      data?.code ?? `HTTP_${response.status}`,
      data?.detail ?? response.statusText,
      data ?? {},
    );
    if (response.status === 401 && !path.startsWith("/auth/")) {
      unauthenticatedListeners.forEach((l) => l(error));
    }
    throw error;
  }
  return data as T;
}

export const get = <T,>(path: string) => api<T>("GET", path);
export const post = <T,>(path: string, body?: unknown, headers?: Record<string, string>) =>
  api<T>("POST", path, body ?? {}, headers);
export const put = <T,>(path: string, body: unknown) => api<T>("PUT", path, body);
export const patch = <T,>(path: string, body: unknown) => api<T>("PATCH", path, body);
export const del = <T,>(path: string) => api<T>("DELETE", path);

// ---- API types ----------------------------------------------------------------------------

export interface Me {
  user_id: string;
  email: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  mfa_enabled: boolean;
  mfa_recent: boolean;
  auth_method: string;
}

export interface Health {
  status: string;
  version: string;
  mode: string;
  maintenance_mode: boolean;
}

export interface Account {
  account_id: string;
  name: string;
  venue: string;
  mode: "LIVE" | "PAPER";
  base_currency: string;
  status: string;
  markets: string[]; // instrument venues it can trade; ["*"] for paper accounts
}

export function accountTrades(account: Account | undefined, instrument: Instrument): boolean {
  return !account || account.markets.includes("*") || account.markets.includes(instrument.venue);
}

export interface Instrument {
  instrument_id: string;
  venue: string;
  symbol: string;
  asset_class: string;
  base_asset: string;
  quote_asset: string;
  tick_size: string;
  lot_size: string;
  min_quantity: string;
  min_notional: string | null;
  status: string;
  reference_price: string | null;
  feed_status: string;
}

export interface Order {
  order_id: string;
  client_order_id: string;
  account_id: string;
  deployment_id: string;
  instrument_id: string;
  side: "BUY" | "SELL";
  order_type: string;
  time_in_force: string;
  quantity: string;
  limit_price: string | null;
  status: string;
  filled_quantity: string;
  remaining_quantity: string;
  average_fill_price: string | null;
  fees: Record<string, string>;
  reject_code: string | null;
  reject_reason: string | null;
  source: string;
  created_at: string;
}

export interface Position {
  account_id: string;
  instrument_id: string;
  deployment_id: string;
  quantity: string;
  average_entry_price: string;
  realized_pnl: string;
  unrealized_pnl: string | null;
  fees_paid: string;
}

export interface Fill {
  fill_id: string;
  order_id: string;
  account_id: string;
  instrument_id: string;
  side: string;
  price: string;
  quantity: string;
  fee: string;
  fee_asset: string;
  liquidity: string;
  exchange_ts: string;
}

export interface KillSwitch {
  kill_switch_id: string;
  scope: string;
  target_id: string | null;
  action: string;
  reason: string;
  triggered_by: string;
  triggered_at: string;
  active: boolean;
  released_by: string | null;
  released_at: string | null;
}

export interface Deployment {
  deployment_id: string;
  strategy_name: string;
  strategy_version: string;
  account_id: string;
  mode: string;
  parameters: Record<string, unknown>;
  instruments: string[];
  state: string;
  state_reason: string | null;
  created_by: string;
  approved_by: string | null;
}

export interface StrategyTemplate {
  name: string;
  version: string;
  description: string;
  parameters: Record<string, { type: string; default: unknown; min: string | null; max: string | null; description: string }>;
}

export interface Backtest {
  reproducibility_hash: string;
  final_equity: string;
  metrics: Record<string, number | null>;
  order_count: number;
  fill_count: number;
  trades: {
    instrument_id: string;
    direction: string;
    quantity: string;
    entry_time: string;
    exit_time: string;
    entry_price: string;
    exit_price: string;
    net_pnl: string;
  }[];
  equity_curve: [string, string][];
  assumptions: string[];
}

export interface RiskLimit {
  limit_type: string;
  threshold: string;
  action: string;
}

export interface RiskProfile {
  name: string;
  scope: string;
  target_id: string | null;
  limits: RiskLimit[];
  restricted_instruments: string[];
  active: boolean;
}

export interface Connection {
  connection_id: string;
  name: string;
  venue: string;
  environment: string;
  account_id: string | null;
  has_credentials: boolean;
  status: string;
  last_error: string | null;
  last_tested_at: string | null;
  instrument_count: number;
  watchlist: string[];
  settings: Record<string, unknown>;
  requires_login: boolean;
  session_expires_at: string | null;
  markets: string[];
}

export interface User {
  user_id: string;
  email: string;
  display_name: string;
  roles: string[];
  status: string;
  mfa_enabled: boolean;
  last_login_at: string | null;
}

export interface AuditEvent {
  seq: number;
  at: string;
  actor: string;
  action: string;
  category: string;
  target: string | null;
  outcome: string;
  data: Record<string, unknown>;
}

export interface ModelVersion {
  model: string;
  version: number;
  stage: string;
  algorithm: string;
  features: string[];
  instrument_id: string | null;
  created_by: string;
  created_at: string;
  rollback_target: number | null;
  model_card: string;
  report: {
    metrics?: { train?: Record<string, number | null>; test?: Record<string, number | null> };
    warnings?: string[];
    samples?: Record<string, number>;
  };
}

export interface TranscriptEntry {
  kind: "user" | "assistant" | "tool" | "action" | "notice";
  text: string;
  at: string;
  data: Record<string, unknown>;
}

export interface PendingAction {
  action_id: string;
  tool: string;
  input: Record<string, unknown>;
  summary: string;
  status: string;
}

export interface ConversationView {
  conversation_id: string;
  title: string;
  created_at: string;
  transcript: TranscriptEntry[];
  pending_actions: PendingAction[];
}

// ---- autopilot ----------------------------------------------------------------------------------

export interface AutopilotConfig {
  enabled: boolean;
  universe: string[];
  data_source: "auto" | "venue" | "synthetic";
  interval_seconds: number;
  history_bars: number;
  capital: string;
  max_positions: number;
  max_weight: number;
  product: "CNC" | "INTRADAY";
  stop_loss: string;
  take_profit: string;
  trailing_stop: string;
  slippage_bps: string;
  min_sharpe: number;
  max_drawdown: number;
  min_dsr: number;
  min_trades: number;
  cycle_hours: number;
  max_deployment_drawdown: number;
  min_paper_days: number;
  min_paper_trades: number;
  use_analyst: boolean;
  analyst_can_veto: boolean;
  loss_floor: number;
  lock_in_gains: number;
  daily_loss_limit: number;
  max_cost_share: number;
  vol_target: string;
  leverage: string;
  max_participation: number;
  portfolio_drawdown_limit: number;
  halt_cooldown_days: number;
  roll_days: number;
  fx_rates: Record<string, string>;
  decision_mode: "ai" | "strategies";
  panel_size: number;
  monitor_enabled: boolean;
  monitor_mode: "advise" | "act";
  monitor_interval_seconds: number;
  monitor_min_confidence: number;
  monitor_entry_gate: boolean;
  monitor_fallback: "allow" | "block";
  monitor_cooldown_minutes: number;
  monitor_max_calls_per_day: number;
  monitor_reasoning: boolean;
}

export interface MonitorReview {
  review_id: string;
  at: string;
  kind: "POSITION" | "ENTRY" | "TRADE";
  deployment_id: string;
  instrument_id: string;
  mode: string;
  direction: number;
  quantity: string;
  price: string;
  verdict: "HOLD" | "REDUCE" | "EXIT" | "APPROVE" | "REJECT" | "LONG" | "SHORT" | "FLAT" | "NO_ANSWER";
  confidence: number;
  reason: string;
  acted: boolean;
  model: string;
  label: string;
  currency: string;
  moves: Record<string, number | null>;
}

export interface TradeLesson {
  lesson_id: string;
  at: string;
  instrument_id: string;
  mode: string;
  direction: number;
  entry_price: string;
  exit_price: string;
  pnl_inr: number;
  minutes_held: number;
  exit_reason: string;
  label: string;
  status: "pending" | "analysed" | "failed";
  mistake: string;
  diagnosis: string;
  lesson: string;
}

export interface LearnedRule {
  mistake: string;
  cases: number;
  instruments: string[];
  loss_inr: number;
  until: string;
  text: string;
}

export interface LessonsStatus {
  lessons: TradeLesson[];
  rules: LearnedRule[];
  open_trades: number;
}

export interface MonitorScore {
  judged: number;
  right: number;
  accuracy: number | null;
  value_of_actions_inr: number;
}

export interface MonitorStatus {
  enabled: boolean;
  mode: "advise" | "act";
  interval_seconds: number;
  min_confidence: number;
  available: boolean;
  provider: string | null;
  model: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  last_error: string | null;
  calls_today: number;
  pending: { instrument_id: string; side: string; label: string; since: string }[];
  positions: MonitorReview[];
  trader: {
    instrument_id: string;
    deployment_id: string;
    position: string;
    live_copies: number;
    views: { label: string; view: "LONG" | "SHORT" | "FLAT" }[];
    decision: MonitorReview | null;
  }[];
  recent: MonitorReview[];
  scorecard: {
    "15": MonitorScore;
    "60": MonitorScore;
    verdicts: Record<string, number>;
    acted: number;
    ai_book_paper_inr: number;
    ai_book_live_inr: number;
  };
  decision_mode: "ai" | "strategies";
  ai_trading: boolean;
}

export interface UniverseOption {
  id: string;
  label: string;
  group: string;
}

export interface Protection {
  budget: string;
  equity: string;
  floor: string;
  cushion: string;
  exposure: string;
  floor_hit: boolean;
}

export interface ArmedAccount {
  account_id: string;
  capital_cap: string;
  armed_by: string;
  armed_at: string;
  allow_futures: boolean;
}

export interface AnalystConcern {
  instrument_id: string;
  concern: string;
  severity: "low" | "medium" | "high";
}

export interface PeriodStats {
  return: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  trades: number;
  win_rate?: number | null;
  positive_folds?: number;
  bars: number;
  charges?: number;
  charges_share?: number | null;
}

export interface EvaluationSummary {
  instrument_id: string;
  candidate: string;
  label: string;
  signal: string;
  validation: PeriodStats;
  holdout: PeriodStats;
  fold_returns: number[];
  dsr: number | null;
  passed: boolean;
  reasons: string[];
  instruments?: string[];
}

export interface SelectedStrategy extends EvaluationSummary {
  weight: number;
  capital: string;
  equity: [string, number][];
}

export interface AutopilotRun {
  run_id: string;
  started_at: string;
  finished_at: string | null;
  data_source: string;
  trials: number;
  leaderboard: EvaluationSummary[];
  selected: SelectedStrategy[];
  skipped: Record<string, string>;
  summary: string | null;
  error: string | null;
  analyst: string | null;
  concerns: AnalystConcern[];
}

export interface ManagedDeployment {
  deployment_id: string;
  instrument_id: string;
  candidate: string;
  label: string;
  signal: string;
  mode: "PAPER" | "LIVE";
  capital: string;
  created_at: string;
  status: "ACTIVE" | "CLOSING" | "RETIRED";
  pnl: string;
  peak_pnl: string;
  model: string | null;
  source_deployment: string | null;
  live_deployment: string | null;
  expected: Partial<PeriodStats>;
  account_id: string;
  universe_id: string;
  instruments: string[];
  currency: string;
  strategy: string;
  rolled_to: string | null;
}

export interface AutopilotStatus {
  config: AutopilotConfig;
  live: { accounts: Record<string, ArmedAccount> };
  halted_until: Record<string, string>;
  protection: Record<"PAPER" | "LIVE", Protection>;
  progress: { running: boolean; done?: number; total?: number; message?: string; run_id?: string };
  last_run_at: string | null;
  next_run_at: string | null;
  paper_account: string;
  analyst_available: boolean;
  managed: ManagedDeployment[];
  latest_run: AutopilotRun | null;
}

export interface AutopilotDecision {
  decision_id: string;
  at: string;
  kind: string;
  title: string;
  reasons: string[];
  instrument_id: string | null;
  deployment_id: string | null;
  run_id: string | null;
  actor: string;
}

// ---- strategy lab -------------------------------------------------------------------------------

export type RuleSpec = Record<string, unknown>;

export interface Translation {
  spec: RuleSpec;
  description: string[];
  assumptions: string[];
  unsupported: string[];
  model: string;
}

export interface LabCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface LabRun {
  run_id: string;
  at: string;
  user_id: string;
  instrument_id: string;
  interval_seconds: number;
  bars: number;
  spec: RuleSpec;
  description: string[];
  text: string;
  assumptions: string[];
  unsupported: string[];
  data_source: string;
  capital: string;
  capital_quote: string;
  currency: string;
  leverage: string;
  results: {
    return: number | null;
    sharpe: number | null;
    max_drawdown: number | null;
    volatility: number | null;
    trades: number;
    win_rate: number | null;
    profit_factor: number | null;
    benchmark_return: number | null;
    charges: number;
    charges_share: number | null;
    periods: number[];
    bars: number;
    leverage_used: string;
    leverage_cap: string;
    strategy_errors: number;
  };
  confidence: {
    score: number;
    grade: "High" | "Medium" | "Low";
    dsr: number | null;
    trials: number;
    probability_of_profit: number | null;
    synthetic: boolean;
    checks: LabCheck[];
  };
  equity?: [string, number][];
  trades?: {
    direction: string;
    quantity: string;
    entry_time: string;
    exit_time: string;
    entry_price: string;
    exit_price: string;
    net_pnl: string;
  }[];
  review: { summary: string; strengths: string[]; weaknesses: string[]; suggestions: string[] } | null;
  model: string;
  deployment_id: string | null;
}
