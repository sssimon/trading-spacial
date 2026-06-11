// ============================================================
// types.ts — TypeScript interfaces for all API responses
// ============================================================

export interface Sizing1h {
  capital_usd?: number;
  riesgo_usd?:  number;
  atr_1h?:      number;
  sl_mode?:     string;
  sl_pct?:      string;
  tp_pct?:      string;
  sl_precio?:   number;
  tp_precio?:   number;
  qty_btc?:     number;
  valor_pos?:   number;
  pct_capital?: number;
}

export interface SymbolStatus {
  symbol: string;
  estado: string;
  /** Decision-time price (1H close). Used by SL/TP/sizing on the backend; stable across 5-min scans within an hour. */
  price: number | null;
  /** Display price (5m close). Refreshes every scan. Prefer this for rendering; fall back to `price`. */
  live_price?: number | null;
  /** Rolling buffer of recent prices for the sparkline. Populated client-side by `useLiveTicker` from successive ticker polls — empty on mount, grows over time. */
  recent_closes?: number[];
  /** 24h percent change. Populated client-side from /ticker (Binance 24hr endpoint). */
  change_24h?: number | null;
  lrc_pct: number | null;
  score: number | null;
  señal: boolean;
  /** 1H setup detected (LRC in zone + indicator conditions). Independent of gatillo. */
  setup?: boolean;
  gatillo: boolean;
  ts: string | null;
  sizing_1h?: Sizing1h;
  direction?: 'LONG' | 'SHORT' | null;
}

export interface SymbolsResponse {
  total: number;
  symbols: SymbolStatus[];
}

export interface ScannerState {
  running: boolean;
  last_scan_ts: string | null;
  last_symbol: string | null;
  last_estado: string | null;
  scans_total: number;
  signals_total: number;
  errors: number;
  /** List of curated symbol IDs (e.g. ["BTCUSDT", "ETHUSDT", ...]). */
  symbols_active: string[];
  /** ISO timestamp when the scanner thread entered its loop. Used for uptime display. */
  started_at: string | null;
}

export interface StatusResponse {
  scanner_state: ScannerState;
  ultimo_escaneo: string | null;
}

/** Aggregated context the AgentBrief + AgentDock consume. Composed
 *  client-side from ScannerState + MacroResponse + a few derived fields
 *  the backend doesn't ship yet (kill-switch count is hardcoded to 0
 *  until /health/symbols aggregation lands — see App.tsx). */
export interface MacroState {
  /** Composite regime label from the daily regime detector. */
  regime:           'BULL' | 'BEAR' | 'NEUTRAL' | null;
  /** Fear & Greed index 0–100. */
  fng:              number | null;
  /** BTC perp funding rate as a decimal fraction (not percent). 0.0001 = 0.01%. */
  funding:          number | null;
  /** Lifetime scans (we don't yet split by day — used as a proxy). */
  scansToday:       number;
  /** Lifetime signals generated. */
  signalsToday:     number;
  /** Errors in the last cycle. */
  errors:           number;
  /** Number of symbols currently paused by the kill switch. */
  killSwitchActive: number;
}

export interface Signal {
  id: number;
  ts: string;
  symbol: string;
  estado: string;
  señal: boolean;
  setup: boolean;
  price: number | null;
  lrc_pct: number | null;
  rsi_1h: number | null;
  score: number | null;
  score_label: string;
  macro_ok: boolean;
  gatillo: boolean;
  direction?: 'LONG' | 'SHORT' | null;
  /** Historical SL/TP from sizing_1h at the time of the scan — projected from
   *  the scan's payload by /signals (Closes #211). Null on pre-direction rows
   *  or rows without sizing. Used by the OpenPositionModal prefill. */
  sl_precio?: number | null;
  tp_precio?: number | null;
}

export interface SignalsResponse {
  total: number;
  signals: Signal[];
}

export interface ScanResult {
  symbol: string;
  estado: string;
  score: number;
  señal: boolean;
}

export interface ScanResponse {
  scanned: number;
  results: ScanResult[];
}

// Backend returns one entry per channel under the keys it actually tried.
// `error` is set when ok=false; `status_code` and `url` are present when the
// channel did an HTTP call.
export interface WebhookTestChannelResult {
  ok: boolean;
  status_code?: number;
  error?: string;
  url?: string;
}

// Public agent feature status — served by GET /agent/status.
// The `reason` field is a closed enum; never expand it with operator-only
// strings (env-var names, paths, secret names). See api/agent/config.py.
export interface AgentStatus {
  enabled: boolean;
  reason:  'ok' | 'agent_disabled';
}

export interface WebhookTestResponse {
  ok: boolean;  // overall: at least one channel succeeded
  telegram_directo: WebhookTestChannelResult;
  webhook_n8n: WebhookTestChannelResult;
}

export interface SignalsParams {
  limit?: number;
  only_signals?: boolean;
  since_hours?: number;
  symbol?: string;
}

// ---- OHLCV ----------------------------------------------------------------

export interface OhlcvCandle {
  time: number;   // Unix seconds UTC
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface OhlcvVolume {
  time: number;
  value: number;
  color: string;
}

export interface OhlcvResponse {
  symbol: string;
  interval: string;
  candles: OhlcvCandle[];
  volumes: OhlcvVolume[];
}

// ---- Signal filters -------------------------------------------------------

export interface SignalFilters {
  min_score: number;        // 0-10
  require_macro_ok: boolean;
  notify_setup: boolean;
}

export interface AppConfig {
  webhook_url: string;
  notify_setup_only: boolean;
  scan_interval_sec: number;
  num_symbols: number;
  telegram_chat_id: string;
  signal_filters: SignalFilters;
  auto_approve_tune: boolean;
}

export interface ConfigUpdateResponse {
  ok: boolean;
  config: AppConfig;
}

// ---- Positions -------------------------------------------------------

export type PositionStatus    = 'open' | 'closed' | 'cancelled';
export type PositionDirection = 'LONG' | 'SHORT';
export type ExitReason        = 'TP_HIT' | 'SL_HIT' | 'MANUAL' | 'EXPIRED';

export interface Position {
  id:          number;
  scan_id:     number | null;
  symbol:      string;
  direction:   PositionDirection;
  status:      PositionStatus;
  entry_price: number;
  entry_ts:    string;
  sl_price:    number | null;
  tp_price:    number | null;
  size_usd:    number | null;
  qty:         number | null;
  exit_price:  number | null;
  exit_ts:     string | null;
  exit_reason: ExitReason | null;
  pnl_usd:     number | null;
  pnl_pct:     number | null;
  notes:       string | null;
  atr_entry:   number | null;
  observed_orders?: ObservedOrder[];   // v0.3: solo filas EXTERNAL
}

// Binance v0.3 — orden de protección observada en la cuenta spot (read-only).
// Solo presente en posiciones EXTERNAL; el backend la adjunta en GET /positions.
export interface ObservedOrder {
  symbol:      string;
  kind:        'SL' | 'TP';
  price:       number;
  qty:         number;
  pct_holding: number | null;   // null = holding sin qty conocida (se abstiene)
  order_id:    number;
  oco_group:   number | null;   // patas OCO comparten grupo
  observed_at: string;
}

export interface PositionsResponse {
  total:     number;
  positions: Position[];
}

export interface PositionCreatePayload {
  symbol:      string;
  direction?:  PositionDirection;
  entry_price: number;
  qty:         number;            // REQUIRED by the backend OpenPositionRequest
  sl_price?:   number | null;
  tp_price?:   number | null;
  size_usd?:   number | null;
  scan_id?:    number | null;
  notes?:      string;
}

export interface PositionUpdatePayload {
  sl_price?:    number | null;
  tp_price?:    number | null;
  size_usd?:    number | null;
  entry_price?: number;
  notes?:       string;
}

export interface PositionClosePayload {
  exit_price:  number;
  exit_reason?: ExitReason;
}

// ---- Auto-Tune -------------------------------------------------------

export interface TuneSymbolResult {
  symbol: string;
  recommendation: 'CHANGE' | 'KEEP' | 'NO_DATA' | 'ERROR';
  current_params: {
    atr_sl_mult: number;
    atr_tp_mult: number;
    atr_be_mult: number;
  };
  proposed_params?: {
    atr_sl_mult: number;
    atr_tp_mult: number;
    atr_be_mult: number;
  } | null;
  current_val_pnl?: number;
  proposal_detail?: {
    val_pnl: number;
    val_pf: number;
    improvement_pct: number;
    total_trades: number;
    train_pnl: number;
    val_trades: number;
  } | null;
}

export interface TuneResult {
  id: number;
  ts: string;
  status: 'pending' | 'applied' | 'rejected';
  results?: TuneSymbolResult[];
  report_md?: string;
  applied_ts?: string | null;
  changes_count: number;
}

// ---- Notifications (#162 PR C) ----------------------------------------

export interface Notification {
  id: number;
  event_type: 'signal' | 'health' | 'infra' | 'system' | 'position_exit' | string;
  event_key: string;
  priority: 'info' | 'warning' | 'critical' | string;
  payload_json: string;
  channels_sent: string;
  delivery_status: 'ok' | 'partial' | 'failed' | 'rate_limited' | string;
  sent_at: string;
  read_at: string | null;
  error_log: string | null;
}

export interface NotificationsResponse {
  notifications: Notification[];
}

// ─── Kill switch observability (#187 phase 1) ────────────────────────

export type KillSwitchEngine = 'v1' | 'v2_shadow' | 'v2_live';
export type KillSwitchPerSymbolTier =
  | 'NORMAL' | 'ALERT' | 'REDUCED' | 'PAUSED' | 'PROBATION';
export type KillSwitchPortfolioTier =
  | 'NORMAL' | 'WARNED' | 'REDUCED' | 'FROZEN';

export interface KillSwitchDecision {
  id: number;
  ts: string;
  scan_id: number | null;
  symbol: string;
  engine: KillSwitchEngine;
  per_symbol_tier: KillSwitchPerSymbolTier;
  portfolio_tier: KillSwitchPortfolioTier;
  velocity_active: boolean;
  size_factor: number;
  skip: boolean;
  reasons_json: string;
  slider_value: number | null;
}

export interface KillSwitchDecisionsResponse {
  decisions: KillSwitchDecision[];
}

export interface KillSwitchSymbolState {
  symbol: string;
  per_symbol_tier: KillSwitchPerSymbolTier;
  portfolio_tier: KillSwitchPortfolioTier;
  size_factor: number;
  skip: boolean;
  velocity_active: boolean;
  ts: string;
  reasons_json: string;
}

export interface KillSwitchPortfolioState {
  tier: KillSwitchPortfolioTier;
  concurrent_failures: number;
}

export interface KillSwitchCurrentStateResponse {
  symbols: { [symbol: string]: KillSwitchSymbolState };
  portfolio: KillSwitchPortfolioState;
}

// ─── Kill switch v2 dashboard (#187 B6) ───────────────────────────────

export interface DashboardSymbolMetrics {
  trades_count_total: number;
  win_rate_20_trades: number;
  win_rate_10_trades: number;
  pnl_30d: number;
  months_negative_consecutive: number;
  probation_trades_remaining: number | null;
  paused_days_at_entry: number | null;
}

export interface DashboardSymbolTransition {
  from_state: string;
  to_state: string;
  reason: string;
  ts: string;
}

export interface DashboardSymbolState {
  symbol: string;
  state: KillSwitchPerSymbolTier;
  state_since: string | null;
  manual_override: boolean;
  metrics: DashboardSymbolMetrics;
  last_transition: DashboardSymbolTransition | null;
  sparkline_20: Array<'W' | 'L' | null>;
  next_conditions: string;
}

export interface DashboardPortfolioTransition {
  from_tier: string;
  to_tier: string;
  reason: string;
  dd_pct: number;
  concurrent: number;
  ts: string;
}

export interface DashboardPortfolioState {
  tier: KillSwitchPortfolioTier;
  dd_pct: number;
  peak_equity: number;
  current_equity: number;
  // Equity en vivo display-only: cash + holds EXTERNAL marcados a precio actual.
  // null para tenants sin holds externos ni cash (señal-only). Separado de
  // current_equity/dd_pct: NO alimenta el kill-switch (CD-1).
  real_equity_usd?: number | null;
  concurrent_failures: number;
  recent_transitions: DashboardPortfolioTransition[];
}

export type DashboardAlertKind =
  | 'symbol_failures' | 'portfolio_dd' | 'velocity_burst' | 'auto_reactivation';

export interface DashboardAlertItem {
  kind: DashboardAlertKind;
  text: string;
  severity: 'info' | 'warning' | 'critical';
  ts: string;
}

export interface DashboardAlertSummary {
  items: DashboardAlertItem[];
}

export interface DashboardResponse {
  symbols: DashboardSymbolState[];
  portfolio: DashboardPortfolioState;
  alerts: DashboardAlertSummary;
  generated_at: string;
}

// ---- Multi-tenant: capital + user preferences (Epic B #253, B.5 follow-up B) ----
//
// Both are per-user resources. The API derives tenant_id from JWT —
// the frontend NEVER sends tenant_id / user_id in any request.

export interface Capital {
  id: number;
  tenant_id: number;
  balance: number;
  peak_balance: number;
  max_drawdown_pct: number | null;
  updated_at: string;
}

export interface CapitalPutPayload {
  balance: number;
  peak_balance?: number;
  max_drawdown_pct?: number;
}

export interface UserPreferences {
  id?: number;
  tenant_id: number;
  symbol_filter: string[] | null;
  min_score: number;
  notify_channels: Record<string, unknown> | null;
  updated_at?: string;
}

export interface PreferencesPutPayload {
  symbol_filter?: string[] | null;
  min_score?: number;
  notify_channels?: Record<string, unknown> | null;
}

// ---- Telegram per-user config (spec 2026-05-21) ----

export interface NotifyChannels {
  telegram_bot_token?: string;   // masked from server: '<10chars>****<4chars>'
  telegram_chat_id?:   string;
}

export interface TestDeliveryReceipt {
  channel: string;
  status:  'ok' | 'failed' | 'rate_limited';
  error:   string | null;
}

export type TestDeliveryReason = 'no_telegram_configured' | null;

export interface TestDeliveryResponse {
  ok:       boolean;
  receipts: TestDeliveryReceipt[];
  reason:   TestDeliveryReason;
}

// ---- Vista Valles A — screener de consolidación (sin score de atractivo) ----

export interface ValleyCandidate {
  symbol:               string;
  price:                number;
  pct_rango:            number;
  semanas_consolidando: number;
  vol_percentil:        number;
  volumen_usd_dia:      number;
  distancia_ath_pct:    number;
  razones_vida:         string[];
}

export interface ValleySnapshot {
  generated_at: string | null;
  coverage:     { universe: number; evaluated: number; complete: boolean };
  candidates:   ValleyCandidate[];
}
