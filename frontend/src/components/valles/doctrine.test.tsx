// doctrine.test.tsx — el textContent de cada pantalla NO emite veredicto.
import { render, act } from '@testing-library/react';
import { it, expect, vi, beforeEach } from 'vitest';
import { ValleysFlow } from './ValleysFlow';
import { Copilot } from './Copilot';
import { IdeaView } from './idea/IdeaView';
import * as api from '../../api';
import type { ValleySnapshot } from '../../types';
import type { ChatMsg } from '../../agent/useAgentStream';

vi.mock('../../api');
const FORBIDDEN = /compra|c[oó]mpr|buena|score|recomend|veredicto|potencial|d[oó]nde operar/i;
const snap: ValleySnapshot = {
  generated_at: '2026-06-14T10:00:00Z', coverage: { universe: 5, evaluated: 5, complete: true },
  candidates: [{ symbol: 'ADAUSDT', price: 0.45, pct_rango: 0.12, semanas_consolidando: 6, vol_percentil: 0.2, volumen_usd_dia: 1e7, distancia_ath_pct: 0.7, razones_vida: [] }],
  frescura: { estado: 'fresco', edad_seg: 1800, generated_at: '2026-06-14T10:00:00Z', umbral_seg: 43200 },
};
beforeEach(() => {
  vi.mocked(api.getValleyEval).mockResolvedValue({ symbol: 'ADAUSDT', estado: 'ok', candidata: true, pct_rango: 0.1, semanas_consolidando: 5, vol_percentil: 0.2 } as never);
  vi.mocked(api.getLevels).mockResolvedValue({ symbol: 'ADAUSDT', estado: 'ok', generated_at: null, price_live: 1, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } } as never);
  vi.mocked(api.getDossier).mockResolvedValue({ symbol: 'ADAUSDT', equipo: [], equipo_identificado: false, presencia: {}, actividad: {}, financiacion: [], hitos: [], estado_general: 'opaco', no_encontrado_en: [], generated_at: null } as never);
  vi.mocked(api.getAltSeason).mockResolvedValue({
    generated_at: null, coverage: { universe: 0, evaluated: 0, complete: false },
    dominancia_fetch: { ok: false, fetched_at: null, source: 'coingecko/global' },
    regime: { estado: 'mixto', componentes: {}, votos: { alts: 0, neutral: 0, btc: 0, vivos: 0 }, n_alts_evaluadas: 0 },
  } as never);
});

// ── Mocks para IdeaView ──────────────────────────────────────────────────────
// IdeaChart usa lightweight-charts (no disponible en jsdom); lo reemplazamos
// con un stub div para aislar el test de doctrina del rendering del canvas.
vi.mock('./idea/IdeaChart', () => ({
  IdeaChart: () => <div data-testid="idea-chart-stub" />,
}));

// useValleyBundle provee los datos de vida/niveles/dossier.
vi.mock('./useValleyBundle', () => ({
  useValleyBundle: () => ({
    vida: { data: { symbol: 'ADAUSDT', estado: 'ok', candidata: true, vivo: true, pct_rango: 0.1, semanas_consolidando: 4, vol_percentil: 0.2, volumen_usd_dia: 5e6 }, loading: false, error: false },
    niveles: { data: { symbol: 'ADAUSDT', estado: 'ok', generated_at: null, price_live: 0.45, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } }, loading: false, error: false },
    dossier: { data: null, loading: false, error: true },
    refreshDossier: vi.fn(),
  }),
}));

// useJugada provee el plan derivado y el estado vivo.
vi.mock('./jugada/useJugada', () => ({
  useJugada: () => ({
    derived: {
      data: {
        symbol: 'ADAUSDT', entry: 0.44, rungs: [{ price: 0.50, sizeFrac: 0.5, toques: 2 }],
        sl_plan: 0.40, be_trigger: null, runner_frac: null,
      },
      loading: false, error: null,
    },
    live: { data: null, loading: false, error: null },
    conducta: { data: null, loading: false, error: null },
  }),
}));

it('el chrome del flujo (pick) no emite veredicto', () => {
  const { container } = render(<ValleysFlow snapshot={snap} loading={false} />);
  // las SUGG del copiloto incluyen "comprar" a propósito (son preguntas que se RECHAZAN),
  // así que este gate corre sobre el chrome del flujo, no sobre el dock abierto.
  expect(container.textContent ?? '').not.toMatch(FORBIDDEN);
});

// ── Doctrine: IdeaView completa no emite veredicto ──────────────────────────
//
// IdeaView es la superficie donde ahora vive el riesgo real: muestra la
// narrativa descriptiva, el plan derivado y el CTA "Fijar jugada". El gate
// anti-veredicto debe cubrirla explícitamente.
//
// Mocks: useValleyBundle + useJugada retornan datos representativos con plan
// derivado; IdeaChart sustituido por stub div para evitar lightweight-charts
// en jsdom; confirmPlan mockeado (declarado en vi.mock('../../api') arriba).

it('IdeaView no emite veredicto ni copia prohibida', async () => {
  let container!: HTMLElement;
  await act(async () => {
    ({ container } = render(<IdeaView symbol="ADAUSDT" />));
  });
  expect(container.textContent ?? '').not.toMatch(FORBIDDEN);
});

// ── Doctrine: refusal SSE → vwBubbleRefusal ─────────────────────────────
//
// Monta el Copilot con el hook useAgentStream mockeado. El mock emite
// un mensaje de assistant con refusal: true, que simula el evento SSE
// `refusal` que el backend emite cuando la anti-verdict guard rechaza.
// Verificamos que el bubble recibe la clase vwBubbleRefusal.

vi.mock('../../agent/useAgentStream', () => {
  const refusalMsg: ChatMsg = {
    role: 'assistant',
    text: 'No te digo si comprar ni cuál es "la mejor".',
    refusal: true,
  };
  return {
    useAgentStream: () => ({
      msgs: [refusalMsg],
      loading: false,
      sendTurn: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      confirmProposal: vi.fn(),
      loadConversation: vi.fn(),
      streamGreeting: vi.fn(),
      hydrating: false,
    }),
  };
});

it('un mensaje de refusal recibe la clase vwBubbleRefusal', async () => {
  await act(async () => {
    render(<Copilot onClose={() => {}} />);
  });
  // El bubble del mensaje de refusal debe tener la clase vwBubbleRefusal
  // (módulo CSS → selector buscado por substring del className)
  const bubbles = document.querySelectorAll('[class*="vwBubbleRefusal"]');
  expect(bubbles.length).toBeGreaterThan(0);
  expect(bubbles[0].textContent).toMatch(/no te digo/i);
});
