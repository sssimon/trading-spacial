// doctrine.test.tsx — el textContent de cada pantalla NO emite veredicto.
import { render, act } from '@testing-library/react';
import { it, expect, vi, beforeEach } from 'vitest';
import { ValleysFlow } from './ValleysFlow';
import { Copilot } from './Copilot';
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
});

it('el chrome del flujo (pick) no emite veredicto', () => {
  const { container } = render(<ValleysFlow snapshot={snap} loading={false} />);
  // las SUGG del copiloto incluyen "comprar" a propósito (son preguntas que se RECHAZAN),
  // así que este gate corre sobre el chrome del flujo, no sobre el dock abierto.
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
