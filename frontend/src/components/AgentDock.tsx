// ============================================================
// AgentDock — floating launcher + slide-up chat panel.
//
// Mounted at App level so the conversation persists across tab
// changes (Mercado → Posiciones → Kill-switch → back to Mercado).
//
// Phase 2B of epic #400 — rewired from the legacy /agent/chat (one
// shot, frontend-built system prompt, marker-parsed pseudo-tools)
// to the proper SSE streaming endpoint POST /agent/conversations/{id}/turn
// with a server-side system prompt and typed tool_use events. The
// `<<<TOOL:...>>>` marker protocol is dead — its UX (inline action
// buttons) returns in Phase 3 via signed proposal events.
// ============================================================

import React, { useEffect, useMemo, useRef, useState } from 'react';
import styles from './AgentDock.module.css';
import historyStyles from './AgentHistorySidebar.module.css';
import type { SymbolStatus, Position, MacroState } from '../types';
import { useAgentStream } from '../agent/useAgentStream';
import { SURFACE_DOCK } from '../agent/surfaces';
import type { ProposalChip, ToolChip } from '../agent/types';
import AgentHistorySidebar from './AgentHistorySidebar';

interface AgentDockProps {
  open:           boolean;
  onOpen:         () => void;
  onClose:        () => void;
  symbols:        SymbolStatus[];
  positions:      Position[];
  macro:          MacroState;
  initialPrompt?: string | null;
  /** Kept for future use — Phase 3's proposal events will dispatch
   *  open_symbol via a structured action, not a text marker. */
  onOpenSymbol?:  (pair: string) => void;
  unreadHint?:    boolean;
}

const DOCK_SUGGESTIONS = [
  '¿en qué par tengo más probabilidad ahora?',
  'explícame el setup de PENDLE',
  'simular $500 en RUNE',
  '¿debería cerrar ETH ahora?',
];

const AgentDock: React.FC<AgentDockProps> = ({
  open, onOpen, onClose,
  symbols, positions,
  initialPrompt, unreadHint,
}) => {
  const [input, setInput] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const { msgs, loading, sendTurn, confirmProposal } = useAgentStream({ surface: SURFACE_DOCK });
  // Synthetic welcome bubble before the first real turn so the dock
  // isn't an empty void on open. Lives outside the stream hook because
  // it never goes on the wire — purely UI. Derived (useMemo) instead
  // of stored, so it reflects current watchlist counts even after a
  // conversation reset (PR #405 review nit).
  const welcome = useMemo<string | null>(() => {
    if (!open || msgs.length > 0 || initialPrompt) return null;
    return (
      `Hola. Estoy mirando tus ${symbols.length} pares y ${positions.length} posiciones. ` +
      `Pregúntame lo que quieras.`
    );
  }, [open, msgs.length, initialPrompt, symbols.length, positions.length]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Auto-send the initial prompt when one is passed in.
  useEffect(() => {
    if (open && initialPrompt && msgs.length === 0) {
      void sendTurn(initialPrompt);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialPrompt]);

  // Autoscroll on new messages or loading state changes.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, loading]);

  // Keep the input focused while the dock is open and idle. Without this,
  // ending a turn (loading: true → false) leaves the user without focus
  // because the browser blurred the input when it became disabled and
  // doesn't restore focus when it's re-enabled.
  useEffect(() => {
    if (open && !loading) inputRef.current?.focus();
  }, [open, loading]);

  const submit = async (text: string) => {
    const t = text.trim();
    if (!t || loading) return;
    setInput('');
    await sendTurn(t);
  };

  return (
    <>
      {/* Floating launcher */}
      <button
        className={`${styles.fab} ${open ? styles.fabHidden : ''}`}
        onClick={onOpen}
        title="Abrir copiloto"
        aria-label="Abrir copiloto"
      >
        <span className={styles.fabGlyph}>◈</span>
        <span className={styles.fabPulse} />
        {unreadHint && <span className={styles.fabBadge}>1</span>}
      </button>

      {/* Dock panel */}
      {open && (
        <aside className={styles.dock} role="dialog" aria-label="Copiloto">
          <header className={styles.hd}>
            <div className={styles.id}>
              <span className={styles.avatar}>◈</span>
              <div>
                <div className={styles.name}>copiloto</div>
                <div className={styles.sub}>
                  <span className={styles.dot} /> contexto completo del portafolio
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button
                type="button"
                className={historyStyles.openButton}
                onClick={() => setHistoryOpen(true)}
                aria-label="Abrir historial"
              >Historial</button>
              <button className={styles.close} onClick={onClose} aria-label="Cerrar">×</button>
            </div>
          </header>

          <div className={styles.scroll} ref={scrollRef}>
            {welcome && msgs.length === 0 && (
              <DockMessage role="assistant" text={welcome} />
            )}
            {msgs.map((m, i) => (
              <DockMessage
                key={i}
                role={m.role}
                text={m.text}
                reasoning={m.reasoning}
                toolChips={m.tool_chips}
                proposals={m.proposals}
                onConfirmProposal={confirmProposal}
                showTyping={
                  m.role === 'assistant' && loading && i === msgs.length - 1 && m.text === ''
                }
              />
            ))}
          </div>

          <div className={styles.sugg}>
            {DOCK_SUGGESTIONS.map((s, i) => (
              <button
                key={i}
                className={styles.suggChip}
                onClick={() => void submit(s)}
                disabled={loading}
              >{s}</button>
            ))}
          </div>

          <form className={styles.inputRow} onSubmit={(e) => { e.preventDefault(); void submit(input); }}>
            <span className={styles.prompt}>&gt;</span>
            <input
              ref={inputRef}
              className={styles.input}
              placeholder={loading ? 'pensando…' : 'pregúntame algo sobre el mercado, tus posiciones, un par…'}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              autoFocus
            />
            <button className={styles.send} type="submit" disabled={loading || !input.trim()}>↵</button>
          </form>
        </aside>
      )}

      {/* H.4 sidebar — slides in on top of the dock. H.5 will wire
          onSelectConversation through useAgentStream to rehydrate the
          transcript; for H.4 it just closes the sidebar (no-op load). */}
      <AgentHistorySidebar
        open={open && historyOpen}
        onClose={() => setHistoryOpen(false)}
      />
    </>
  );
};

// ── Sub-components ──────────────────────────────────────────────────

interface DockMessageProps {
  role:       'user' | 'assistant';
  text:       string;
  reasoning?: string;
  toolChips?: ToolChip[];
  proposals?: ProposalChip[];
  onConfirmProposal?: (proposal_id: string) => void;
  showTyping?: boolean;
}

const DockMessage: React.FC<DockMessageProps> = ({
  role, text, reasoning, toolChips, proposals, onConfirmProposal, showTyping,
}) => {
  if (role === 'user') {
    return (
      <div className={`${styles.msg} ${styles.msgUser}`}>
        <div className={`${styles.bubble} ${styles.bubbleUser}`}>{text}</div>
      </div>
    );
  }
  return (
    <div className={`${styles.msg} ${styles.msgAsst}`}>
      <div className={styles.msgAvatar}>◈</div>
      <div className={styles.msgBody}>
        {showTyping && (
          <div className={`${styles.bubble} ${styles.bubbleAsst} ${styles.bubbleTyping}`}>
            <span className={styles.typingDot} />
            <span className={styles.typingDot} />
            <span className={styles.typingDot} />
          </div>
        )}
        {!showTyping && text && (
          <div className={`${styles.bubble} ${styles.bubbleAsst}`}>
            <DockText text={text} />
          </div>
        )}
        {reasoning && reasoning.length > 0 && (
          <details className={styles.reasoning}>
            <summary className={styles.reasoningSummary}>Razonamiento</summary>
            {/* PR #414 review pickup 3: reasoning is the model's
                internal chain-of-thought, not formatted output for the
                user. Render as PLAIN TEXT so literal ** marks (which
                R1 uses for emphasis in its own prose) survive verbatim.
                Using DockText/FormattedText here would interpret them
                as bold and the operator loses fidelity of the
                reasoning's structure. white-space: pre-wrap in the CSS
                preserves newlines + indentation. */}
            <div className={styles.reasoningBody}>{reasoning}</div>
          </details>
        )}
        {toolChips && toolChips.length > 0 && (
          <div className={styles.toolChipsRow}>
            {toolChips.map((c, i) => (
              <span
                key={i}
                className={[
                  styles.toolChip,
                  c.status === 'pending' ? styles.toolChipPending :
                  c.status === 'ok'      ? styles.toolChipOk :
                                            styles.toolChipError,
                ].join(' ')}
                title={`tool ${c.tool} — ${c.status}`}
              >
                {c.status === 'pending' ? '⋯' : c.status === 'ok' ? '✓' : '✗'} {c.tool}
              </span>
            ))}
          </div>
        )}
        {proposals && proposals.length > 0 && proposals.map((p) => (
          <ProposalConfirm
            key={p.proposal_id}
            proposal={p}
            onConfirm={onConfirmProposal}
          />
        ))}
      </div>
    </div>
  );
};

// ── Proposal confirm row ────────────────────────────────────────────

interface ProposalConfirmProps {
  proposal: ProposalChip;
  onConfirm?: (proposal_id: string) => void;
}

const ProposalConfirm: React.FC<ProposalConfirmProps> = ({ proposal, onConfirm }) => {
  // Button copy reflects the state machine. Terminal states freeze the
  // button (disabled + colored) so the user sees what happened without
  // a toast / popover layer.
  const labelByState: Record<ProposalChip['state'], string> = {
    pending:   'Confirmar',
    in_flight: 'Procesando…',
    ok:        'Confirmado ✓',
    expired:   'Expirado',
    drift:     'Estado cambió — re-pregunta',
    error:     'Falló — re-pregunta',
    // #428 H.3: rehydrated chip without a valid signed_payload. UX is
    // identical to expired — not actionable, button disabled — but the
    // copy distinguishes "TTL passed" from "you saw this in a previous
    // session and the signed_payload wasn't persisted".
    stale:     'Propuesta ya no activa',
  };
  const stateClass =
    proposal.state === 'in_flight' ? styles.toolConfirmInFlight :
    proposal.state === 'ok'        ? styles.toolConfirmOk :
    (proposal.state === 'expired' || proposal.state === 'drift' ||
     proposal.state === 'error' || proposal.state === 'stale')
                                   ? styles.toolConfirmError :
    '';
  const isInteractive = proposal.state === 'pending';

  return (
    <div className={styles.proposalRow}>
      <div className={styles.proposalSummary}>{proposal.summary}</div>
      <button
        type="button"
        className={[styles.toolConfirm, stateClass].join(' ')}
        disabled={!isInteractive}
        onClick={() => isInteractive && onConfirm?.(proposal.proposal_id)}
      >
        {labelByState[proposal.state]}
      </button>
    </div>
  );
};

const DockText: React.FC<{ text: string }> = ({ text }) => {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith('**') && p.endsWith('**')) {
          return <strong key={i}>{p.slice(2, -2)}</strong>;
        }
        return (
          <React.Fragment key={i}>
            {p.split('\n').map((line, j) => (
              <React.Fragment key={j}>{j > 0 && <br />}{line}</React.Fragment>
            ))}
          </React.Fragment>
        );
      })}
    </>
  );
};

export default AgentDock;
