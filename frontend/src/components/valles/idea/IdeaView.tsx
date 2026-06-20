// ============================================================
// IdeaView.tsx — vista única "idea de moneda" (SP3)
//
// Port 1:1 de docs/superpowers/handoffs/sp3/sp3-ideaview.jsx
// (líneas 250-462: PlayNow, Dossier, NAV, IdeaView), conservando
// la lógica real (useValleyBundle, useJugada, confirmPlan).
//
// Estructura:
//   .iv > .iv__frame > (
//     RegimeFrame,             ← marco de régimen dominante
//     RegimeStrip (sticky),    ← franja persistente del clima
//     .iv__body > (
//       nav,                   ← Vida·Paredes·Jugada·Quién·Noticias
//       head,                  ← eyebrow + título + precio
//       chart-wide,            ← IdeaChart (callout honesto si no hay datos)
//       narrativa,             ← Vida · Paredes · Jugada (descriptivo)
//       PlayNow,               ← lifecycle de "Tu jugada ahora"
//       Dossier,               ← "Quién está detrás"
//       Noticias,              ← vacío honesto
//       footer,                ← "← Mirar otra moneda"
//     )
//   )
//
// DOCTRINA: el régimen ENMARCA pero NO modula la moneda. Cero
// color/orden/énfasis condicionado por el régimen. Texto VERBATIM
// del mockup intacto. Tuteo venezolano. Sin lenguaje de veredicto.
// ============================================================

import React, { useEffect, useState } from 'react';
import { IdeaChart } from './IdeaChart';
import { Narrativa } from './Narrativa';
import { Fresh, RegimeFrame, RegimeStrip } from '../regime/RegimeFrame';
import { humanName } from '../atoms';
import { useValleyBundle } from '../useValleyBundle';
import { useJugada } from '../jugada/useJugada';
import { confirmPlan, getAltSeason } from '../../../api';
import { formatPrice } from '../../../utils';
import type {
  PlanLive, PlanConducta, Dossier, RegimeSnapshot,
} from '../../../types';
import type { AsyncState as JugadaAsync } from '../jugada/useJugada';
import type { LiveState } from '../jugada/overlays';
import styles from './idea.module.css';

// ── helper: extrae LiveState de PlanLive ─────────────────────
function liveStateFrom(pl: PlanLive | null): LiveState | null {
  if (!pl?.realidad) return null;
  return {
    rungs_llenos: pl.realidad.rungs_llenos ?? [],
    be_movido:    !!pl.realidad.be_movido,
    sl_actual:    pl.realidad.sl_actual ?? null,
  };
}

// ── CH_LABEL (port de sp3-data.jsx) ──────────────────────────
const CH_LABEL: Record<string, string> = {
  sitio_web: 'Sitio web',
  github: 'GitHub',
  twitter: 'Redes (X)',
  telegram_discord: 'Telegram',
  whitepaper: 'Documento técnico',
};

// ════════════════════════════════════════════════════════════
// ⑤ TU JUGADA AHORA — lifecycle (port de sp3-ideaview.jsx:253-302)
//
// El mockup recibe un `play` con forma:
//   { estado_vivo, titular?, campos?, hechos?, frescura?, _fijada, _recienFijada }
// Aquí se construye con DATOS REALES:
//   - estado_vivo  ← live.data.estado_vivo
//   - hechos       ← live.data.hechos
//   - frescura     ← live.data.frescura
//   - titular/campos (cerrado) ← conducta.data
//   - _fijada / _recienFijada  ← estado LOCAL (confirmPlan)
// ════════════════════════════════════════════════════════════
interface PlayModel {
  estado_vivo: 'activo' | 'incierto' | 'cerrado' | null;
  titular?: string;
  campos?: PlanConducta['campos'];
  hechos?: string[];
  frescura?: PlanLive['frescura'];
  _fijada: boolean;
  _recienFijada: boolean;
}

const PlayNow: React.FC<{
  play: PlayModel | null;
  onFijar: () => void;
  enviando: boolean;
  ctaError: string | null;
}> = ({ play, onFijar, enviando, ctaError }) => {
  if (!play || play.estado_vivo == null) {
    return (
      <div className={`${styles.play} ${styles['play--empty']}`}>
        <div className={styles['play__lead']}>
          No hay plan calculado ahora mismo. Puedes revisar los bloques de arriba para ver el estado actual.
        </div>
      </div>
    );
  }

  if (play.estado_vivo === 'cerrado') {
    const ICON: Record<string, string> = { si: '✓', no: '○', dato: '·' };
    return (
      <div className={styles.play}>
        <div className={styles['play__lead']}><b>{play.titular}</b></div>
        {play.campos && play.campos.length > 0 && (
          <ul className={styles['play__close-fields']}>
            {play.campos.map((c, i) => (
              <li className={styles['play__cf']} key={i}>
                <span className={`${styles['play__cf-i']} ${styles[`play__cf-i--${c.ok}`]}`}>{ICON[c.ok]}</span>
                {c.k}{c.v && <span className={styles['play__cf-v']}>{c.v}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  const incierto = play.estado_vivo === 'incierto';
  const planListo = play.estado_vivo === 'activo' && play._fijada === false;
  const recienFijada = play._recienFijada;

  return (
    <div className={`${styles.play} ${incierto ? styles['play--incierto'] : ''}`}>
      {recienFijada ? (
        <div className={styles['play__status']}>
          <b>Jugada fijada</b> — se sigue en vivo. <Fresh frescura={play.frescura} noun="lectura" />
        </div>
      ) : planListo ? (
        <>
          <div className={styles['play__lead']}>
            El plan está listo. Si decides entrar, fija la jugada y el sistema la sigue en vivo.
          </div>
          <button
            className={styles['play__cta']}
            onClick={onFijar}
            disabled={enviando}
            type="button"
          >
            {enviando ? 'Fijando…' : 'Fijar esta jugada'}
          </button>
          {ctaError && <div className={styles['play__error']}>{ctaError}</div>}
        </>
      ) : (
        <>
          <div className={styles['play__status']}>
            <b>{incierto ? 'Jugada incierta' : 'Jugada en curso'}</b> · <Fresh frescura={play.frescura} noun="lectura" />
          </div>
          {incierto && (
            <div className={styles['play__lead']} style={{ marginTop: 12 }}>
              El sistema no está seguro de dónde está la jugada — revisa en Binance.
            </div>
          )}
          {play.hechos && play.hechos.length > 0 && (
            <ul className={styles['play__facts']}>
              {play.hechos.map((h, i) => (
                <li className={styles['play__fact']} key={i}>
                  <span className={`${styles['play__fact-i']} ${styles['play__fact-i--dato']}`}>·</span>{h}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
};

// ════════════════════════════════════════════════════════════
// ⑥ QUIÉN ESTÁ DETRÁS — dossier (port de sp3-ideaview.jsx:307-368)
// ════════════════════════════════════════════════════════════
const DossierBody: React.FC<{
  state: { data: Dossier | null; loading: boolean; error: boolean };
  onRefresh: () => void;
}> = ({ state, onRefresh }) => {
  const { data: dossier, loading, error } = state;

  if (loading) {
    return (
      <div className={styles['iv-load']}>
        <span className={styles['iv-load__spin']} />Buscando quién está detrás…
      </div>
    );
  }

  if (error || !dossier || dossier.estado_general === 'no_disponible') {
    return (
      <div className={`${styles['iv-callout']} ${styles['iv-callout--mute']}`}>
        <span className={styles['iv-callout__icon']}>×</span>
        <div>
          <div className={styles['iv-callout__t']}>No se pudo averiguar ahora</div>
          <div className={styles['iv-callout__s']}>
            Falló la búsqueda. Es un problema de la herramienta, no del proyecto.
          </div>
          <button className={styles['dos__retry']} onClick={onRefresh} type="button">↻ Intentar de nuevo</button>
        </div>
      </div>
    );
  }

  if (dossier.estado_general === 'opaco') {
    return (
      <div className={styles.dos}>
        <div className={styles['dos__lead-row']}>
          <div className={`${styles['dos__icon']} ${styles['dos__icon--opaco']}`}>◍</div>
          <div>
            <div className={styles['dos__lead']}>No se encontró quién está detrás</div>
            <div className={styles['dos__say']}>
              Se buscó equipo, presencia y actividad pública, y no apareció nada. Eso es un dato sobre el proyecto, no una falla de la herramienta.
              {dossier.no_encontrado_en && dossier.no_encontrado_en.length > 0 && (
                <> No se halló en: {dossier.no_encontrado_en.join(', ')}.</>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const channels = Object.keys(dossier.presencia || {});
  return (
    <div className={styles.dos}>
      <div className={styles['dos__lead-row']}>
        <div className={styles['dos__icon']}>☻</div>
        <div>
          <div className={styles['dos__lead']}>Se sabe quién está detrás</div>
          <div className={styles['dos__say']}>
            Hay nombres y canales públicos, y cada dato se puede comprobar en su fuente.
          </div>
        </div>
      </div>
      <div className={styles['dos__people']}>
        {dossier.equipo.map((m, i) => (
          <div className={styles['dos__person']} key={i}>
            <div className={styles['dos__face']}>☻</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className={styles['dos__name']}>
                {m.nombre}{m.rol && <span className={styles['dos__role']}> · {m.rol}</span>}
              </div>
            </div>
            {m.fuente && (
              <a className={styles['dos__src']} href={m.fuente} target="_blank" rel="noreferrer">fuente</a>
            )}
          </div>
        ))}
      </div>
      <div className={styles['dos__channels']}>
        {channels.map((k) => {
          const p = dossier.presencia[k];
          const st = p.activo === 'si' ? 'activo' : p.activo === 'no' ? 'inactivo' : 'sin confirmar';
          return (
            <a className={styles['dos__ch']} key={k} href={p.url || '#'} target="_blank" rel="noreferrer">
              <span className={`${styles['dos__ch-dot']} ${styles[`dos__ch-dot--${p.activo}`]}`} />
              {CH_LABEL[k] || k} <span className={styles['dos__ch-state']}>· {st}</span>
              {p.fuente && <span className={styles['dos__ch-src']}>· fuente</span>}
            </a>
          );
        })}
      </div>
    </div>
  );
};

// ── régimen hook local (fetch /alt-season) ───────────────────
function useRegime(): RegimeSnapshot | null {
  const [regime, setRegime] = useState<RegimeSnapshot | null>(null);
  useEffect(() => {
    let alive = true;
    getAltSeason()
      .then((s) => { if (alive) setRegime(s); })
      .catch(() => { if (alive) setRegime(null); });
    return () => { alive = false; };
  }, []);
  return regime;
}

// ── nav (port de sp3-ideaview.jsx:373-379) ───────────────────
const NAV = [
  { id: 'idea-vida', label: 'Vida' },
  { id: 'idea-paredes', label: 'Paredes' },
  { id: 'idea-jugada', label: 'Jugada' },
  { id: 'idea-quien', label: 'Quién' },
  { id: 'idea-noticias', label: 'Noticias' },
];

// ── props ─────────────────────────────────────────────────────
export interface IdeaViewProps {
  symbol: string;
  onRestart?: () => void;
}

// ── componente principal ──────────────────────────────────────
export const IdeaView: React.FC<IdeaViewProps> = ({ symbol, onRestart }) => {
  const regime    = useRegime();
  const bundle    = useValleyBundle(symbol);
  const livePrice = bundle.niveles.data?.price_live ?? null;
  const { derived, live, conducta } = useJugada(symbol, livePrice);

  // Estado local del CTA "Fijar jugada"
  const [enviando, setEnviando] = useState(false);
  const [fijada, setFijada]     = useState(false);
  const [ctaError, setCtaError] = useState<string | null>(null);

  const handleFijar = async () => {
    if (enviando || fijada) return;
    const entry = livePrice ?? derived.data?.entry;
    if (entry == null) return;
    setEnviando(true);
    setCtaError(null);
    try {
      await confirmPlan(symbol, entry);
      setFijada(true);
    } catch {
      setCtaError('No se pudo fijar la jugada. Intenta de nuevo en un momento.');
    } finally {
      setEnviando(false);
    }
  };

  // ── datos de cabecera ───────────────────────────────────────
  const vida   = bundle.vida.data;
  const levels = bundle.niveles.data;
  const price  = levels?.price_live ?? vida?.price ?? null;

  // ¿Estado inicial de carga? (ningún dato disponible aún)
  const initialLoading = bundle.niveles.loading && !bundle.niveles.data;

  // chart no-disponible: vida y niveles ambos caídos
  const evalUnavailable = !vida || vida.estado === 'no_disponible';
  const levelsUnavailable = !levels || levels.estado === 'no_disponible';
  const chartUnavailable = evalUnavailable && levelsUnavailable;

  // ── modelo de jugada (live real + estado local + conducta) ───
  const estadoVivo = live.data?.estado_vivo ?? null;
  const cerradoConducta = conducta.data?.estado_vivo === 'cerrado';
  const hayPlan = !!derived.data && derived.data.rungs.length > 0;

  const play: PlayModel | null = buildPlay({
    estadoVivo,
    live: live.data,
    conducta: conducta.data,
    cerradoConducta,
    hayPlan,
    fijada,
  });

  return (
    <div className={styles.iv}>
      <div className={styles['iv__frame']}>

        {/* ── marco de régimen (dominante) + franja sticky ─────── */}
        <RegimeFrame regime={regime} />
        <RegimeStrip regime={regime} />

        <div className={styles['iv__body']}>

          {/* ① nav de secciones (sticky bajo la franja) ────────── */}
          <nav className={styles['iv-nav']} aria-label="Secciones de la moneda">
            {NAV.map((n, i) => (
              <React.Fragment key={n.id}>
                {i > 0 && <span className={styles['iv-nav__sep']} aria-hidden="true" />}
                <a
                  className={`${styles['iv-nav__a']} ${i === 0 ? styles['iv-nav__a--on'] : ''}`}
                  href={`#${n.id}`}
                >
                  {n.label}
                </a>
              </React.Fragment>
            ))}
          </nav>

          {/* ② cabecera de la moneda ───────────────────────────── */}
          <div className={styles['iv-col']}>
            <div className={styles['iv-head']}>
              <div className={styles['iv-head__eyebrow']}>
                <span className={styles['iv-head__coin']}>{humanName(symbol)}</span>
                <span className={styles['iv-head__sym']}>{symbol}</span>
              </div>
              <h1 className={styles['iv-head__title']}>{humanName(symbol)}</h1>
              {price != null && (
                <div className={styles['iv-head__price']}>
                  ${formatPrice(price)}<small>último cierre</small>
                </div>
              )}
            </div>
          </div>

          {/* ③ gráfico — ancho ─────────────────────────────────── */}
          <div className={styles['iv-wide']}>
            {initialLoading ? (
              <div className={styles['idea-chart-placeholder']} aria-busy="true">
                Cargando el gráfico…
              </div>
            ) : chartUnavailable ? (
              <div className={`${styles['iv-callout']} ${styles['iv-callout--mute']}`} style={{ marginTop: 0 }}>
                <span className={styles['iv-callout__icon']}>⧖</span>
                <div>
                  <div className={styles['iv-callout__t']}>No se pudo revisar esta moneda ahora</div>
                  <div className={styles['iv-callout__s']}>
                    Binance no respondió. Es un problema de la herramienta, no de la moneda — sin campos en blanco fingiendo datos.
                  </div>
                </div>
              </div>
            ) : (
              <IdeaChart
                symbol={symbol}
                vida={vida}
                levels={levels}
                plan={derived.data}
                live={livePrice ?? 0}
                state={liveStateFrom(live.data)}
                height={520}
              />
            )}
          </div>

          {/* columna de lectura: narrativa + secciones ─────────── */}
          <div className={styles['iv-col']}>

            {/* ④ narrativa descriptiva */}
            <Narrativa vida={vida} levels={levels} plan={derived.data} />

            {/* ⑤ tu jugada ahora */}
            <section className={styles['iv-sec']} id="idea-jugada-now">
              <div className={styles['iv-sec__h']}>Tu jugada ahora</div>
              <h2 className={styles['iv-sec__q']}>El estado vivo de tu plan</h2>
              <PlayNow
                play={play}
                onFijar={handleFijar}
                enviando={enviando}
                ctaError={ctaError}
              />
            </section>

            {/* ⑥ quién está detrás */}
            <section className={styles['iv-sec']} id="idea-quien">
              <div className={styles['iv-sec__h']}>Quién está detrás</div>
              <h2 className={styles['iv-sec__q']}>¿Se sabe quién sostiene el proyecto?</h2>
              <DossierBody state={bundle.dossier} onRefresh={bundle.refreshDossier} />
            </section>

            {/* ⑦ noticias — vacío honesto */}
            <section className={styles['iv-sec']} id="idea-noticias">
              <div className={styles['iv-sec__h']}>Lo último que se dijo</div>
              <div className={styles['news__empty']}>
                Las noticias de esta moneda aún no están conectadas.
              </div>
            </section>

            {/* ⑧ footer */}
            <div className={styles['iv-foot']}>
              <button
                className={styles['iv-foot__btn']}
                onClick={() => onRestart?.()}
                type="button"
              >
                ← Mirar otra moneda
              </button>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
};

// ── construye el PlayModel a partir de los datos reales ──────
function buildPlay(args: {
  estadoVivo: 'activo' | 'incierto' | 'cerrado' | null;
  live: PlanLive | null;
  conducta: PlanConducta | null;
  cerradoConducta: boolean;
  hayPlan: boolean;
  fijada: boolean;
}): PlayModel | null {
  const { estadoVivo, live, conducta, cerradoConducta, hayPlan, fijada } = args;

  // Cerrado: el titular/campos vienen de conducta
  if (estadoVivo === 'cerrado' || cerradoConducta) {
    return {
      estado_vivo: 'cerrado',
      titular: conducta?.titular,
      campos: conducta?.campos,
      _fijada: fijada,
      _recienFijada: false,
    };
  }

  // En curso / incierto: estado vivo REAL (posición ya ejecutada).
  // _fijada = true ⇒ NO se muestra el CTA "Fijar"; se muestra el
  // status "en curso" con los hechos. _recienFijada = false ⇒ no es
  // la confirmación local recién hecha, sino el estado vivo del backend.
  if (estadoVivo === 'activo' || estadoVivo === 'incierto') {
    return {
      estado_vivo: estadoVivo,
      hechos: live?.hechos,
      frescura: live?.frescura,
      _fijada: true,
      _recienFijada: false,
    };
  }

  // Sin estado vivo pero con plan derivado: el CTA "Fijar" vive aquí.
  // Tras fijar (estado local) mostramos "Jugada fijada".
  if (hayPlan) {
    if (fijada) {
      return {
        estado_vivo: 'activo',
        frescura: live?.frescura,
        _fijada: true,
        _recienFijada: true,
      };
    }
    return {
      estado_vivo: 'activo',
      frescura: live?.frescura,
      _fijada: false,
      _recienFijada: false,
    };
  }

  // Sin plan: empty honesto
  return null;
}

// Re-export del tipo del estado async de jugada (uso interno)
export type { JugadaAsync };
