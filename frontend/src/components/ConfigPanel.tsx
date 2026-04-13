// ============================================================
// ConfigPanel.tsx — Drawer lateral para filtros de señales
// ============================================================

import React, { useState, useEffect } from 'react';
import type { SignalFilters, AppConfig } from '../types';
import { getConfig, updateConfig } from '../api';

interface ConfigPanelProps {
  open: boolean;
  onClose: () => void;
}

const DEFAULT_FILTERS: SignalFilters = {
  min_score: 0,
  require_macro_ok: false,
  notify_setup: false,
};

const ConfigPanel: React.FC<ConfigPanelProps> = ({ open, onClose }) => {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [filters, setFilters] = useState<SignalFilters>(DEFAULT_FILTERS);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Cargar config cuando se abre el panel
  useEffect(() => {
    if (!open) return;
    setLoadError(null);
    setSaved(false);
    getConfig()
      .then((cfg) => {
        setConfig(cfg);
        setFilters({ ...DEFAULT_FILTERS, ...cfg.signal_filters });
      })
      .catch((err) => {
        setLoadError(err instanceof Error ? err.message : 'Error al cargar config');
      });
  }, [open]);

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    try {
      const res = await updateConfig(filters);
      setConfig(res.config);
      setFilters({ ...DEFAULT_FILTERS, ...res.config.signal_filters });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Error al guardar');
    } finally {
      setSaving(false);
    }
  };

  const scoreLabels: Record<number, string> = {
    0: 'Sin filtro',
    1: '≥ 1 — Mínimo',
    2: '≥ 2 — Bajo',
    3: '≥ 3 — Moderado',
    4: '≥ 4 — Bueno',
    5: '≥ 5 — Medio',
    6: '≥ 6 — Alto',
    7: '≥ 7 — Muy alto',
    8: '≥ 8 — Excelente',
    9: '≥ 9 — Casi perfecto',
    10: '= 10 — Solo perfecto',
  };

  return (
    <>
      {/* Overlay */}
      <div
        className={`config-overlay ${open ? 'config-overlay--visible' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <aside className={`config-panel ${open ? 'config-panel--open' : ''}`} role="dialog" aria-modal="true">
        <div className="config-panel-header">
          <span className="config-panel-title">⚙ Filtros de señales Telegram</span>
          <button className="config-close-btn" onClick={onClose} aria-label="Cerrar">✕</button>
        </div>

        {loadError && (
          <div className="config-error">{loadError}</div>
        )}

        {!config && !loadError && (
          <div className="config-loading">Cargando configuración…</div>
        )}

        {config && (
          <div className="config-body">
            {/* Info readonly */}
            <div className="config-info-row">
              <span className="config-info-label">Webhook</span>
              <span className="config-info-value config-info-value--mono">
                {config.webhook_url || '(no configurado)'}
              </span>
            </div>
            <div className="config-info-row">
              <span className="config-info-label">Intervalo de escaneo</span>
              <span className="config-info-value">{config.scan_interval_sec}s</span>
            </div>
            <div className="config-info-row">
              <span className="config-info-label">Símbolos activos</span>
              <span className="config-info-value">{config.num_symbols}</span>
            </div>

            <div className="config-divider" />

            <p className="config-section-title">Filtros de notificación</p>
            <p className="config-hint">
              Solo se envían a Telegram las señales que pasen todos los filtros activos.
            </p>

            {/* Score mínimo */}
            <div className="config-field">
              <label className="config-label">
                Score mínimo para notificar
                <span className="config-badge">{scoreLabels[filters.min_score]}</span>
              </label>
              <div className="config-slider-row">
                <span className="config-slider-min">0</span>
                <input
                  type="range"
                  className="config-slider"
                  min={0}
                  max={10}
                  step={1}
                  value={filters.min_score}
                  onChange={(e) =>
                    setFilters((f) => ({ ...f, min_score: Number(e.target.value) }))
                  }
                />
                <span className="config-slider-max">10</span>
              </div>
              <div className="config-slider-ticks">
                {[0, 2, 4, 6, 8, 10].map((v) => (
                  <span
                    key={v}
                    className={`config-tick ${filters.min_score === v ? 'config-tick--active' : ''}`}
                    onClick={() => setFilters((f) => ({ ...f, min_score: v }))}
                  >
                    {v}
                  </span>
                ))}
              </div>
            </div>

            {/* Macro 4H requerida */}
            <div className="config-field config-field--toggle">
              <div className="config-toggle-info">
                <span className="config-label">Exigir macro 4H alcista</span>
                <span className="config-hint">
                  Solo notifica si el precio está por encima de la SMA100 en 4H.
                </span>
              </div>
              <button
                className={`config-toggle ${filters.require_macro_ok ? 'config-toggle--on' : ''}`}
                onClick={() =>
                  setFilters((f) => ({ ...f, require_macro_ok: !f.require_macro_ok }))
                }
                aria-pressed={filters.require_macro_ok}
              >
                <span className="config-toggle-thumb" />
              </button>
            </div>

            {/* Notificar setups */}
            <div className="config-field config-field--toggle">
              <div className="config-toggle-info">
                <span className="config-label">Notificar setups válidos</span>
                <span className="config-hint">
                  Enviar señales de SETUP VÁLIDO aunque no haya gatillo 5M.
                </span>
              </div>
              <button
                className={`config-toggle ${filters.notify_setup ? 'config-toggle--on' : ''}`}
                onClick={() =>
                  setFilters((f) => ({ ...f, notify_setup: !f.notify_setup }))
                }
                aria-pressed={filters.notify_setup}
              >
                <span className="config-toggle-thumb" />
              </button>
            </div>

            <div className="config-divider" />

            {/* Preview */}
            <div className="config-preview">
              <span className="config-preview-label">Se notificará si:</span>
              <ul className="config-preview-list">
                <li>Señal activa (gatillo 5M confirmado)</li>
                {filters.min_score > 0 && (
                  <li>Score ≥ {filters.min_score}/10</li>
                )}
                {filters.require_macro_ok && (
                  <li>Macro 4H alcista (precio &gt; SMA100)</li>
                )}
                {filters.notify_setup && (
                  <li className="config-preview-extra">
                    + Setups válidos sin gatillo (también se notifican)
                  </li>
                )}
              </ul>
            </div>
          </div>
        )}

        <div className="config-footer">
          <button className="btn btn-secondary" onClick={onClose}>
            Cancelar
          </button>
          <button
            className={`btn btn-primary ${saved ? 'btn--saved' : ''}`}
            onClick={handleSave}
            disabled={saving || !config}
          >
            {saving ? (
              <><span className="btn-spinner" /> Guardando…</>
            ) : saved ? (
              '✓ Guardado'
            ) : (
              'Guardar filtros'
            )}
          </button>
        </div>
      </aside>
    </>
  );
};

export default ConfigPanel;
