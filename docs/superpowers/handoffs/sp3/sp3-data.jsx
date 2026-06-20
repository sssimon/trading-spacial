/* global window */
// ============================================================
// Valles · SP3 — datos mock 1:1 con el contrato del brief §7.
// Cada objeto refleja la forma EXACTA del endpoint correspondiente:
//   /alt-season · /valley-eval/{sym} · /levels/{sym} · /plan/{sym} · /dossier/{sym}
// Doctrina §4: hechos, nunca veredicto. Sin score, sin ranking.
// ============================================================

// ── mapa de nombres humanos (§6.12) ────────────────────────
const SP3_NAMES = {
  ADAUSDT: 'Cardano', XLMUSDT: 'Stellar', RUNEUSDT: 'THORChain', PENDLEUSDT: 'Pendle',
  JUPUSDT: 'Jupiter', UNIUSDT: 'Uniswap', INJUSDT: 'Injective', GMXUSDT: 'GMX',
  BTCUSDT: 'Bitcoin', PYTHUSDT: 'Pyth', ZBCUSDT: 'Zebec',
};
const nm = (s) => SP3_NAMES[s] || (s || '').replace('USDT', '');

// ── formato (hechos legibles) ──────────────────────────────
function p$(n) {
  if (n == null) return '—';
  if (n >= 1000) return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 10) return n.toFixed(2);
  return n.toFixed(2);
}
function pct1(n) { return n == null ? '—' : (n >= 0 ? '' : '−') + Math.abs(n).toFixed(1) + '%'; }
function pctFrac1(f) { return f == null ? '—' : (f * 100).toFixed(1) + '%'; }
function volUsd(n) {
  if (n == null) return '—';
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return '$' + (n / 1e3).toFixed(0) + 'K';
  return '$' + n;
}
function edad(seg) {
  if (seg == null) return null;
  if (seg < 3600) return 'hace ' + Math.max(1, Math.round(seg / 60)) + ' min';
  if (seg < 172800) return 'hace ' + Math.round(seg / 3600) + ' h';
  return 'hace ' + Math.round(seg / 86400) + ' dias';
}

// ── PRNG determinista (mulberry32) para velas reproducibles ─
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Genera N velas diarias siguiendo una trayectoria de cierres.
// traj(i) → precio base esperado en el día i. El ruido da cuerpo a la vela.
function genCandles(seed, n, traj, t0) {
  const rnd = mulberry32(seed);
  const out = [];
  let prevClose = traj(0);
  const start = t0 || 1715000000;
  for (let i = 0; i < n; i++) {
    const base = traj(i);
    const open = prevClose;
    const drift = (base - open) * (0.55 + rnd() * 0.4);
    const close = open + drift + (rnd() - 0.5) * base * 0.012;
    const hi = Math.max(open, close) + rnd() * base * 0.016;
    const lo = Math.min(open, close) - rnd() * base * 0.016;
    out.push({ time: start + i * 86400, open, high: hi, low: lo, close });
    prevClose = close;
  }
  return out;
}

// pos_in_30d_range a partir de las últimas 30 velas
function pos30(candles) {
  const last = candles.slice(-30);
  const lo = Math.min(...last.map((c) => c.low));
  const hi = Math.max(...last.map((c) => c.high));
  const price = candles[candles.length - 1].close;
  return { lo, hi, pos: (price - lo) / (hi - lo) };
}

// ── velas INJUSDT (hero): corrección, cuartil inferior ─────
// El grueso de la corrección cae DENTRO de la ventana de 30d, de
// modo que 21.34 queda en la parte baja del rango (pos ≈ 0.20).
const INJ_TRAJ = (i) => {
  if (i < 32) return 27.6 - i * 0.019;          // plateau alto previo
  if (i < 60) return 27.0 - (i - 32) * 0.246;   // corrección sostenida (en la ventana 30d)
  return 20.1 + (i - 60) * 0.41;                // pequeño rebote a 21.34
};
const INJ_CANDLES = (() => {
  const c = genCandles(7, 64, INJ_TRAJ);
  c[c.length - 1].close = 21.34; // fija el cierre exacto del brief
  return c;
})();

// ── velas UNIUSDT: viva pero arriba en su rango (pos alta) ──
const UNI_TRAJ = (i) => {
  if (i < 20) return 6.4 - i * 0.02;
  if (i < 50) return 6.0 + (i - 20) * 0.085;   // tendencia al alza
  return 8.5 + Math.sin((i - 50) * 0.6) * 0.18;
};
const UNI_CANDLES = (() => {
  const c = genCandles(19, 64, UNI_TRAJ);
  c[c.length - 1].close = 8.62;
  return c;
})();

// ════════════════════════════════════════════════════════════
// /alt-season — RegimeSnapshot (§7.1) — alimenta la Pieza 1
// ════════════════════════════════════════════════════════════
function regimeBase(over) {
  return Object.assign({
    generated_at: '2026-06-19T08:30:00+00:00',
    coverage: { universe: 218, evaluated: 214, complete: false },
    dominancia_fetch: { ok: true, fetched_at: '2026-06-19T08:30:12+00:00', source: 'coingecko/global' },
    regime: {
      estado: 'alts',
      componentes: {
        breadth50:      { valor: 0.63,  lean: 'alts',    estado: 'fresco', n: 213 },
        outperf_30d:    { valor: 0.082, lean: 'alts',    estado: 'fresco' },
        dominancia_btc: { valor: 0.555, lean: 'neutral', estado: 'fresco' },
      },
      votos: { alts: 2, neutral: 1, btc: 0, vivos: 3 },
      n_alts_evaluadas: 213,
    },
    frescura: { estado: 'fresco', edad_seg: 1820, generated_at: '2026-06-19T08:30:00+00:00', umbral_seg: 43200 },
  }, over || {});
}

const REGIME = {
  alts: regimeBase(),
  rancio: regimeBase({
    frescura: { estado: 'rancio', edad_seg: 64800, generated_at: '2026-06-18T14:30:00+00:00', umbral_seg: 43200 },
  }),
  muerto: {
    generated_at: null,
    coverage: { universe: 218, evaluated: 0, complete: false },
    dominancia_fetch: { ok: false, fetched_at: null, source: 'coingecko/global' },
    regime: null,
    frescura: { estado: 'muerto', edad_seg: null, generated_at: null, umbral_seg: 43200 },
  },
  // un componente caído (dominancia) mientras los otros dos viven (§9.9)
  domMuerta: regimeBase({
    regime: {
      estado: 'alts',
      componentes: {
        breadth50:      { valor: 0.61,  lean: 'alts',    estado: 'fresco', n: 209 },
        outperf_30d:    { valor: 0.071, lean: 'alts',    estado: 'fresco' },
        dominancia_btc: { valor: null,  lean: null,      estado: 'muerto', razon: 'fuente caída' },
      },
      votos: { alts: 2, neutral: 0, btc: 0, vivos: 2 },
      n_alts_evaluadas: 209,
    },
  }),
};

// ════════════════════════════════════════════════════════════
// /valley-eval/{sym} — vida + pos_in_30d_range (§7.2)
// ════════════════════════════════════════════════════════════
const injPos = pos30(INJ_CANDLES);
const uniPos = pos30(UNI_CANDLES);

const EVAL = {
  // candidata: viva y en la parte baja del rango
  INJUSDT: {
    symbol: 'INJUSDT', estado: 'ok', candidata: true,
    generated_at: '2026-06-19T08:42:10+00:00',
    price: 21.34, pos_in_30d_range: injPos.pos, rsi14: 38.6,
    pct_vs_sma20: -4.2, pct_vs_sma50: -11.7, consol_30d: 22.5,
    vol_ratio: 1.34, drawdown_from_90h: -28.4, volumen_usd_dia: 4_850_000,
    distancia_ath_pct: 0.74, razones_vida: [],
    frescura: { estado: 'fresco', edad_seg: 0.4, generated_at: '2026-06-19T08:42:10+00:00', umbral_seg: 60 },
  },
  // viva pero NO candidata — arriba en su rango
  UNIUSDT: {
    symbol: 'UNIUSDT', estado: 'ok', candidata: false, vivo: true,
    generated_at: '2026-06-19T08:42:10+00:00',
    price: 8.62, pos_in_30d_range: uniPos.pos, rsi14: 61.2,
    pct_vs_sma20: 3.8, pct_vs_sma50: 9.1, consol_30d: 19.0,
    vol_ratio: 1.05, drawdown_from_90h: -7.2, volumen_usd_dia: 6_400_000,
    distancia_ath_pct: 0.42, razones_muerte: [],
    frescura: { estado: 'fresco', edad_seg: 0.6, generated_at: '2026-06-19T08:42:10+00:00', umbral_seg: 60 },
  },
  // Binance no respondió — números ausentes (§7.2 nota)
  FOOUSDT: {
    symbol: 'FOOUSDT', estado: 'no_disponible',
    frescura: { estado: 'muerto', edad_seg: null, generated_at: null, umbral_seg: 60 },
  },
};

const RAZONES_MUERTE = {
  historia_insuficiente: 'Historia insuficiente (menos de 120 días de velas)',
  volumen_bajo_piso: 'Volumen bajo el piso operable (mediana 30d < $500k/día)',
  volumen_agonizante: 'El volumen reciente cayó a menos de la mitad',
  velas_planas: 'Más de la mitad de las velas recientes casi sin movimiento',
};

// ════════════════════════════════════════════════════════════
// /levels/{sym} — paredes S/R + ubicación + velas (§7.3)
// ════════════════════════════════════════════════════════════
const LEVELS = {
  INJUSDT: {
    symbol: 'INJUSDT', estado: 'ok', generated_at: '2026-06-19T08:45:00+00:00', price_live: 21.34,
    zonas: [
      { tipo: 'soporte',     precio_bajo: 18.90, precio_alto: 19.40, centro: 19.15, toques: 3, confluencia_redondo: [19.0] },
      { tipo: 'soporte',     precio_bajo: 20.10, precio_alto: 20.85, centro: 20.40, toques: 4, confluencia_redondo: [20.5] },
      { tipo: 'resistencia', precio_bajo: 24.00, precio_alto: 24.70, centro: 24.35, toques: 5, confluencia_redondo: [24.0] },
      { tipo: 'resistencia', precio_bajo: 28.50, precio_alto: 29.20, centro: 28.80, toques: 2, confluencia_redondo: [29.0] },
    ],
    ubicacion: { dentro_de: null, techo: { centro: 24.35, dist_pct: 14.10 }, piso: { centro: 20.40, dist_pct: -4.41 } },
    candles: INJ_CANDLES,
    range30: { lo: injPos.lo, hi: injPos.hi },
    frescura: { estado: 'fresco', edad_seg: 0.5, generated_at: '2026-06-19T08:45:00+00:00', umbral_seg: 60 },
  },
  UNIUSDT: {
    symbol: 'UNIUSDT', estado: 'ok', generated_at: '2026-06-19T08:45:00+00:00', price_live: 8.62,
    zonas: [
      { tipo: 'soporte',     precio_bajo: 7.55, precio_alto: 7.80, centro: 7.68, toques: 3, confluencia_redondo: [] },
      { tipo: 'resistencia', precio_bajo: 8.80, precio_alto: 9.05, centro: 8.92, toques: 4, confluencia_redondo: [9.0] },
      { tipo: 'resistencia', precio_bajo: 9.60, precio_alto: 9.90, centro: 9.75, toques: 2, confluencia_redondo: [] },
    ],
    ubicacion: { dentro_de: null, techo: { centro: 8.92, dist_pct: 3.48 }, piso: { centro: 7.68, dist_pct: -10.9 } },
    candles: UNI_CANDLES,
    range30: { lo: uniPos.lo, hi: uniPos.hi },
    frescura: { estado: 'fresco', edad_seg: 0.5, generated_at: '2026-06-19T08:45:00+00:00', umbral_seg: 60 },
  },
  FOOUSDT: { symbol: 'FOOUSDT', estado: 'no_disponible', price_live: null, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } },
};

// ════════════════════════════════════════════════════════════
// /plan/{sym} — la jugada (§7.4)
// ════════════════════════════════════════════════════════════
const INJ_PLAN_CORE = {
  entry: 21.34, sl_plan: 18.71,
  sl_piso: { centro: 19.15, precio_bajo: 18.90, precio_alto: 19.40, toques: 3 },
  rungs: [
    { tp_price: 24.35, size_frac: 0.523, zona: { centro: 24.35, precio_bajo: 24.00, precio_alto: 24.70, toques: 5 } },
    { tp_price: 28.80, size_frac: 0.209, zona: { centro: 28.80, precio_bajo: 28.50, precio_alto: 29.20, toques: 2 } },
  ],
  runner_frac: 0.05, entry_zone: { centro: 20.40, precio_bajo: 20.10, precio_alto: 20.85, toques: 4 },
};

const PLAN = {
  // 10 · en curso (activo) — primer peldaño tocado, stop en break-even
  activo: {
    symbol: 'INJUSDT', estado_vivo: 'activo', _fijada: true, plan: INJ_PLAN_CORE,
    realidad: { fase: 'RUNNING', rungs_llenos: [0], sl_actual: 21.34, be_movido: true, size_restante_frac: 0.477 },
    hechos: ['TP1 se llenó', 'tu SL está en break-even'],
    frescura: { estado: 'fresco', edad_seg: 320, generated_at: '2026-06-19T08:39:40+00:00', umbral_seg: 900 },
  },
  // 11 · incierta
  incierto: {
    symbol: 'INJUSDT', estado_vivo: 'incierto', _fijada: true, plan: INJ_PLAN_CORE,
    realidad: { fase: 'RUNNING', rungs_llenos: [], sl_actual: 18.71, be_movido: false, size_restante_frac: 1.0 },
    hechos: ['transición sin confirmar — revisa en Binance'],
    frescura: { estado: 'fresco', edad_seg: 410, generated_at: '2026-06-19T08:38:10+00:00', umbral_seg: 900 },
  },
  // 12 · plan listo, sin fijar
  plan_listo: {
    symbol: 'INJUSDT', estado_vivo: 'activo', _fijada: false, plan: INJ_PLAN_CORE,
    realidad: { fase: 'PLANNED', rungs_llenos: [], sl_actual: 18.71, be_movido: false, size_restante_frac: 1.0 },
    hechos: [],
    frescura: { estado: 'fresco', edad_seg: 90, generated_at: '2026-06-19T08:44:30+00:00', umbral_seg: 900 },
  },
  // 13 · fijada
  fijada: {
    symbol: 'INJUSDT', estado_vivo: 'activo', _fijada: true, _reciencFijada: true, plan: INJ_PLAN_CORE,
    realidad: { fase: 'CONFIRMED', rungs_llenos: [], sl_actual: 18.71, be_movido: false, size_restante_frac: 1.0 },
    hechos: ['tu SL sigue debajo de la zona'],
    frescura: { estado: 'fresco', edad_seg: 40, generated_at: '2026-06-19T08:45:20+00:00', umbral_seg: 900 },
  },
  // 15 · sin plan (estado_vivo null, y nada más)
  sin_plan: { symbol: 'INJUSDT', estado_vivo: null },
};

// 14 · cerrada — espejo de conducta (sin PnL) — /plan/{sym}/conducta
const CONDUCTA = {
  honrado: {
    symbol: 'INJUSDT', estado_vivo: 'cerrado',
    titular: 'Honraste el plan que aprobaste.',
    campos: [
      { k: 'Entraste en la zona', ok: 'si' },
      { k: 'Respetaste el stop', ok: 'si' },
      { k: 'Moviste a break-even', ok: 'si' },
      { k: 'Honraste los peldaños', ok: 'si' },
      { k: 'Cerraste según el plan', ok: 'si' },
      { k: 'Cuánto aguantaste', ok: 'dato', v: '36 h' },
    ],
  },
};

// ════════════════════════════════════════════════════════════
// /dossier/{sym} — quién está detrás (§7.5)
// ════════════════════════════════════════════════════════════
const DOSSIER = {
  INJUSDT: {
    symbol: 'INJUSDT',
    equipo: [{ nombre: 'Eric Chen', rol: 'Co-fundador / CEO', enlaces: ['https://twitter.com/erichchen'], fuente: 'https://injective.com/team' }],
    equipo_identificado: true,
    presencia: {
      web:     { url: 'https://injective.com', activo: 'si', fuente: 'exa' },
      twitter: { url: 'https://twitter.com/injective', activo: 'si', fuente: 'exa' },
      github:  { url: 'https://github.com/InjectiveLabs', activo: 'si', fuente: 'exa' },
      discord: { url: null, activo: 'desconocido', fuente: null },
    },
    actividad: { github_commits: { valor: 'commits recientes en los últimos 30 días', fuente: 'github.com/InjectiveLabs' } },
    financiacion: [{ descripcion: 'Ronda de $40M liderada por Jump Crypto', fecha: '2022-01-01', fuente: 'techcrunch.com/...' }],
    hitos: [{ descripcion: 'Mainnet launch', fecha: '2021-11-08', fuente: 'injective.com/blog' }],
    estado_general: 'rastreable', no_encontrado_en: ['telegram'],
    generated_at: '2026-06-19T07:00:00+00:00',
    frescura: { estado: 'fresco', edad_seg: 5400, generated_at: '2026-06-19T07:00:00+00:00', umbral_seg: 604800 },
  },
  opaco: {
    symbol: 'ZBCUSDT', equipo: [], equipo_identificado: false,
    presencia: {}, actividad: {}, financiacion: [], hitos: [],
    estado_general: 'opaco', no_encontrado_en: ['equipo', 'presencia pública', 'actividad'],
    generated_at: '2026-06-19T07:00:00+00:00',
    frescura: { estado: 'fresco', edad_seg: 9000, generated_at: '2026-06-19T07:00:00+00:00', umbral_seg: 604800 },
  },
};

const CH_LABEL = { web: 'Sitio web', sitio_web: 'Sitio web', github: 'GitHub', twitter: 'Redes (X)', telegram: 'Telegram', telegram_discord: 'Telegram', discord: 'Discord', whitepaper: 'Documento técnico' };

Object.assign(window, {
  SP3_NAMES, sp3nm: nm, sp3$: p$, sp3pct1: pct1, sp3pctFrac1: pctFrac1, sp3volUsd: volUsd, sp3edad: edad,
  REGIME, EVAL, LEVELS, PLAN, CONDUCTA, DOSSIER, RAZONES_MUERTE, CH_LABEL, sp3pos30: pos30,
});
