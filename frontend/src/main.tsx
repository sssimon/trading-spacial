// ============================================================
// main.tsx — React 18 entry point
// ============================================================

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';

// Foundation styles — order matters.
// (1) Legacy stylesheet — still in use by Positions, Kill-switch, modals,
//     NotificationToast, ErrorBoundary, auth pages. These will be migrated
//     in follow-up PRs; until then App.css owns their CSS.
import './App.css';
// (2) New design tokens — defines all --nbc-*, --bull, --bear, --d-* CSS
//     custom properties consumed by every .module.css file below.
import './styles/tokens.css';
// (3) Base reset + global utility classes (.btn, .label, .num, .prose, .term).
//     Defined AFTER App.css so the new visual language wins for shared
//     selectors like `.btn`.
import './styles/base.css';
// (4) Tema CÁLIDO global (rediseño Mercado, 2026-06-16). Remapea los
//     tokens --nbc-* a la paleta cálida (papel/arcilla). Importado DESPUÉS
//     de tokens.css para que el remap gane por orden de fuente.
import './styles/warm-tokens.css';
// (5) Estilos de componentes del Mercado cálido (.mw-*), hoja global.
import './styles/mercado-warm.css';
// (6) SP3 — extensión de tokens del tema cálido para la vista per-coin de Valles.
//     Añade --read (medida de lectura). Los tokens de color/tipo ya los provee
//     warm-tokens.css; este import declara la intención y el scope del SP3.
import './styles/sp3-warm.css';

import App from './App';
import { AuthProvider } from './auth/AuthContext';
import { LoginPage } from './auth/LoginPage';
import { ProtectedRoute } from './auth/ProtectedRoute';
import { SetupPage } from './auth/SetupPage';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Root element #root not found in the document.');
}

createRoot(rootElement).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public routes — no auth check */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/setup" element={<SetupPage />} />
          {/* Everything else is gated by ProtectedRoute */}
          <Route
            path="/*"
            element={
              <ProtectedRoute>
                <App />
              </ProtectedRoute>
            }
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>
);
