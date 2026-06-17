// ============================================================
// IdeaView.tsx — vista única "idea de moneda"
//
// Orquesta para un símbolo dado:
//   1. Sticky nav (5 anclas)
//   2. Eyebrow + título
//   3. IdeaChart (gráfico unificado)
//   4. Narrativa (vida · paredes · jugada descriptiva)
//   5. Jugada lifecycle compacto (interactivo)
//   6. Quién está detrás (dossier inlineado — NO usa el wrapper vwScreen)
//   7. NoticiasSection
//   8. Footer "Mirar otra moneda"
//
// Nota sobre Quién/FundScreen:
//   FundScreen envuelve todo en `vwScreen` (animación fade) y renderiza
//   un <Eyebrow> propio + <h2 className=vwQuestion>. Eso traería un eyebrow
//   duplicado y un heading-grande que rompe la jerarquía de IdeaView.
//   Decisión: se inlinea el cuerpo del dossier bajo <h2>Quién está detrás</h2>
//   reutilizando las clases de valles.module.css y los mismos átomos
//   (Loading, Callout, Retry) que FundScreen usa internamente.
// ============================================================

import React, { useState } from 'react';
import { IdeaChart } from './IdeaChart';
import { Narrativa } from './Narrativa';
import { NoticiasSection } from './NoticiasSection';
import { Eyebrow, humanName, Loading, Callout, Retry } from '../atoms';
import { FreshnessTag } from '../../FreshnessTag';
import { useValleyBundle } from '../useValleyBundle';
import { useJugada } from '../jugada/useJugada';
import { confirmPlan } from '../../../api';
import type { PlanLive, Dossier } from '../../../types';
import type { AsyncState } from '../useValleyBundle';
import type { LiveState } from '../jugada/overlays';
import styles from './idea.module.css';
import vwStyles from '../valles.module.css';

// ── helper: extrae LiveState de PlanLive ─────────────────────
function liveStateFrom(pl: PlanLive | null): LiveState | null {
  if (!pl?.realidad) return null;
  return {
    rungs_llenos: pl.realidad.rungs_llenos ?? [],
    be_movido:    !!pl.realidad.be_movido,
    sl_actual:    pl.realidad.sl_actual ?? null,
  };
}

// ── CH_LABEL (igual que FundScreen) ──────────────────────────
const CH_LABEL: Record<string, string> = {
  sitio_web: 'Sitio web',
  github: 'GitHub',
  twitter: 'Redes (X)',
  telegram_discord: 'Telegram',
  whitepaper: 'Documento técnico',
};

const Fuente: React.FC<{ url: string | null }> = ({ url }) =>
  url ? (
    <a className={vwStyles.vwSrc} href={url} target="_blank" rel="noreferrer">
      fuente
    </a>
  ) : null;

// ── bloque Quién inlineado ────────────────────────────────────
const DossierBody: React.FC<{ state: AsyncState<Dossier>; onRefresh: () => void }> = ({
  state,
  onRefresh,
}) => {
  const { data, loading, error } = state;

  if (loading) return <Loading label="Buscando quién está detrás…" />;

  if (error || !data || data.estado_general === 'no_disponible') {
    return (
      <Callout
        tone="mute"
        icon="×"
        title="No se pudo averiguar ahora"
        sub="Falló la búsqueda. Es un problema de la herramienta, no del proyecto."
      >
        <Retry onClick={onRefresh} />
      </Callout>
    );
  }

  if (data.estado_general === 'opaco') {
    return (
      <Callout
        tone="ochre"
        icon="◍"
        title="No se encontró quién está detrás"
        sub={
          <>
            Se buscó equipo, presencia y actividad pública, y{' '}
            <b>no apareció nada</b>. Eso es un dato sobre el proyecto, no una falla de la
            herramienta.
            {data.no_encontrado_en.length > 0 && (
              <> No se halló en: {data.no_encontrado_en.join(', ')}.</>
            )}
          </>
        }
      />
    );
  }

  return (
    <div className={vwStyles.vwAnswer}>
      <div className={vwStyles.vwAnswerRow}>
        <div className={vwStyles.vwAnswerIcon} aria-hidden="true">☻</div>
        <div>
          <div className={vwStyles.vwAnswerLead}>Se sabe quién está detrás</div>
          <div className={vwStyles.vwAnswerSay}>
            Hay nombres y canales públicos, y cada dato se puede comprobar en su fuente.
          </div>
        </div>
      </div>
      {data.equipo.length > 0 && (
        <div className={vwStyles.vwPeople}>
          {data.equipo.map((m, i) => (
            <div className={vwStyles.vwPerson} key={i}>
              <div className={vwStyles.vwPersonFace} aria-hidden="true">☻</div>
              <div>
                <div className={vwStyles.vwPersonName}>
                  {m.nombre}{m.rol ? ` · ${m.rol}` : ''}
                </div>
                <Fuente url={m.fuente} />
              </div>
            </div>
          ))}
        </div>
      )}
      <div className={vwStyles.vwChannels}>
        {Object.entries(data.presencia).map(([k, c]) => (
          <div className={vwStyles.vwChannel} key={k}>
            <span
              className={`${vwStyles.vwChannelDot} ${vwStyles[`vwChannelDot_${c.activo}`]}`}
              aria-hidden="true"
            />
            <span>
              {CH_LABEL[k] ?? k.replace(/_/g, ' ')} ·{' '}
              <span className={vwStyles.vwChannelState}>
                {c.activo === 'si' ? 'activo' : c.activo === 'no' ? 'inactivo' : 'sin confirmar'}
              </span>
            </span>
            <Fuente url={c.url ?? c.fuente} />
          </div>
        ))}
      </div>
      {data.frescura && (
        <div className={vwStyles.vwFundFresh}>
          <FreshnessTag frescura={data.frescura} />
        </div>
      )}
    </div>
  );
};

// ── props ─────────────────────────────────────────────────────
export interface IdeaViewProps {
  symbol: string;
  onRestart?: () => void;
}

// ── componente principal ──────────────────────────────────────
export const IdeaView: React.FC<IdeaViewProps> = ({ symbol, onRestart }) => {
  const bundle    = useValleyBundle(symbol);
  const livePrice = bundle.niveles.data?.price_live ?? bundle.vida.data?.price ?? null;
  const { derived, live, conducta } = useJugada(symbol, livePrice);

  // Estado local del CTA "Fijar jugada"
  const [enviando, setEnviando] = useState(false);
  const [fijada, setFijada]     = useState(false);
  const [ctaError, setCtaError] = useState<string | null>(null);

  const handleFijar = async () => {
    if (enviando || fijada) return;
    setEnviando(true);
    setCtaError(null);
    try {
      await confirmPlan(symbol, livePrice ?? derived.data!.entry);
      setFijada(true);
    } catch {
      setCtaError('No se pudo fijar la jugada. Intenta de nuevo en un momento.');
    } finally {
      setEnviando(false);
    }
  };

  // ¿Estado inicial de carga? (ningún dato disponible aún)
  const initialLoading = bundle.niveles.loading && !bundle.niveles.data;

  // ── estado del lifecycle de jugada ───────────────────────────
  const estadoVivo = live.data?.estado_vivo ?? null;
  const enCurso    = estadoVivo === 'activo' || estadoVivo === 'incierto';
  const cerrado    = estadoVivo === 'cerrado' || conducta.data?.estado_vivo === 'cerrado';
  const hayPlan    = !!derived.data && derived.data.rungs.length > 0;

  return (
    <div className={styles['idea-view']}>

      {/* ── 1. Sticky nav ─────────────────────────────────────── */}
      <nav className={styles['idea-nav']} aria-label="Secciones de la moneda">
        <a href="#idea-vida">Vida</a>
        <span className={styles['idea-nav__sep']} aria-hidden="true">·</span>
        <a href="#idea-paredes">Paredes</a>
        <span className={styles['idea-nav__sep']} aria-hidden="true">·</span>
        <a href="#idea-jugada">Jugada</a>
        <span className={styles['idea-nav__sep']} aria-hidden="true">·</span>
        <a href="#idea-quien">Quién</a>
        <span className={styles['idea-nav__sep']} aria-hidden="true">·</span>
        <a href="#idea-noticias">Noticias</a>
      </nav>

      {/* ── 2. Eyebrow + título ───────────────────────────────── */}
      <div className={styles['idea-header']}>
        <Eyebrow symbol={symbol} />
        <h1 className={styles['idea-title']}>{humanName(symbol)}</h1>
      </div>

      {/* ── 3. Gráfico ────────────────────────────────────────── */}
      {initialLoading ? (
        <div className={styles['idea-chart-placeholder']} aria-busy="true">
          Cargando el gráfico…
        </div>
      ) : (
        <IdeaChart
          symbol={symbol}
          vida={bundle.vida.data}
          levels={bundle.niveles.data}
          plan={derived.data}
          live={livePrice ?? 0}
          state={liveStateFrom(live.data)}
        />
      )}

      {/* ── 4. Narrativa descriptiva ──────────────────────────── */}
      <Narrativa
        vida={bundle.vida.data}
        levels={bundle.niveles.data}
        plan={derived.data}
      />

      {/* ── 5. Jugada lifecycle compacto ─────────────────────── */}
      <section id="idea-jugada-cta" className={styles['na-block']}>
        <h3 className={styles['na-heading']}>Tu jugada ahora</h3>

        {enCurso && (
          <div className={styles['idea-jugada-encurso']}>
            <p className={styles['idea-jugada-estado']}>
              Jugada <b>en curso</b>
              {live.data?.frescura && (
                <span className={styles['idea-jugada-frescura']}>
                  {' '}· <FreshnessTag frescura={live.data.frescura} />
                </span>
              )}
            </p>
            {live.data?.hechos && live.data.hechos.length > 0 && (
              <ul className={styles['na-list']}>
                {live.data.hechos.map((h, i) => (
                  <li key={i}>{h}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {!enCurso && !cerrado && hayPlan && !fijada && (
          <div className={styles['idea-jugada-cta-wrap']}>
            <p className={styles['na-body']}>
              El plan está listo. Si decides entrar, fija la jugada y el sistema la sigue en vivo.
            </p>
            <button
              className={styles['idea-cta']}
              onClick={handleFijar}
              disabled={enviando}
              type="button"
            >
              {enviando ? 'Fijando…' : 'Fijar esta jugada'}
            </button>
            {ctaError && (
              <p className={styles['idea-jugada-error']}>{ctaError}</p>
            )}
          </div>
        )}

        {!enCurso && fijada && (
          <p className={styles['idea-jugada-ok']}>
            Jugada fijada — se sigue en vivo.
          </p>
        )}

        {!enCurso && cerrado && !fijada && conducta.data && (
          <div className={styles['idea-jugada-cerrado']}>
            {conducta.data.titular && (
              <p className={styles['na-body']}>{conducta.data.titular}</p>
            )}
            {conducta.data.campos && conducta.data.campos.length > 0 && (
              <ul className={styles['na-list']}>
                {conducta.data.campos.map((c, i) => (
                  <li key={i}>
                    <span className={styles['idea-conducta-icon']}>
                      {c.ok === 'si' ? '✓' : c.ok === 'no' ? '○' : '·'}
                    </span>{' '}
                    {c.k}{c.v ? `: ${c.v}` : ''}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {!enCurso && !cerrado && !hayPlan && !fijada && (
          <p className={styles['na-empty']}>
            No hay plan calculado ahora mismo. Puedes revisar los bloques de arriba para ver el estado actual.
          </p>
        )}
      </section>

      {/* ── 6. Quién está detrás (dossier inlineado) ─────────── */}
      <section id="idea-quien" className={styles['na-block']}>
        <h2 className={styles['idea-quien-heading']}>Quién está detrás</h2>
        <DossierBody state={bundle.dossier} onRefresh={bundle.refreshDossier} />
      </section>

      {/* ── 7. Noticias ───────────────────────────────────────── */}
      <NoticiasSection symbol={symbol} />

      {/* ── 8. Footer ─────────────────────────────────────────── */}
      <footer className={styles['idea-footer']}>
        <button
          className={styles['idea-restart']}
          onClick={() => onRestart?.()}
          type="button"
        >
          Mirar otra moneda
        </button>
      </footer>

    </div>
  );
};
