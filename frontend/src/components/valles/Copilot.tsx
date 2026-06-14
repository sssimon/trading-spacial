// Copilot.tsx
import React, { useEffect, useRef, useState } from 'react';
import styles from './valles.module.css';

export interface CannedReply { refusal?: boolean; tag: string; html: React.ReactNode; }

const DECISION = /(en cu[aá]l|cu[aá]l (compr|entr|conv)|vale la pena|qu[eé] har[ií]as|should i (buy|enter)|recomiend|me conviene)/i;
const SIZING   = /(cu[aá]nto|pongo|apost|tama[ñn]o|how much)/i;
const VERDICT  = /(compr|conviene|mejor|vend|buena|mala)/i;

export function canned(qRaw: string): CannedReply {
  const q = (qRaw || '').toLowerCase();
  if (SIZING.test(q)) return { refusal: true, tag: 'no te dice cuánto', html: <>El tamaño lo decides tú, a propósito. Valles no te dice cuánto poner. Solo te muestra los hechos para que decidas con tu criterio.</> };
  if (DECISION.test(q) || VERDICT.test(q)) return { refusal: true, tag: 'no decide', html: <>No te digo si comprar ni cuál es "la mejor". No existe un puntaje de calidad: la herramienta muestra hechos y el veredicto es tuyo.</> };
  if (q.includes('valle') || q.includes('quiet') || q.includes('viva')) return { tag: 'fact', html: <>"En valle" quiere decir que la moneda <b>se mueve poco</b>, dentro de una franja angosta, durante varias semanas. Es una descripción del gráfico, no un consejo.</> };
  if (q.includes('viej') || q.includes('fresc') || q.includes('ranci') || q.includes('actual')) return { tag: 'fact', html: <>Cada lectura te dice su edad. Si algo es de hace varios días te aviso que pudo cambiar.</> };
  return { tag: 'fact', html: <>Te leo hechos: si está viva, dónde está el precio respecto a sus paredes, y quién está detrás con su fuente. Pregúntame por cualquiera.</> };
}

const SUGG = ['¿Qué quiere decir "en valle"?', '¿Está vieja la información?', '¿Cuál conviene comprar?', '¿Cuánto pongo?'];
type Msg = { role: 'user' | 'assistant'; html: React.ReactNode; tag?: string; refusal?: boolean };

export const Copilot: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [msgs, setMsgs] = useState<Msg[]>([{ role: 'assistant', tag: 'fact', html: <>Te leo los hechos de las tres lentes. No predigo, no digo cuál es mejor, y no te digo cuánto poner.</> }]);
  const [input, setInput] = useState('');
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [msgs]);
  const send = (t: string) => { const s = t.trim(); if (!s) return; setMsgs((p) => [...p, { role: 'user', html: s }, { role: 'assistant', ...canned(s) }]); setInput(''); };
  return (
    <>
      <div className={styles.vwScrim} onClick={onClose} />
      <aside className={styles.vwDock} role="dialog" aria-label="Copiloto de Valles">
        <header className={styles.vwDockHd}>
          <div className={styles.vwDockAvatar} aria-hidden="true">◈</div>
          <div><div className={styles.vwDockName}>Copiloto · Valles</div><div className={styles.vwDockSub}>exhibe los hechos · no decide</div></div>
          <button className={styles.vwDockClose} onClick={onClose} aria-label="Cerrar">×</button>
        </header>
        <div className={styles.vwDockScroll} ref={scroll}>
          {msgs.map((m, i) => (
            <div className={`${styles.vwMsg} ${m.role === 'user' ? styles.vwMsgUser : ''}`} key={i}>
              {m.role === 'assistant' && m.tag && <span className={`${styles.vwTag} ${m.tag === 'fact' ? styles.vwTagFact : ''}`}>{m.tag}</span>}
              <div className={`${styles.vwBubble} ${m.refusal ? styles.vwBubbleRefusal : ''}`}>{m.html}</div>
            </div>
          ))}
        </div>
        <div className={styles.vwDockSugg}>{SUGG.map((s, i) => <button key={i} className={styles.vwSugg} onClick={() => send(s)}>{s}</button>)}</div>
        <form className={styles.vwDockInput} onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <input placeholder="pregunta en tus palabras…" value={input} onChange={(e) => setInput(e.target.value)} />
          <button className={styles.vwDockSend} type="submit" disabled={!input.trim()} aria-label="Enviar">↑</button>
        </form>
      </aside>
    </>
  );
};
