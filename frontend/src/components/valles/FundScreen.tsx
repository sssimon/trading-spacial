// FundScreen.tsx
import React from 'react';
import type { Dossier } from '../../types';
import type { AsyncState } from './useValleyBundle';
import { Eyebrow, Callout, Loading, Retry } from './atoms';
import { FreshnessTag } from '../FreshnessTag';
import styles from './valles.module.css';

const CH_LABEL: Record<string, string> = {
  sitio_web: 'Sitio web', github: 'GitHub', twitter: 'Redes (X)', telegram_discord: 'Telegram', whitepaper: 'Documento técnico',
};

// §4.4 — cada hecho ancla a su fuente verificable (patrón traído de ProjectDossier).
const Fuente: React.FC<{ url: string | null }> = ({ url }) =>
  url ? <a className={styles.vwSrc} href={url} target="_blank" rel="noreferrer">fuente</a> : null;

export const FundScreen: React.FC<{ symbol: string; state: AsyncState<Dossier>; onRefresh: () => void }> = ({ symbol, state, onRefresh }) => {
  const { data, loading, error } = state;
  let answer: React.ReactNode;

  if (loading) {
    answer = <Loading label="Buscando quién está detrás…" />;
  } else if (error || !data || data.estado_general === 'no_disponible') {
    answer = (
      <Callout tone="mute" icon="×" title="No se pudo averiguar ahora" sub="Falló la búsqueda. Es un problema de la herramienta, no del proyecto.">
        <Retry onClick={onRefresh} />
      </Callout>
    );
  } else if (data.estado_general === 'opaco') {
    answer = (
      <Callout tone="ochre" icon="◍" title="No se encontró quién está detrás"
        sub={<>Se buscó equipo, presencia y actividad pública, y <b>no apareció nada</b>. Eso es un dato sobre el proyecto, no una falla de la herramienta.{data.no_encontrado_en.length > 0 && <> No se halló en: {data.no_encontrado_en.join(', ')}.</>}</>} />
    );
  } else {
    answer = (
      <div className={styles.vwAnswer}>
        <div className={styles.vwAnswerRow}>
          <div className={styles.vwAnswerIcon} aria-hidden="true">☻</div>
          <div>
            <div className={styles.vwAnswerLead}>Se sabe quién está detrás</div>
            <div className={styles.vwAnswerSay}>Hay nombres y canales públicos, y cada dato se puede comprobar en su fuente.</div>
          </div>
        </div>
        {data.equipo.length > 0 && (
          <div className={styles.vwPeople}>
            {data.equipo.map((m, i) => (
              <div className={styles.vwPerson} key={i}>
                <div className={styles.vwPersonFace} aria-hidden="true">☻</div>
                <div>
                  <div className={styles.vwPersonName}>{m.nombre}{m.rol ? ` · ${m.rol}` : ''}</div>
                  <Fuente url={m.fuente} />
                </div>
              </div>
            ))}
          </div>
        )}
        <div className={styles.vwChannels}>
          {Object.entries(data.presencia).map(([k, c]) => (
            <div className={styles.vwChannel} key={k}>
              <span className={`${styles.vwChannelDot} ${styles[`vwChannelDot_${c.activo}`]}`} aria-hidden="true" />
              <span>{CH_LABEL[k] ?? k.replace(/_/g, ' ')} · <span className={styles.vwChannelState}>{c.activo === 'si' ? 'activo' : c.activo === 'no' ? 'inactivo' : 'sin confirmar'}</span></span>
              <Fuente url={c.url ?? c.fuente} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className={styles.vwScreen}>
      <Eyebrow symbol={symbol} />
      <h2 className={styles.vwQuestion}>¿Quién está detrás del proyecto?</h2>
      {answer}
      {/* §5.4 — frescura exhibida también cuando hay dato (rastreable Y opaco) */}
      {data && data.frescura && data.estado_general !== 'no_disponible' && (
        <div className={styles.vwFundFresh}><FreshnessTag frescura={data.frescura} /></div>
      )}
    </div>
  );
};
