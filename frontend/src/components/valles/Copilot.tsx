// Copilot.tsx — dock del Copiloto de Valles.
//
// Consumidor del hook useAgentStream con surface='valles'. El backend
// tiene la capa anti-veredicto (system prompt + denylist + LLM judge);
// las preguntas trampa de SUGG llegan al agente real y se rechazan
// server-side con un evento SSE `refusal`. El componente aplica
// vwBubbleRefusal para el styling de rechazo.
//
// Chrome preservado: dock, avatar ◈, header, subtítulo doctrinal,
// botón de cierre, scrim, chips de sugerencias, formulario de input.
import React, { useEffect, useRef, useState } from 'react';
import styles from './valles.module.css';
import { useAgentStream } from '../../agent/useAgentStream';
import { SURFACE_VALLES } from '../../agent/surfaces';
import type { ChatMsg } from '../../agent/useAgentStream';

const SUGG = ['¿Qué quiere decir "en valle"?', '¿Está vieja la información?', '¿Cuál conviene comprar?', '¿Cuánto pongo?'];

export const Copilot: React.FC<{ onClose: () => void; symbol?: string }> = ({ onClose, symbol }) => {
  const [input, setInput] = useState('');
  const scroll = useRef<HTMLDivElement>(null);

  const { msgs, loading, sendTurn } = useAgentStream({ surface: SURFACE_VALLES });

  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [msgs, loading]);

  const send = async (t: string) => {
    const s = t.trim();
    if (!s || loading) return;
    setInput('');
    const hints = symbol ? { symbol } : undefined;
    await sendTurn(s, hints);
  };

  // Greeting inicial estático — el agente real responde cuando el usuario pregunta.
  const showGreeting = msgs.length === 0;

  // "Pensando": Valles buffea la respuesta (no streamea token a token), así que
  // el placeholder assistant queda vacío hasta el final. No lo pintamos como
  // burbuja vacía; en su lugar mostramos UN loader de tres puntos.
  const last = msgs[msgs.length - 1];
  const thinking = loading && (!last || last.role !== 'assistant' || last.text === '');

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
          {showGreeting && (
            <div className={styles.vwMsg}>
              <span className={`${styles.vwTag} ${styles.vwTagFact}`}>fact</span>
              <div className={styles.vwBubble}>Te leo hechos: si está viva, dónde está el precio respecto a sus paredes, y quién está detrás con su fuente. Pregúntame por cualquiera.</div>
            </div>
          )}
          {msgs.map((m: ChatMsg, i: number) => {
            // Placeholder vacío mientras piensa → no se pinta (el loader lo cubre).
            if (m.role === 'assistant' && !m.text) return null;
            return (
              <div className={`${styles.vwMsg} ${m.role === 'user' ? styles.vwMsgUser : ''}`} key={i}>
                {m.role === 'assistant' && (
                  <span className={`${styles.vwTag} ${m.refusal ? '' : styles.vwTagFact}`}>
                    {m.refusal ? 'no decide' : 'fact'}
                  </span>
                )}
                <div className={`${styles.vwBubble} ${m.refusal ? styles.vwBubbleRefusal : ''}`}>
                  {m.text}
                </div>
              </div>
            );
          })}
          {thinking && (
            <div className={styles.vwMsg}>
              <div className={styles.vwBubble}>
                <span className={styles.vwTyping} role="status" aria-label="Pensando">
                  <span className={styles.vwTypingDot} />
                  <span className={styles.vwTypingDot} />
                  <span className={styles.vwTypingDot} />
                </span>
              </div>
            </div>
          )}
        </div>
        <div className={styles.vwDockSugg}>
          {SUGG.map((s, i) => (
            <button key={i} className={styles.vwSugg} onClick={() => send(s)} disabled={loading}>{s}</button>
          ))}
        </div>
        <form className={styles.vwDockInput} onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <input
            placeholder="pregunta en tus palabras…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={loading}
          />
          <button className={styles.vwDockSend} type="submit" disabled={!input.trim() || loading} aria-label="Enviar">↑</button>
        </form>
      </aside>
    </>
  );
};
