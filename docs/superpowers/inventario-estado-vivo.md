# Inventario de estado vivo (liveness operacional)

Lista **CERRADA** de todo reader de estado vivo que cruza una frontera de proceso
(el writer y el reader NO son el mismo acto temporal). Cada entrada está:

- **migrado** — usa `freshness.LiveSnapshot` y tiene un owner de frescura nombrado;
- **respira-vía-scanner** — alimentado por un thread del lifespan (vivo), pero
  todavía sin el tipo `LiveSnapshot` en el reader (**deuda nombrada**);
- **deuda #N** — pendiente de revivir o migrar (con ticket).

El patrón está **ARMADO, no falsamente "cerrado"**: este inventario es la verdad del
estado real — qué órgano sabe su edad y cuál es deuda visible. Tocar un reader
no-migrado de esta lista sin migrarlo viola el no-negociable #8 de `CLAUDE.md`.

Spec: `docs/superpowers/specs/es/2026-06-13-liveness-frescura-huerfanos-design.md`.

| Reader | Writer | Owner de frescura en prod | Frescura en contrato | Estado |
|---|---|---|---|---|
| `GET /valley-candidates` | `tools.run_valley_screener.regenerate` | `screener_loop` (**trading-scanner.service**, 6h) | `LiveSnapshot` | **migrado** |
| `GET /dossier/{symbol}` | `research.dossier.build_dossier_live` | on-request (auto-cura tras TTL) | `LiveSnapshot` | **migrado** |
| `GET /levels/{symbol}` | Binance on-request (vivo cada request, sin caché) | n/a (no cruza snapshot) | `LiveSnapshot` | **migrado** · plan `2026-06-15-valles-copiloto-agente-real` PASO 0 |
| `GET /valley-eval/{symbol}` | Binance on-request (vivo cada request, sin caché) | n/a (no cruza snapshot) | `LiveSnapshot` | **migrado** · plan `2026-06-15-valles-copiloto-agente-real` PASO 0 |
| `observed_orders` + F3a `track_live` | `tools.sync_binance_spot.sync_tenant` | `sync_loop` (**trading-scanner.service**, 5min) | estado en DB (`updated_at`) | **migrado (latido)** · deuda: sin `LiveSnapshot` en el reader |
| `symbols_status.json` | `update_symbols_json` | `scanner_loop` (**trading-scanner.service**) | trae `updated_at` | **respira-vía-scanner** · deuda: sin `LiveSnapshot` |
| `equity` | computado on-read (`compute_real_equity`) | n/a (vivo por consulta) | n/a | **respira** (no cruza frontera-snapshot) |
| `kill_switch state` | `health_monitor_loop` | **trading-scanner.service** | observability | **respira-vía-scanner** · deuda: sin `LiveSnapshot` |
| `GET /health` (liveness del scanner) | `api.scanner_liveness` (último scan ts DB) | **trading-scanner.service** | `LiveSnapshot` | **migrado** · epic deploy zero-downtime |

## Cómo se paga la deuda
Los readers "respira-vía-scanner" están vivos (un thread del lifespan los regenera),
así que su riesgo agudo es bajo — pero aún no declaran su frescura en el contrato.
Se migran a `LiveSnapshot` **cuando alguien los toca** por otra razón (el gate #8 lo
fuerza). No se retrofitean en masa: eso sería tocar código estable por un riesgo que
solo es agudo en los huérfanos ya revividos.

## Cómo añadir una pieza nueva
Antes de mergear una pieza con estado vivo que cruza una frontera de proceso:
1. ¿Quién la corre en prod, y con qué cadencia? (un thread del lifespan en
   `scanner/runtime.py`, registrado en `_managed_threads`). Nómbralo aquí.
2. ¿El reader emite su frescura vía `freshness.LiveSnapshot`? Si no, no mergea.
3. Añade la fila a esta tabla como `migrado`.
