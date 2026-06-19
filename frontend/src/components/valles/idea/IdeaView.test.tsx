// IdeaView.test.tsx — TDD para el componente IdeaView
//
// Mocks:
//   - useValleyBundle   (su módulo)
//   - useJugada         (su módulo)
//   - ../../../api       (confirmPlan)
//   - ./IdeaChart        (gráfico lightweight-charts → div simple)
//
// Cobertura:
//   1. Con derived con rungs y live.estado_vivo=null → "Fijar esta jugada" visible
//   2. Con live.estado_vivo='activo' → estado "en curso" visible, botón NO
//   3. Índice de navegación tiene los 5 anclajes
//   4. Placeholder de carga mientras niveles está cargando

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
// ── mocks ─────────────────────────────────────────────────────────────────────
// Usamos vi.hoisted para que las referencias a los mocks puedan usarse
// tanto en vi.mock factories (que se elevan al top del archivo) como
// en los tests que vienen después.

const { mockConfirmPlan, mockUseValleyBundle, mockUseJugada } = vi.hoisted(() => ({
  mockConfirmPlan:     vi.fn(),
  mockUseValleyBundle: vi.fn(),
  mockUseJugada:       vi.fn(),
}));

vi.mock('./IdeaChart', () => ({
  IdeaChart: (props: { symbol: string }) => (
    <div data-testid="idea-chart" data-symbol={props.symbol} />
  ),
}));

vi.mock('../../../api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../../api')>();
  return { ...original, confirmPlan: mockConfirmPlan };
});

vi.mock('../useValleyBundle', () => ({
  useValleyBundle: (...args: unknown[]) => mockUseValleyBundle(...args),
}));

vi.mock('../jugada/useJugada', () => ({
  useJugada: (...args: unknown[]) => mockUseJugada(...args),
}));

// ── fixtures ──────────────────────────────────────────────────────────────────

import type { ValleyEval, SrLevels, PlanDerived, PlanLive, PlanConducta, Dossier } from '../../../types';

const mockVida: ValleyEval = {
  symbol:            'ADAUSDT',
  estado:            'ok',
  candidata:         true,
  vivo:              true,
  pos_in_30d_range:  0.12,
  rsi14:             38,
  pct_vs_sma20:      -6,
  pct_vs_sma50:      -9,
  consol_30d:        40,
  vol_ratio:         0.7,
  drawdown_from_90h: -35,
  volumen_usd_dia:   820000,
};

const mockLevels: SrLevels = {
  symbol: 'ADAUSDT',
  estado: 'ok',
  generated_at: null,
  price_live: 0.42,
  zonas: [
    { tipo: 'resistencia', centro: 0.448, precio_bajo: 0.445, precio_alto: 0.451, toques: 3, confluencia_redondo: [] },
    { tipo: 'soporte', centro: 0.385, precio_bajo: 0.382, precio_alto: 0.388, toques: 4, confluencia_redondo: [] },
  ],
  ubicacion: {
    dentro_de: null,
    techo: { centro: 0.448, dist_pct: 6.7 },
    piso: { centro: 0.385, dist_pct: -8.3 },
  },
};

const mockDerived: PlanDerived = {
  entry: 0.419,
  sl_plan: 0.382,
  sl_piso: { centro: 0.385, precio_bajo: 0.382, precio_alto: 0.388, toques: 4 },
  entry_zone: { centro: 0.419, precio_bajo: 0.412, precio_alto: 0.426, toques: 5 },
  rungs: [
    { tp_price: 0.448, size_frac: 0.50, zona: { centro: 0.448, precio_bajo: 0.445, precio_alto: 0.451, toques: 3 } },
  ],
  runner_frac: 0.05,
};

const mockLiveNull: PlanLive = {
  symbol: 'ADAUSDT',
  estado_vivo: null,
};

const mockLiveActivo: PlanLive = {
  symbol: 'ADAUSDT',
  estado_vivo: 'activo',
  hechos: ['Entrada ejecutada en $0.419', 'Primer peldaño a $0.448'],
  frescura: { estado: 'fresco', edad_seg: 30, generated_at: null, umbral_seg: 120 },
};

const mockConducta: PlanConducta = {
  symbol: 'ADAUSDT',
  estado_vivo: null,
};

const mockDossier: Dossier = {
  symbol: 'ADAUSDT',
  estado_general: 'opaco',
  equipo: [],
  equipo_identificado: false,
  presencia: {},
  actividad: {},
  financiacion: [],
  hitos: [],
  no_encontrado_en: [],
  generated_at: null,
};

function makeBundleState<T>(data: T | null, loading = false, error = false) {
  return { data, loading, error };
}

function defaultBundle() {
  return {
    vida: makeBundleState(mockVida),
    niveles: makeBundleState(mockLevels),
    dossier: makeBundleState(mockDossier),
    refreshDossier: vi.fn(),
  };
}

function defaultJugada(liveOverride?: Partial<PlanLive>) {
  return {
    derived: makeBundleState(mockDerived),
    live: makeBundleState(liveOverride !== undefined ? { ...mockLiveNull, ...liveOverride } : mockLiveNull),
    conducta: makeBundleState(mockConducta),
  };
}

// ── import del componente ──────────────────────────────────────────────────────

import { IdeaView } from './IdeaView';

// ── setup ──────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  mockConfirmPlan.mockResolvedValue({ symbol: 'ADAUSDT', estado_vivo: 'activo', plan: mockDerived });
});

// ── suites ────────────────────────────────────────────────────────────────────

describe('IdeaView — índice de navegación', () => {
  it('tiene exactamente 5 anclajes sticky: vida, paredes, jugada, quien, noticias', () => {
    mockUseValleyBundle.mockReturnValue(defaultBundle());
    mockUseJugada.mockReturnValue(defaultJugada());

    render(<IdeaView symbol="ADAUSDT" />);

    const nav = document.querySelector('nav[aria-label]') ?? document.querySelector('nav');
    expect(nav).not.toBeNull();

    // Los 5 links a sus anclas
    const links = nav!.querySelectorAll('a[href]');
    const hrefs = Array.from(links).map((l) => l.getAttribute('href'));
    expect(hrefs).toContain('#idea-vida');
    expect(hrefs).toContain('#idea-paredes');
    expect(hrefs).toContain('#idea-jugada');
    expect(hrefs).toContain('#idea-quien');
    expect(hrefs).toContain('#idea-noticias');
  });
});

describe('IdeaView — caso base (derived con rungs, live=null)', () => {
  it('renderiza el chart placeholder (IdeaChart)', () => {
    mockUseValleyBundle.mockReturnValue(defaultBundle());
    mockUseJugada.mockReturnValue(defaultJugada());

    render(<IdeaView symbol="ADAUSDT" />);
    expect(screen.getByTestId('idea-chart')).toBeTruthy();
  });

  it('renderiza los headings de Narrativa', () => {
    mockUseValleyBundle.mockReturnValue(defaultBundle());
    mockUseJugada.mockReturnValue(defaultJugada());

    render(<IdeaView symbol="ADAUSDT" />);
    expect(screen.getByRole('heading', { name: /¿Está viva\?/i })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /¿Dónde está entre sus paredes\?/i })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /Si decides entrar/i })).toBeTruthy();
  });

  it('muestra el botón "Fijar esta jugada" cuando derived tiene rungs y live.estado_vivo=null', () => {
    mockUseValleyBundle.mockReturnValue(defaultBundle());
    mockUseJugada.mockReturnValue(defaultJugada());

    render(<IdeaView symbol="ADAUSDT" />);
    expect(screen.getByRole('button', { name: /Fijar esta jugada/i })).toBeTruthy();
  });

  it('botón "Mirar otra moneda" llama onRestart', () => {
    mockUseValleyBundle.mockReturnValue(defaultBundle());
    mockUseJugada.mockReturnValue(defaultJugada());
    const onRestart = vi.fn();

    render(<IdeaView symbol="ADAUSDT" onRestart={onRestart} />);
    const btn = screen.getByRole('button', { name: /Mirar otra moneda/i });
    fireEvent.click(btn);
    expect(onRestart).toHaveBeenCalledOnce();
  });
});

describe('IdeaView — "Fijar esta jugada" flujo', () => {
  it('llama confirmPlan y muestra "Jugada fijada" en success', async () => {
    mockUseValleyBundle.mockReturnValue(defaultBundle());
    mockUseJugada.mockReturnValue(defaultJugada());

    render(<IdeaView symbol="ADAUSDT" />);
    const btn = screen.getByRole('button', { name: /Fijar esta jugada/i });
    fireEvent.click(btn);

    expect(mockConfirmPlan).toHaveBeenCalledWith('ADAUSDT', 0.42);
    await waitFor(() => {
      expect(screen.getByText(/Jugada fijada/i)).toBeTruthy();
    });
  });

  it('muestra mensaje de error cuando confirmPlan falla', async () => {
    mockConfirmPlan.mockRejectedValue(new Error('Network error'));
    mockUseValleyBundle.mockReturnValue(defaultBundle());
    mockUseJugada.mockReturnValue(defaultJugada());

    render(<IdeaView symbol="ADAUSDT" />);
    const btn = screen.getByRole('button', { name: /Fijar esta jugada/i });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByText(/No se pudo fijar/i)).toBeTruthy();
    });
  });
});

describe('IdeaView — live.estado_vivo="activo"', () => {
  it('muestra estado "en curso" con hechos cuando activo', () => {
    mockUseValleyBundle.mockReturnValue(defaultBundle());
    mockUseJugada.mockReturnValue({
      derived: makeBundleState(mockDerived),
      live: makeBundleState(mockLiveActivo),
      conducta: makeBundleState(mockConducta),
    });

    render(<IdeaView symbol="ADAUSDT" />);

    // Debe mostrar el estado en curso
    expect(screen.getByText(/en curso/i)).toBeTruthy();
    // Debe mostrar los hechos
    expect(screen.getByText(/Entrada ejecutada/i)).toBeTruthy();
  });

  it('NO muestra "Fijar esta jugada" cuando activo', () => {
    mockUseValleyBundle.mockReturnValue(defaultBundle());
    mockUseJugada.mockReturnValue({
      derived: makeBundleState(mockDerived),
      live: makeBundleState(mockLiveActivo),
      conducta: makeBundleState(mockConducta),
    });

    render(<IdeaView symbol="ADAUSDT" />);
    expect(screen.queryByRole('button', { name: /Fijar esta jugada/i })).toBeNull();
  });
});

describe('IdeaView — placeholder de carga', () => {
  it('muestra placeholder cuando niveles está cargando y no hay data', () => {
    mockUseValleyBundle.mockReturnValue({
      vida: makeBundleState(null, true),
      niveles: makeBundleState(null, true),
      dossier: makeBundleState(null, true),
      refreshDossier: vi.fn(),
    });
    mockUseJugada.mockReturnValue({
      derived: makeBundleState(null),
      live: makeBundleState(null),
      conducta: makeBundleState(null),
    });

    render(<IdeaView symbol="ADAUSDT" />);
    // Chart NO debe aparecer durante la carga inicial
    expect(screen.queryByTestId('idea-chart')).toBeNull();
    // Pero la nav sí debe estar
    const nav = document.querySelector('nav');
    expect(nav).not.toBeNull();
  });
});
