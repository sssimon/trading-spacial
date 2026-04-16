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
  price: number | null;
  lrc_pct: number | null;
  score: number | null;
  señal: boolean;
  gatillo: boolean;
  ts: string | null;
  sizing_1h?: Sizing1h;
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
  symbols_active: number;
}

export interface StatusResponse {
  scanner_state: ScannerState;
  ultimo_escaneo: string | null;
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

export interface WebhookTestResponse {
  ok: boolean;
  status_code: number;
  url: string;
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
}

export interface PositionsResponse {
  total:     number;
  positions: Position[];
}

export interface PositionCreatePayload {
  symbol:      string;
  direction?:  PositionDirection;
  entry_price: number;
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
