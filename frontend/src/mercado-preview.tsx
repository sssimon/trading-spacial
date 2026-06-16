// ============================================================
// mercado-preview.tsx — PREVIEW DEV-ONLY del Mercado cálido.
//
// Monta MercadoView con datos mock, SIN backend ni auth, para
// validar el rediseño visualmente. Abrir en:
//   http://localhost:5173/mercado-preview.html
//
// Switch de frescura arriba para ver los estados honestos
// (fresco / rancio / muerto). NO entra al build de producción
// (vite solo bundlea index.html). Borrar tras validar Fase 1.
// ============================================================

import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';

import './App.css';
import './styles/tokens.css';
import './styles/base.css';
import './styles/warm-tokens.css';
import './styles/mercado-warm.css';

import MercadoView, { type MercadoWallet } from './components/MercadoView';
import type { SymbolStatus, Signal, StatusResponse, Frescura, ScoreComponent } from './types';
import type { MacroResponse } from './api';
import type { FocusItem } from './helpers/hierarchy';

const NOW = Date.now();
const iso = (minsAgo: number) => new Date(NOW - minsAgo * 60_000).toISOString();

const COMP_VAL: Record<string, string> = {
  RSI: 'RSI 31', DIV: '', SR: '0.4%', BB: '', VOL: 'x1.3', CVD: '+1820', SMA: '',
};
const mkComp = (passes: boolean[]): ScoreComponent[] =>
  ['RSI', 'DIV', 'SR', 'BB', 'VOL', 'CVD', 'SMA'].map((key, i) => ({
    key,
    pass: !!passes[i],
    value: passes[i] && COMP_VAL[key] ? COMP_VAL[key] : null,
  }));

// sparkline determinista (paseo suave)
const spark = (seed: number, up: boolean): number[] => {
  const out: number[] = [];
  let v = 100;
  for (let i = 0; i < 24; i++) {
    v += Math.sin((i + seed) * 0.7) * 1.6 + (up ? 0.5 : -0.5);
    out.push(Math.round(v * 100) / 100);
  }
  return out;
};

const sym = (
  o: Partial<SymbolStatus> & { symbol: string; score: number; passes: boolean[] },
): SymbolStatus => ({
  estado: 'OK',
  price: o.price ?? 100,
  live_price: o.live_price ?? o.price ?? 100,
  recent_closes: o.recent_closes ?? spark(o.score, (o.change_24h ?? 0) >= 0),
  change_24h: o.change_24h ?? 0,
  lrc_pct: o.lrc_pct ?? 42,
  señal: o.señal ?? false,
  setup: o.setup ?? false,
  gatillo: o.gatillo ?? false,
  ts: o.ts ?? iso(4),
  direction: o.direction ?? null,
  score_components: mkComp(o.passes),
  ...o,
});

const SYMBOLS: SymbolStatus[] = [
  sym({ symbol: 'BTCUSDT', score: 6, passes: [1, 1, 1, 0, 1, 1, 0].map(Boolean), señal: true, gatillo: true, setup: true, direction: 'LONG', price: 84120.5, change_24h: 2.4, lrc_pct: 18, ts: iso(3) }),
  sym({ symbol: 'ETHUSDT', score: 5, passes: [1, 0, 1, 1, 1, 1, 0].map(Boolean), señal: true, gatillo: true, setup: true, direction: 'LONG', price: 2184.3, change_24h: 1.8, lrc_pct: 24, ts: iso(6) }),
  sym({ symbol: 'SOLUSDT', score: 4, passes: [1, 0, 1, 0, 1, 1, 0].map(Boolean), setup: true, direction: 'LONG', price: 142.66, change_24h: -0.9, lrc_pct: 38 }),
  sym({ symbol: 'AVAXUSDT', score: 3, passes: [1, 0, 1, 0, 0, 1, 0].map(Boolean), direction: 'LONG', price: 28.41, change_24h: 0.6, lrc_pct: 47 }),
  sym({ symbol: 'LINKUSDT', score: 2, passes: [0, 0, 1, 0, 1, 0, 0].map(Boolean), direction: 'SHORT', price: 13.92, change_24h: -1.4, lrc_pct: 61 }),
  sym({ symbol: 'XRPUSDT', score: 1, passes: [0, 0, 1, 0, 0, 0, 0].map(Boolean), price: 0.5123, change_24h: -0.3 }),
  sym({ symbol: 'DOGEUSDT', score: 0, passes: [0, 0, 0, 0, 0, 0, 0].map(Boolean), price: 0.1287, change_24h: -2.1 }),
  sym({ symbol: 'ADAUSDT', score: 1, passes: [0, 0, 0, 0, 1, 0, 0].map(Boolean), price: 0.3344, change_24h: 0.2 }),
];

const MACRO: MacroResponse = {
  regime: 'BULL', regime_score: 64, fear_greed_index: 62, fear_greed_label: 'Codicia',
  funding_rate_pct: 0.012, btc_24h_pct: 2.4, btc_price: 84120.5, ts: iso(2),
};

const WALLET: MercadoWallet = {
  equity: 10234.5, peak: 11800, drawdown: -3.2, pnlToday: 42.18, openCount: 2, capitalLocked: 1840,
};

const FOCUS: FocusItem[] = [
  { kind: 'fresh-signal', priority: 86, pair: 'BTCUSDT', title: 'Bitcoin disparó setup', body: '6 de 7 condiciones coinciden · gatillo activo · hace 3 min', action: 'Abrir posición' },
  { kind: 'near-tp', priority: 70, pair: 'ETHUSDT', title: 'Ethereum cerca del objetivo', body: '+2.83% · falta 0.9% al TP', action: 'Asegurar ganancia' },
];

const SIGNALS: Signal[] = [
  { id: 1, ts: iso(3), symbol: 'BTCUSDT', estado: 'SETUP_OK', señal: true, setup: true, price: 84120.5, lrc_pct: 18, rsi_1h: 31, score: 6, score_label: '', macro_ok: true, gatillo: true, direction: 'LONG' },
  { id: 2, ts: iso(11), symbol: 'ETHUSDT', estado: 'SETUP_OK', señal: true, setup: true, price: 2184.3, lrc_pct: 24, rsi_1h: 34, score: 5, score_label: '', macro_ok: true, gatillo: true, direction: 'LONG' },
  { id: 3, ts: iso(26), symbol: 'SOLUSDT', estado: 'SETUP_LRC', señal: false, setup: true, price: 142.66, lrc_pct: 38, rsi_1h: 41, score: 4, score_label: '', macro_ok: true, gatillo: false, direction: 'LONG' },
];

const STATUS: StatusResponse = {
  scanner_state: {
    running: true, last_scan_ts: iso(3), last_symbol: 'ADAUSDT', last_estado: 'OK',
    scans_total: 412, signals_total: 18, errors: 0, symbols_active: SYMBOLS.map((s) => s.symbol), started_at: iso(600),
  },
  ultimo_escaneo: iso(3),
};

const FRESCURAS: Record<string, Frescura> = {
  fresco: { estado: 'fresco', edad_seg: 240, generated_at: iso(4), umbral_seg: 900 },
  rancio: { estado: 'rancio', edad_seg: 190000, generated_at: iso(3166), umbral_seg: 900 },
  muerto: { estado: 'muerto', edad_seg: null, generated_at: null, umbral_seg: 900 },
};

const Preview = () => {
  const [estado, setEstado] = useState<'fresco' | 'rancio' | 'muerto'>('fresco');
  const [lenguaje, setLenguaje] = useState<'claro' | 'tecnico'>('claro');
  const status: StatusResponse = estado === 'muerto'
    ? { ...STATUS, scanner_state: { ...STATUS.scanner_state, running: false } }
    : STATUS;
  return (
    <div style={{ minHeight: '100vh', background: 'var(--paper)' }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 16px', borderBottom: '1px solid var(--card-edge)', background: 'var(--card-2)', fontFamily: 'var(--sans)', fontSize: 13, flexWrap: 'wrap' }}>
        <b style={{ fontFamily: 'var(--serif)' }}>Preview · Mercado cálido</b>
        <span style={{ color: 'var(--ink-3)' }}>frescura:</span>
        {(['fresco', 'rancio', 'muerto'] as const).map((e) => (
          <button key={e} onClick={() => setEstado(e)} style={{ cursor: 'pointer', borderRadius: 999, padding: '4px 12px', border: '1px solid var(--card-edge-2)', background: estado === e ? 'var(--clay-tint)' : 'var(--card)', color: estado === e ? 'var(--clay-deep)' : 'var(--ink-2)', fontWeight: estado === e ? 600 : 400 }}>{e}</button>
        ))}
        <span style={{ color: 'var(--ink-3)', marginLeft: 8 }}>lenguaje:</span>
        {(['claro', 'tecnico'] as const).map((l) => (
          <button key={l} onClick={() => setLenguaje(l)} style={{ cursor: 'pointer', borderRadius: 999, padding: '4px 12px', border: '1px solid var(--card-edge-2)', background: lenguaje === l ? 'var(--clay-tint)' : 'var(--card)', color: lenguaje === l ? 'var(--clay-deep)' : 'var(--ink-2)', fontWeight: lenguaje === l ? 600 : 400 }}>{l}</button>
        ))}
      </div>
      <MercadoView
        symbols={SYMBOLS}
        loading={false}
        macro={MACRO}
        wallet={WALLET}
        signals={SIGNALS}
        status={status}
        frescura={FRESCURAS[estado]}
        focus={FOCUS}
        onSymbolClick={(s) => console.log('open', s.symbol)}
        onOpenSignal={(s) => console.log('signal', s.symbol)}
        onRescan={() => console.log('rescan')}
        lenguaje={lenguaje}
      />
    </div>
  );
};

document.documentElement.setAttribute('data-score', 'on');
createRoot(document.getElementById('root')!).render(<StrictMode><Preview /></StrictMode>);
