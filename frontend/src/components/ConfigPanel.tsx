// ============================================================
// ConfigPanel.tsx — slide-out from the right with backdrop blur.
// Sections: General, Filtros de notificación, Auto-tune.
// Preserves existing wiring to /config (get + post) and the
// auto_approve_tune contract.
// ============================================================

import React, { useEffect, useState } from 'react';
import styles from './ConfigPanel.module.css';
import type {
  SignalFilters,
  AppConfig,
  WebhookTestResponse,
  WebhookTestChannelResult,
} from '../types';
import { getConfig, updateConfigFull, testWebhook } from '../api';

interface ConfigPanelProps {
  open: boolean;
  onClose: () => void;
}

const DEFAULT_FILTERS: SignalFilters = {
  min_score: 0,
  require_macro_ok: false,
  notify_setup: false,
};

type WebhookTestState =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'done'; result: WebhookTestResponse }
  | { status: 'error'; message: string };

const ConfigPanel: React.FC<ConfigPanelProps> = ({ open, onClose }) => {
  const [config, setConfig]   = useState<AppConfig | null>(null);
  const [filters, setFilters] = useState<SignalFilters>(DEFAULT_FILTERS);
  const [autoApprove, setAutoApprove] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [dirty, setDirty]     = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [webhookTest, setWebhookTest] = useState<WebhookTestState>({ status: 'idle' });

  // Reset the webhook test status whenever the panel re-opens so the user
  // sees a clean slate. (Otherwise the "OK" badge from a previous open
  // would linger and create false confidence about the current config.)
  useEffect(() => {
    if (open) setWebhookTest({ status: 'idle' });
  }, [open]);

  const runWebhookTest = async () => {
    setWebhookTest({ status: 'running' });
    try {
      const result = await testWebhook();
      setWebhookTest({ status: 'done', result });
    } catch (err) {
      setWebhookTest({
        status:  'error',
        message: err instanceof Error ? err.message : 'Error inesperado',
      });
    }
  };

  // Load config when opened.
  useEffect(() => {
    if (!open) return;
    setLoadError(null);
    setDirty(false);
    getConfig()
      .then((cfg) => {
        setConfig(cfg);
        setFilters({ ...DEFAULT_FILTERS, ...cfg.signal_filters });
        setAutoApprove(cfg.auto_approve_tune ?? true);
      })
      .catch((err) => {
        setLoadError(err instanceof Error ? err.message : 'Error al cargar config');
      });
  }, [open]);

  if (!open) return null;

  const updateFilter = <K extends keyof SignalFilters>(k: K, v: SignalFilters[K]) => {
    setFilters((prev) => ({ ...prev, [k]: v }));
    setDirty(true);
  };
  const updateAutoApprove = (v: boolean) => {
    setAutoApprove(v);
    setDirty(true);
  };

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      const res = await updateConfigFull({
        signal_filters:    filters,
        auto_approve_tune: autoApprove,
      });
      setConfig(res.config);
      setFilters({ ...DEFAULT_FILTERS, ...res.config.signal_filters });
      setAutoApprove(res.config.auto_approve_tune ?? true);
      setDirty(false);
      // brief delay so the user sees the "saved" pulse, then close
      setTimeout(onClose, 300);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Error al guardar');
    } finally {
      setSaving(false);
    }
  };

  const scoreLabel =
    filters.min_score >= 8 ? 'Excelente' :
    filters.min_score >= 6 ? 'Alto'      :
    filters.min_score >= 4 ? 'Bueno'     :
    filters.min_score >= 1 ? 'Permisivo' :
    'Sin filtro';

  return (
    <>
      <div className={styles.backdrop} onClick={onClose} aria-hidden="true" />
      <aside className={styles.slideout} role="dialog" aria-modal="true" aria-label="Ajustes">

        <header className={styles.hd}>
          <div className={styles.hdTitle}>
            <span className={styles.hdMark}>⚙</span>
            <span>Ajustes del escáner</span>
          </div>
          <button className={styles.hdClose} onClick={onClose} aria-label="Cerrar">×</button>
        </header>

        {loadError && (
          <div className={styles.error}>{loadError}</div>
        )}

        {!config && !loadError && (
          <div className={styles.loading}>Cargando configuración…</div>
        )}

        {config && (
          <div className={styles.scroll}>

            {/* GENERAL */}
            <section className={styles.sec}>
              <header className={styles.secHd}>
                <span className={styles.secLabel}>general</span>
                <span className={`${styles.secHint} prose`}>parámetros del escaneo</span>
              </header>

              <div className={styles.row}>
                <div className={styles.rowLeft}>
                  <div className={styles.rowLabel}>Webhook</div>
                  <div className={`${styles.rowHint} prose`}>URL para recibir señales fuera de Telegram.</div>
                </div>
                <div className={styles.rowRight}>
                  {config.webhook_url ? (
                    <span className={`${styles.pill} ${styles.pillBull}`}>conectado</span>
                  ) : (
                    <button className={styles.dashed}>＋ Configurar</button>
                  )}
                </div>
              </div>

              <div className={styles.row}>
                <div className={styles.rowLeft}>
                  <div className={styles.rowLabel}>Probar entrega</div>
                  <div className={`${styles.rowHint} prose`}>
                    Envía un mensaje de prueba a Telegram y al webhook configurado.
                  </div>
                </div>
                <div className={styles.rowRight}>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={runWebhookTest}
                    disabled={webhookTest.status === 'running'}
                  >
                    <span className="btn__caret">▸</span>{' '}
                    {webhookTest.status === 'running' ? 'probando…' : 'probar'}
                  </button>
                </div>
              </div>

              {webhookTest.status === 'done' && (
                <WebhookTestResult result={webhookTest.result} />
              )}
              {webhookTest.status === 'error' && (
                <div className={styles.error}>Falló: {webhookTest.message}</div>
              )}

              <div className={styles.row}>
                <div className={styles.rowLeft}>
                  <div className={styles.rowLabel}>Intervalo de escaneo</div>
                  <div className={`${styles.rowHint} prose`}>Cada cuánto re-evalúa los pares.</div>
                </div>
                <div className={styles.rowRight}>
                  <div className={styles.read}>
                    <span className="num">{config.scan_interval_sec}</span>
                    <span className={styles.readUnit}>s</span>
                  </div>
                </div>
              </div>

              <div className={styles.row}>
                <div className={styles.rowLeft}>
                  <div className={styles.rowLabel}>Símbolos activos</div>
                  <div className={`${styles.rowHint} prose`}>Pares en la watch-list.</div>
                </div>
                <div className={styles.rowRight}>
                  <div className={styles.read}><span className="num">{config.num_symbols}</span></div>
                </div>
              </div>
            </section>

            {/* FILTROS */}
            <section className={styles.sec}>
              <header className={styles.secHd}>
                <span className={styles.secLabel}>filtros de notificación</span>
                <span className={`${styles.secHint} prose`}>
                  solo se envían a Telegram las señales que pasen todos los filtros
                </span>
              </header>

              <ScoreSlider
                value={filters.min_score}
                onChange={(v) => updateFilter('min_score', v)}
                label={scoreLabel}
              />

              <Toggle
                label="Exigir macro 4H alcista"
                hint="Solo notifica si el precio está por encima de la SMA100 en 4H."
                value={filters.require_macro_ok}
                onChange={(v) => updateFilter('require_macro_ok', v)}
              />
              <Toggle
                label="Notificar setups válidos"
                hint="Enviar señales de SETUP VÁLIDO aunque no haya gatillo 5M."
                value={filters.notify_setup}
                onChange={(v) => updateFilter('notify_setup', v)}
              />
            </section>

            {/* AUTO-TUNE */}
            <section className={styles.sec}>
              <header className={styles.secHd}>
                <span className={styles.secLabel}>auto-tune</span>
                <span className={`${styles.secHint} prose`}>recalibración mensual de parámetros</span>
              </header>

              <Toggle
                label="Aprobación automática"
                hint={autoApprove
                  ? 'Los parámetros recomendados se aplican automáticamente cada mes.'
                  : 'Recibirás una notificación para revisar y aprobar los cambios.'}
                value={autoApprove}
                onChange={updateAutoApprove}
              />
            </section>

          </div>
        )}

        <footer className={styles.ft}>
          <div className={styles.ftStatus}>
            {dirty ? (
              <><span className={`${styles.dot} ${styles.dotDirty}`} /> Cambios sin guardar</>
            ) : (
              <><span className={`${styles.dot} ${styles.dotOk}`} /> Sin cambios pendientes</>
            )}
          </div>
          <div className={styles.ftActions}>
            <button className="btn btn--ghost btn--sm" onClick={onClose}>Cancelar</button>
            <button
              className={`btn btn--primary btn--sm ${!dirty || saving ? 'btn--disabled' : ''}`}
              onClick={handleSave}
              disabled={!dirty || saving || !config}
            >
              <span className="btn__caret">▸</span> {saving ? 'guardando…' : 'guardar'}
            </button>
          </div>
        </footer>
      </aside>
    </>
  );
};

// ─── WebhookTestResult ───
// Renders the two-channel summary from GET /webhook/test. Each row is one
// channel (telegram_directo, webhook_n8n). The pill color reflects ok/fail;
// the error string (if any) is shown below in muted prose. We intentionally
// don't conflate "no token configured" with "HTTP request failed" — both
// arrive as ok=false but with distinct error messages from the backend.

interface WebhookTestResultProps {
  result: WebhookTestResponse;
}
const WebhookTestResult: React.FC<WebhookTestResultProps> = ({ result }) => {
  const rows: Array<{ key: string; label: string; channel: WebhookTestChannelResult }> = [
    { key: 'telegram', label: 'Telegram', channel: result.telegram_directo },
    { key: 'webhook',  label: 'Webhook',  channel: result.webhook_n8n },
  ];
  return (
    <div className={styles.webhookResult}>
      {rows.map(({ key, label, channel }) => (
        <div key={key} className={styles.webhookRow}>
          <div>
            <div>{label}</div>
            {channel.error && (
              <div className={`${styles.webhookDetail} prose`}>{channel.error}</div>
            )}
            {channel.url && (
              <div className={styles.webhookChannel}>{channel.url}</div>
            )}
          </div>
          <span
            className={[
              styles.pill,
              channel.ok ? styles.pillBull : styles.pillBear,
            ].join(' ')}
          >
            {channel.ok ? 'ok' : 'fail'}
          </span>
        </div>
      ))}
    </div>
  );
};

// ─── ScoreSlider ───

interface ScoreSliderProps {
  value:    number;
  onChange: (v: number) => void;
  label:    string;
}
const ScoreSlider: React.FC<ScoreSliderProps> = ({ value, onChange, label }) => {
  const ticks = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
  const tone  = value >= 6 ? 'bull' : value >= 3 ? 'warn' : 'dim';
  return (
    <div className={styles.slider}>
      <div className={styles.sliderHd}>
        <div className={styles.sliderLbl}>Score mínimo para notificar</div>
        <div className={`${styles.sliderBadge} ${styles[`sliderBadge--${tone}`]}`}>
          ≥ <span className="num">{value}</span> · {label}
        </div>
      </div>
      <div className={styles.sliderBar}>
        <div className={styles.sliderTrack}>
          <div className={styles.sliderZone} style={{ left: '60%', right: '0%' }} />
        </div>
        {ticks.map((n) => (
          <button
            key={n}
            type="button"
            className={[
              styles.sliderTick,
              n === value ? styles.sliderTickActive : '',
              n < value   ? styles.sliderTickFilled : '',
            ].filter(Boolean).join(' ')}
            onClick={() => onChange(n)}
            style={{ left: `${(n / 10) * 100}%` }}
            aria-label={`Score mínimo ${n}`}
          >
            <span className={styles.sliderTickDot} />
            <span className={`${styles.sliderTickLbl} num`}>{n}</span>
          </button>
        ))}
      </div>
    </div>
  );
};

// ─── Toggle ───

interface ToggleProps {
  label:    string;
  hint?:    string;
  value:    boolean;
  onChange: (v: boolean) => void;
}
const Toggle: React.FC<ToggleProps> = ({ label, hint, value, onChange }) => (
  <button
    type="button"
    role="switch"
    aria-checked={value}
    className={[styles.toggle, value ? styles.toggleOn : ''].filter(Boolean).join(' ')}
    onClick={() => onChange(!value)}
  >
    <div className={styles.toggleText}>
      <div className={styles.toggleLabel}>{label}</div>
      {hint && <div className={`${styles.toggleHint} prose`}>{hint}</div>}
    </div>
    <div className={styles.toggleSwitch} aria-hidden="true">
      <div className={styles.toggleThumb} />
    </div>
  </button>
);

export default ConfigPanel;
