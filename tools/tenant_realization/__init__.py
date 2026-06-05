"""Monitor de realización per-tenant: la cuenta real como evidencia de primera clase.

Lee (read-only, vía ssh) las posiciones cerradas de un tenant en el signals.db
de producción y produce el reporte que convierte "le va bien/mal" en un número
con intervalo: P&L real, retorno sobre capital desplegado (no sobre el balance
nocional de la plataforma), descomposición señal-vs-salida-manual (el hallazgo
q2), y el CI del per-trade acumulándose.

Contexto: 2026-06-06 — capital familiar real corre el mundo direccional desde
2026-05-21 sin instrumentar; el balance de la plataforma ($10k) es nocional,
las operaciones y P&L en $ son reales (Binance).
"""
