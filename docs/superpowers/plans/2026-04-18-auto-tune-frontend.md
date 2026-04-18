# Auto-Tune Frontend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate auto-tune with the frontend — auto-approve toggle in config, notification badge for pending proposals, professional modal for review/accept/reject.

**Architecture:** Backend adds tune_results SQLite table + 3 API endpoints. auto_tune.py writes to DB instead of files. Frontend adds toggle to ConfigPanel, badge to Header, and TuneReportModal component.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript (frontend), SQLite.

**Spec:** `docs/superpowers/specs/en/2026-04-18-auto-tune-frontend-integration.md`

---

## File Structure

```
MODIFIED:
  btc_api.py                                — tune_results table + 3 endpoints + config field
  auto_tune.py                              — DB integration, auto_approve logic
  frontend/src/types.ts                     — TuneResult + TuneSymbolResult interfaces
  frontend/src/api.ts                       — getTuneLatest, applyTune, rejectTune
  frontend/src/components/ConfigPanel.tsx    — auto_approve toggle
  frontend/src/components/Header.tsx         — notification badge + props
  frontend/src/App.tsx                      — state management + modal integration

NEW:
  frontend/src/components/TuneReportModal.tsx — professional report modal
```

---

## Task 1: Backend — DB Table + API Endpoints

**Files:**
- Modify: `btc_api.py`

- [ ] **Step 1: Add tune_results table creation**

In `btc_api.py`, find the `init_db()` function where tables are created. Add after the existing CREATE TABLE statements:

```python
    con.execute("""
        CREATE TABLE IF NOT EXISTS tune_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            results_json TEXT,
            report_md TEXT,
            applied_ts TEXT,
            changes_count INTEGER DEFAULT 0
        )
    """)
```

- [ ] **Step 2: Add auto_approve_tune to config handling**

In the `POST /config` endpoint, add support for `auto_approve_tune` field. Find where `signal_filters` is extracted from the request body and add:

```python
    if "auto_approve_tune" in body:
        cfg["auto_approve_tune"] = bool(body["auto_approve_tune"])
```

In the `GET /config` response, include:
```python
    "auto_approve_tune": cfg.get("auto_approve_tune", True),
```

- [ ] **Step 3: Add GET /tune/latest endpoint**

```python
@app.get("/tune/latest")
async def get_tune_latest():
    """Return the most recent tune result."""
    con = get_db()
    row = con.execute(
        "SELECT * FROM tune_results ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        return None
    result = dict(row)
    if result.get("results_json"):
        result["results"] = json.loads(result["results_json"])
    return result
```

- [ ] **Step 4: Add POST /tune/apply endpoint**

```python
@app.post("/tune/apply")
async def apply_tune(key: str = Security(_api_key_header)):
    await verify_api_key(key)
    con = get_db()
    row = con.execute(
        "SELECT * FROM tune_results WHERE status='pending' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        con.close()
        raise HTTPException(status_code=404, detail="No pending tune results")

    results = json.loads(row["results_json"]) if row["results_json"] else []
    changes = [r for r in results if r.get("recommendation") == "CHANGE"]

    # Update config.json
    cfg = load_config()
    if "symbol_overrides" not in cfg:
        cfg["symbol_overrides"] = {}

    applied_count = 0
    for r in changes:
        sym = r["symbol"]
        if sym not in cfg["symbol_overrides"]:
            cfg["symbol_overrides"][sym] = {}
        cfg["symbol_overrides"][sym].update(r["proposed_params"])
        applied_count += 1

    # Backup and save
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    backup_name = f"config_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    backup_path = os.path.join(SCRIPT_DIR, backup_name)
    if os.path.exists(cfg_path):
        import shutil
        shutil.copy2(cfg_path, backup_path)

    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)

    # Update DB status
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        "UPDATE tune_results SET status='applied', applied_ts=? WHERE id=?",
        (now, row["id"])
    )
    con.commit()
    con.close()

    return {"ok": True, "applied": applied_count, "backup": backup_name}
```

- [ ] **Step 5: Add POST /tune/reject endpoint**

```python
@app.post("/tune/reject")
async def reject_tune(key: str = Security(_api_key_header)):
    await verify_api_key(key)
    con = get_db()
    row = con.execute(
        "SELECT id FROM tune_results WHERE status='pending' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        con.close()
        raise HTTPException(status_code=404, detail="No pending tune results")

    con.execute("UPDATE tune_results SET status='rejected' WHERE id=?", (row["id"],))
    con.commit()
    con.close()
    return {"ok": True}
```

- [ ] **Step 6: Run API tests**

Run: `python -m pytest tests/test_api.py -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add btc_api.py
git commit -m "feat(api): add tune_results table + tune endpoints (#137)"
```

---

## Task 2: auto_tune.py — DB Integration

**Files:**
- Modify: `auto_tune.py`

- [ ] **Step 1: Add save_to_db function**

```python
import sqlite3

DB_FILE = os.path.join(SCRIPT_DIR, "signals.db")

def save_tune_result(results: list[dict], report_md: str, status: str = "pending"):
    """Save tune results to DB."""
    changes = [r for r in results if r.get("recommendation") == "CHANGE"]
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tune_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            results_json TEXT,
            report_md TEXT,
            applied_ts TEXT,
            changes_count INTEGER DEFAULT 0
        )
    """)
    now = datetime.now(timezone.utc).isoformat()
    applied_ts = now if status == "applied" else None
    con.execute(
        "INSERT INTO tune_results (ts, status, results_json, report_md, applied_ts, changes_count) VALUES (?, ?, ?, ?, ?, ?)",
        (now, status, json.dumps(results, default=str), report_md, applied_ts, len(changes))
    )
    con.commit()
    con.close()
```

- [ ] **Step 2: Update main() to use auto_approve logic**

In `main()`, after generating the report, replace the file-writing section with:

```python
    # Check auto_approve setting
    auto_approve = config.get("auto_approve_tune", True)

    if not args.dry_run:
        if auto_approve:
            # Auto mode: apply changes silently
            if any(r["recommendation"] == "CHANGE" for r in results):
                proposed_path = write_config_proposed(results, config)
                if proposed_path:
                    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
                    apply_config(cfg_path, proposed_path, confirm=True)
                    os.remove(proposed_path)  # clean up
            save_tune_result(results, report, status="applied")
            telegram_msg = build_telegram_message(results)
            telegram_msg += "\n\n_Modo auto-approve: cambios aplicados automaticamente._"
            send_telegram(telegram_msg, config)
        else:
            # Manual mode: save as pending
            save_tune_result(results, report, status="pending")
            telegram_msg = build_telegram_message(results)
            telegram_msg += "\n\n_Revisar y aprobar en el dashboard._"
            send_telegram(telegram_msg, config)

        # Save report file (always)
        report_dir = os.path.join(SCRIPT_DIR, "data", "backtest")
        os.makedirs(report_dir, exist_ok=True)
        report_date = datetime.now().strftime("%Y%m%d")
        report_path = os.path.join(report_dir, f"tune_report_{report_date}.md")
        with open(report_path, "w") as f:
            f.write(report)
        print(f"\nReport: {report_path}")
    else:
        print("\n--- DRY RUN ---")
        print(report)
```

- [ ] **Step 3: Commit**

```bash
git add auto_tune.py
git commit -m "feat(auto-tune): add DB integration and auto_approve mode (#137)"
```

---

## Task 3: Frontend — Types + API Functions

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add TuneResult types**

Add to `frontend/src/types.ts`:

```typescript
// ---- Auto-Tune -------------------------------------------------------

export interface TuneSymbolResult {
  symbol: string;
  recommendation: 'CHANGE' | 'KEEP' | 'NO_DATA' | 'ERROR';
  current_params: {
    atr_sl_mult: number;
    atr_tp_mult: number;
    atr_be_mult: number;
  };
  proposed_params?: {
    atr_sl_mult: number;
    atr_tp_mult: number;
    atr_be_mult: number;
  } | null;
  current_val_pnl?: number;
  proposal_detail?: {
    val_pnl: number;
    val_pf: number;
    improvement_pct: number;
    total_trades: number;
    train_pnl: number;
    val_trades: number;
  } | null;
}

export interface TuneResult {
  id: number;
  ts: string;
  status: 'pending' | 'applied' | 'rejected';
  results?: TuneSymbolResult[];
  report_md?: string;
  applied_ts?: string | null;
  changes_count: number;
}
```

Also add `auto_approve_tune` to `AppConfig`:

```typescript
export interface AppConfig {
  webhook_url: string;
  notify_setup_only: boolean;
  scan_interval_sec: number;
  num_symbols: number;
  telegram_chat_id: string;
  signal_filters: SignalFilters;
  auto_approve_tune: boolean;
}
```

- [ ] **Step 2: Add API functions**

Add to `frontend/src/api.ts`:

```typescript
import type { TuneResult } from './types';

// GET /tune/latest
export async function getTuneLatest(): Promise<TuneResult | null> {
  return request<TuneResult | null>('/tune/latest');
}

// POST /tune/apply
export async function applyTune(): Promise<{ ok: boolean; applied: number; backup: string }> {
  return request<{ ok: boolean; applied: number; backup: string }>('/tune/apply', {
    method: 'POST',
  });
}

// POST /tune/reject
export async function rejectTune(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/tune/reject', { method: 'POST' });
}

// POST /config — extended to support auto_approve_tune
export async function updateConfigFull(
  body: { signal_filters?: SignalFilters; auto_approve_tune?: boolean }
): Promise<ConfigUpdateResponse> {
  return request<ConfigUpdateResponse>('/config', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
```

Also add `TuneResult` to the import list at the top.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat(frontend): add tune types and API functions (#137)"
```

---

## Task 4: Frontend — ConfigPanel Toggle

**Files:**
- Modify: `frontend/src/components/ConfigPanel.tsx`

- [ ] **Step 1: Add auto_approve_tune toggle**

In `ConfigPanel.tsx`, add state for auto_approve:

```typescript
const [autoApprove, setAutoApprove] = useState(true);
```

In the `useEffect` that loads config, add:

```typescript
setAutoApprove(cfg.auto_approve_tune ?? true);
```

In the `handleSave` function, update to also send auto_approve:

```typescript
const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    try {
      const res = await updateConfigFull({
        signal_filters: filters,
        auto_approve_tune: autoApprove,
      });
      setConfig(res.config);
      setFilters({ ...DEFAULT_FILTERS, ...res.config.signal_filters });
      setAutoApprove(res.config.auto_approve_tune ?? true);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Error al guardar');
    } finally {
      setSaving(false);
    }
  };
```

Update import to use `updateConfigFull` instead of `updateConfig`.

Add the toggle in the JSX, after the existing toggles and before the divider:

```tsx
            <div className="config-divider" />

            <p className="config-section-title">Auto-Tune</p>

            <div className="config-field config-field--toggle">
              <div className="config-toggle-info">
                <span className="config-label">Aprobacion automatica</span>
                <span className="config-hint">
                  {autoApprove
                    ? 'Los parametros se aplican automaticamente cada mes.'
                    : 'Recibiras una notificacion para revisar y aprobar cambios.'}
                </span>
              </div>
              <button
                className={`config-toggle ${autoApprove ? 'config-toggle--on' : ''}`}
                onClick={() => setAutoApprove((v) => !v)}
                aria-pressed={autoApprove}
              >
                <span className="config-toggle-thumb" />
              </button>
            </div>
```

- [ ] **Step 2: Build and verify**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ConfigPanel.tsx
git commit -m "feat(frontend): add auto-approve toggle to ConfigPanel (#137)"
```

---

## Task 5: Frontend — TuneReportModal Component

**Files:**
- Create: `frontend/src/components/TuneReportModal.tsx`

- [ ] **Step 1: Create the modal component**

```tsx
// ============================================================
// TuneReportModal.tsx — Professional auto-tune report modal
// ============================================================

import React, { useState } from 'react';
import type { TuneResult, TuneSymbolResult } from '../types';

interface TuneReportModalProps {
  tune: TuneResult;
  onApply: () => Promise<void>;
  onReject: () => Promise<void>;
  onClose: () => void;
}

const TuneReportModal: React.FC<TuneReportModalProps> = ({
  tune,
  onApply,
  onReject,
  onClose,
}) => {
  const [applying, setApplying] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [done, setDone] = useState<'applied' | 'rejected' | null>(null);
  const [showKept, setShowKept] = useState(false);

  const results = tune.results || [];
  const changes = results.filter((r) => r.recommendation === 'CHANGE');
  const kept = results.filter((r) => r.recommendation !== 'CHANGE');
  const isPending = tune.status === 'pending';
  const isApplied = tune.status === 'applied' || done === 'applied';

  const handleApply = async () => {
    setApplying(true);
    try {
      await onApply();
      setDone('applied');
    } catch (err) {
      console.error('Apply failed:', err);
    } finally {
      setApplying(false);
    }
  };

  const handleReject = async () => {
    setRejecting(true);
    try {
      await onReject();
      setDone('rejected');
      setTimeout(onClose, 1500);
    } catch (err) {
      console.error('Reject failed:', err);
    } finally {
      setRejecting(false);
    }
  };

  const formatDate = (ts: string) => {
    const d = new Date(ts);
    return d.toLocaleDateString('es-ES', {
      day: 'numeric',
      month: 'long',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <>
      <div className="tune-overlay" onClick={onClose} />
      <div className="tune-modal">
        {/* Header */}
        <div className="tune-modal-header">
          <div>
            <h2 className="tune-modal-title">Optimizacion de Parametros</h2>
            <p className="tune-modal-subtitle">
              {formatDate(tune.ts)} · {tune.changes_count} cambio{tune.changes_count !== 1 ? 's' : ''} propuesto{tune.changes_count !== 1 ? 's' : ''}
            </p>
          </div>
          <button className="tune-close-btn" onClick={onClose}>&#x2715;</button>
        </div>

        {/* Status banner */}
        {isApplied && (
          <div className="tune-banner tune-banner--applied">
            Cambios aplicados exitosamente
          </div>
        )}
        {done === 'rejected' && (
          <div className="tune-banner tune-banner--rejected">
            Propuesta rechazada
          </div>
        )}

        {/* Changes */}
        <div className="tune-modal-body">
          {changes.length > 0 && (
            <div className="tune-section">
              <h3 className="tune-section-title">
                Cambios Recomendados ({changes.length})
              </h3>
              {changes.map((r) => (
                <SymbolChangeCard key={r.symbol} result={r} />
              ))}
            </div>
          )}

          {changes.length === 0 && (
            <div className="tune-empty">
              Todos los parametros actuales son optimos. Sin cambios recomendados.
            </div>
          )}

          {/* Kept symbols */}
          {kept.length > 0 && (
            <div className="tune-section">
              <button
                className="tune-collapse-btn"
                onClick={() => setShowKept((v) => !v)}
              >
                {showKept ? '▾' : '▸'} {kept.length} symbol{kept.length !== 1 ? 's' : ''} sin cambios
              </button>
              {showKept && (
                <div className="tune-kept-list">
                  {kept.map((r) => (
                    <div key={r.symbol} className="tune-kept-item">
                      <span className="tune-kept-symbol">{r.symbol.replace('USDT', '')}</span>
                      <span className="tune-kept-params">
                        SL {r.current_params.atr_sl_mult}x · TP {r.current_params.atr_tp_mult}x · BE {r.current_params.atr_be_mult}x
                      </span>
                      <span className="tune-kept-status">optimo</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        {isPending && !done && (
          <div className="tune-modal-footer">
            <button
              className="btn btn-secondary"
              onClick={handleReject}
              disabled={applying || rejecting}
            >
              {rejecting ? 'Rechazando...' : 'Rechazar'}
            </button>
            <button
              className="btn btn-primary tune-apply-btn"
              onClick={handleApply}
              disabled={applying || rejecting || changes.length === 0}
            >
              {applying ? (
                <><span className="btn-spinner" /> Aplicando...</>
              ) : (
                `Aplicar ${changes.length} cambio${changes.length !== 1 ? 's' : ''}`
              )}
            </button>
          </div>
        )}

        {done === 'applied' && (
          <div className="tune-modal-footer">
            <button className="btn btn-primary" onClick={onClose}>
              Cerrar
            </button>
          </div>
        )}
      </div>
    </>
  );
};

/* ── Sub-component: one symbol change card ─────────────────── */
const SymbolChangeCard: React.FC<{ result: TuneSymbolResult }> = ({ result }) => {
  const { current_params: cur, proposed_params: prop, proposal_detail: detail } = result;
  if (!prop || !detail) return null;

  return (
    <div className="tune-change-card">
      <div className="tune-change-header">
        <span className="tune-change-symbol">{result.symbol.replace('USDT', '')}</span>
        <span className="tune-change-improvement">+{detail.improvement_pct}%</span>
      </div>
      <table className="tune-change-table">
        <thead>
          <tr>
            <th></th>
            <th>SL</th>
            <th>TP</th>
            <th>BE</th>
            <th>P&amp;L Val</th>
          </tr>
        </thead>
        <tbody>
          <tr className="tune-row-current">
            <td>Actual</td>
            <td>{cur.atr_sl_mult}x</td>
            <td>{cur.atr_tp_mult}x</td>
            <td>{cur.atr_be_mult}x</td>
            <td>${(result.current_val_pnl ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
          </tr>
          <tr className="tune-row-proposed">
            <td>Nuevo</td>
            <td>{prop.atr_sl_mult}x</td>
            <td>{prop.atr_tp_mult}x</td>
            <td>{prop.atr_be_mult}x</td>
            <td className="tune-pnl-positive">${detail.val_pnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
          </tr>
        </tbody>
      </table>
      <div className="tune-change-meta">
        <span>PF: {detail.val_pf.toFixed(2)}</span>
        <span>{detail.total_trades} trades</span>
      </div>
    </div>
  );
};

export default TuneReportModal;
```

- [ ] **Step 2: Build and verify**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TuneReportModal.tsx
git commit -m "feat(frontend): add TuneReportModal component (#137)"
```

---

## Task 6: Frontend — Header Badge + App Integration

**Files:**
- Modify: `frontend/src/components/Header.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add pending tune badge to Header**

Update `HeaderProps`:

```typescript
interface HeaderProps {
  scannerRunning: boolean;
  lastRefresh: Date | null;
  scanning: boolean;
  hasPendingTune: boolean;
  onRefresh: () => void;
  onScan: () => void;
  onConfigOpen: () => void;
  onTuneOpen: () => void;
}
```

Add the badge button in the header-right div, before the config button:

```tsx
        {hasPendingTune && (
          <button
            className="btn btn-icon tune-badge-btn"
            onClick={onTuneOpen}
            title="Parametros optimizados pendientes de revision"
            aria-label="Revision de parametros"
          >
            <span className="tune-badge-icon">&#x2691;</span>
            <span className="tune-badge-dot" />
          </button>
        )}
```

Update the component to destructure the new props.

- [ ] **Step 2: Integrate in App.tsx**

Add imports:

```typescript
import TuneReportModal from './components/TuneReportModal';
import { getTuneLatest, applyTune, rejectTune } from './api';
import type { TuneResult } from './types';
```

Add state:

```typescript
const [tuneResult, setTuneResult] = useState<TuneResult | null>(null);
const [tuneModalOpen, setTuneModalOpen] = useState(false);
```

Add tune fetch to `fetchAll`:

```typescript
const fetchAll = useCallback(async () => {
    try {
      const [symbolsRes, statusRes, signalsRes, tuneRes] = await Promise.all([
        getSymbols(),
        getStatus(),
        getSignals({ limit: 20, only_signals: false, since_hours: 24 }),
        getTuneLatest().catch(() => null),
      ]);
      setSymbols(symbolsRes.symbols);
      setStatus(statusRes);
      setSignals(signalsRes.signals);
      setTuneResult(tuneRes);
      setLastRefresh(new Date());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error desconocido');
    } finally {
      setLoading(false);
    }
  }, []);
```

Add handlers:

```typescript
const handleTuneApply = useCallback(async () => {
    await applyTune();
    await fetchAll();
  }, [fetchAll]);

  const handleTuneReject = useCallback(async () => {
    await rejectTune();
    await fetchAll();
  }, [fetchAll]);

  const hasPendingTune = tuneResult?.status === 'pending';
```

Update Header props:

```tsx
<Header
  scannerRunning={scannerRunning}
  lastRefresh={lastRefresh}
  scanning={scanning}
  hasPendingTune={hasPendingTune}
  onRefresh={handleRefresh}
  onScan={handleScan}
  onConfigOpen={() => setConfigOpen(true)}
  onTuneOpen={() => setTuneModalOpen(true)}
/>
```

Add modal after ConfigPanel:

```tsx
{tuneModalOpen && tuneResult && (
  <TuneReportModal
    tune={tuneResult}
    onApply={handleTuneApply}
    onReject={handleTuneReject}
    onClose={() => setTuneModalOpen(false)}
  />
)}
```

- [ ] **Step 3: Build and verify**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Header.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add tune notification badge and modal integration (#137)"
```

---

## Task 7: CSS Styles for Tune Modal

**Files:**
- Modify: `frontend/src/index.css` (or wherever global styles live)

- [ ] **Step 1: Add tune modal styles**

Find the main CSS file and add:

```css
/* ── Tune Report Modal ────────────────────────────────────── */

.tune-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  z-index: 1000;
}

.tune-modal {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(600px, 92vw);
  max-height: 85vh;
  background: var(--bg-card, #1a1a2e);
  border: 1px solid var(--border, #2d2d44);
  border-radius: 12px;
  z-index: 1001;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}

.tune-modal-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 20px 24px 16px;
  border-bottom: 1px solid var(--border, #2d2d44);
}

.tune-modal-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0;
  color: var(--text, #e0e0e0);
}

.tune-modal-subtitle {
  font-size: 13px;
  color: var(--text-muted, #888);
  margin: 4px 0 0;
}

.tune-close-btn {
  background: none;
  border: none;
  color: var(--text-muted, #888);
  font-size: 18px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
}
.tune-close-btn:hover { background: var(--bg-hover, #2d2d44); }

.tune-modal-body {
  padding: 16px 24px;
  overflow-y: auto;
  flex: 1;
}

.tune-banner {
  padding: 10px 24px;
  font-size: 13px;
  font-weight: 500;
  text-align: center;
}
.tune-banner--applied { background: rgba(34, 197, 94, 0.15); color: #22c55e; }
.tune-banner--rejected { background: rgba(239, 68, 68, 0.15); color: #ef4444; }

.tune-section { margin-bottom: 20px; }

.tune-section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text, #e0e0e0);
  margin: 0 0 12px;
}

.tune-empty {
  text-align: center;
  padding: 32px;
  color: var(--text-muted, #888);
  font-size: 14px;
}

/* Change card */
.tune-change-card {
  background: var(--bg-hover, #16162a);
  border: 1px solid var(--border, #2d2d44);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 10px;
}

.tune-change-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.tune-change-symbol {
  font-size: 15px;
  font-weight: 600;
  color: var(--text, #e0e0e0);
}

.tune-change-improvement {
  font-size: 14px;
  font-weight: 600;
  color: #22c55e;
  background: rgba(34, 197, 94, 0.12);
  padding: 2px 8px;
  border-radius: 4px;
}

.tune-change-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.tune-change-table th {
  text-align: right;
  padding: 4px 10px;
  color: var(--text-muted, #888);
  font-weight: 500;
  font-size: 12px;
}
.tune-change-table th:first-child { text-align: left; }
.tune-change-table td {
  text-align: right;
  padding: 5px 10px;
  color: var(--text, #e0e0e0);
}
.tune-change-table td:first-child { text-align: left; font-weight: 500; }
.tune-row-current td { color: var(--text-muted, #888); }
.tune-row-proposed td { color: var(--text, #e0e0e0); }
.tune-pnl-positive { color: #22c55e !important; font-weight: 600; }

.tune-change-meta {
  display: flex;
  gap: 16px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-muted, #888);
}

/* Kept symbols */
.tune-collapse-btn {
  background: none;
  border: none;
  color: var(--text-muted, #888);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 0;
}
.tune-collapse-btn:hover { color: var(--text, #e0e0e0); }

.tune-kept-list { margin-top: 8px; }
.tune-kept-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 12px;
  font-size: 13px;
  border-bottom: 1px solid var(--border, #1a1a2e);
}
.tune-kept-symbol { font-weight: 500; color: var(--text, #e0e0e0); width: 80px; }
.tune-kept-params { color: var(--text-muted, #888); flex: 1; }
.tune-kept-status { color: #22c55e; font-size: 12px; }

/* Footer */
.tune-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 24px;
  border-top: 1px solid var(--border, #2d2d44);
}

.tune-apply-btn {
  min-width: 160px;
}

/* Badge in header */
.tune-badge-btn {
  position: relative;
}
.tune-badge-icon { font-size: 18px; }
.tune-badge-dot {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 8px;
  height: 8px;
  background: #f59e0b;
  border-radius: 50%;
  animation: tune-pulse 2s infinite;
}
@keyframes tune-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.3); }
}
```

- [ ] **Step 2: Build and verify**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
git add frontend/src/
git commit -m "feat(frontend): add tune modal styles (#137)"
```

---

## Task 8: Final — Test Everything

- [ ] **Step 1: Run backend tests**

Run: `python -m pytest tests/test_api.py tests/test_auto_tune.py -v`
Expected: All pass

- [ ] **Step 2: Build frontend**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Push all commits**

```bash
git push origin main
```
