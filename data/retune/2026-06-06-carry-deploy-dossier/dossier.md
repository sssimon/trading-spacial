# Dossier de deploy — funding-rate carry delta-neutral (celda 2)

**Fecha del dossier:** 2026-06-06 · **Puerta:** marco §5 (6 campos + decisión
explícita única) · **Regla dura:** ningún sub-PASS autoriza; este dossier es
condición NECESARIA; la decisión explícita de Samuel es la SUFICIENTE.

---

## Campo 1 — Verdict F PASS con coordenada de procedencia

- **VERDICT: PASS** (falsificación pre-registrada, corrida 2026-06-03, una
  sola corrida del evaluador).
- **Coordenada:** `E1/F/n_F=2` (segunda celda F corrida de la Edición 1).
- **Gate A:** carry pooled net-of-v3 **6.33%/año**, CI95 [5.02, 7.45], 9/9
  símbolos líquidos positivos, LOO-robusto.
- **Gate B2 (shock sintético):** PASS pero THIN — un shock 7.5% > carry anual;
  sobrevive vía acumulación (~2.4 años al rate fósil).
- **Deflación por regla de activación:** NO aplica — no hubo elección entre
  ≥2 VERDICTs F PASS comparables (torneo de un competidor; el único otro
  falsificador F construido, celda 4, está parqueado SIN correr, issue #568).
- **Artefacto:** `data/retune/2026-06-03-funding-carry-falsification/`.

## Campo 2 — Realizabilidad vigente del shadow (a fecha de ESTE dossier)

Shadow v0.1, lectura 2026-06-05T08:15Z (monitor diario, 9/9 símbolos, 0
dropped, ~3 días de operación, bloque semanal 1 en curso):

| Métrica | Vivo (2026-06-05) | Referencia |
|---|---|---|
| R_pooled | **3.69%/año** | banda fósil [5.90, 8.12] |
| CI95 vivo | [1.59, 5.71] | CI_hi NO alcanza el piso fósil |
| vs cost floor v3 (0.39%) | **9.6×** encima | kill exige CI_hi < floor |
| decay_state | THIN | pre-registrado: THIN ≠ kill |
| blocks_below_floor | **0 / 4** | kill = 4 bloques semanales bajo floor |

**Lectura honesta:** el edge está VIVO (lejísimos del kill) pero corre a
~58% del punto fósil y por debajo de la banda fósil completa. El umbral
pre-registrado de muerte (decay-kill) no se ha disparado ni acumula. N de
bloques completos: 0 (el monitor lleva 3 días; la unidad del kill es la
semana). **Vigencia: VIVO-THIN con historia corta.**

## Campo 3 — Fricción de ejecución medida (misma calibration_identity)

- **exec-realism v0.2, U1 VERDICT: PASS** (2026-06-04T08:04Z,
  settlement-adjacent, 9/9 símbolos).
- **T_FLOOR_REAL = 0.00201** vs T_FLOOR v3 = 0.00386 → ratio **0.52**: el
  libro real es ~2× más barato que el upper bound v3. La fricción NO mata el
  edge; R_ci_lo vivo (0.94% en la corrida U1) cubre el piso real.
- **`calibration_identity_hash`: `1b616f742ed2eec7…` — IDÉNTICO al del shadow
  v0.1** (verificado 2026-06-06). Nota de la regla dura: esta coincidencia NO
  constituye autorización; se reporta como consistencia de mundo.
- **Artefacto:** `data/retune/2026-06-04-funding-carry-exec-realism/`.

## Campo 4 — Sizing tail-aware contra el shock G2 pre-registrado

Lo que está medido (estudio tail-kill 2026-06-03 + falsificación G2):

- **Leverage congelado: 2.0×** (conservador; liquidación exige ~50% adverso —
  no binding). Per-interval mark: carry total 27% en la ventana.
- **Shock G2 sintético** (0.5%/8h pagado, 5 días, calibrado LUNA/FTX):
  `post_shock_pooled = +3.06%` — el portafolio SOBREVIVE el shock y queda
  positivo. El kill por funding-negativo NO añade valor (delta −0.0018,
  falsificado): el carry líquido ya es tail-robusto sin kill.
- **Matemática por unidad de equity a 2×:** costo bruto del shock G2 ≈ 7.5%
  del notional × 2 piernas apalancadas → ~15% del equity en el peor caso
  sintético sostenido. Recuperación al rate VIVO (3.69% × 2× = 7.4%/año sobre
  equity): **~2 años**. Al rate fósil: ~1 año. Esta es la asimetría que la
  decisión de capital debe aceptar: cola corta y profunda, recuperación
  lenta-pero-positiva, y la protección NO es un kill (falsificado) sino el
  tamaño.
- **Lo que el campo NO contiene:** un monto de capital. El sizing-as-edge fue
  matado por la junta 2026-06-03 (leverage = escala, no edge); el
  sizing-as-parámetro-de-deploy es exactamente la decisión del campo 6.

## Campo 5 — Kill live pre-registrado

- **Condición:** decay-kill — `R_ci_hi < t_floor` (0.00386, anclas FROZEN
  2026-06-03) durante **N=4 bloques semanales consecutivos** (W=1,
  DECAY_KILL_N=4, congelados por power.py v2).
- **Quién/qué lo evalúa:** el monitor shadow v0.1 (corrida diaria
  automatizada, server) escribe `decay_state` y `blocks_below_floor`;
  la lectura humana es semanal (Samuel o sesión de Claude al pedirla).
- **Acción al disparo:** cierre de posiciones reales + la celda 2 NO se
  reabre (el kill es del deploy, no del verdict; el verdict fósil queda).
- **Declarado honesto:** NO existe un DD-kill pre-registrado sobre posiciones
  reales adicional al decay-kill (el kill por señal de funding fue falsificado
  como sin-valor). Si la decisión de capital quiere un hard-stop de equity,
  debe pre-registrarse ANTES del deploy como adenda a este dossier.

## Campo 6 — Decisión explícita (PENDIENTE — única condición suficiente)

- **Decisor:** Samuel, entrada en `mex log` con el hash de este dossier.
- **La decisión debe fijar:** capital inicial en exchange ($), confirmación
  del leverage 2.0×, y si se añade hard-stop de equity (ver campo 5).
- **Hash del dossier:** se computa sobre este archivo al commitearlo y se
  registra en la entrada de `mex log` de la decisión.

---

## Síntesis para el decisor

Los campos 1-5 CIERRAN: verdict PASS con procedencia limpia, fricción medida
2× mejor que el bound, tail-survival demostrada a 2×, kill pre-registrado con
evaluador automatizado. La tensión honesta vive en el campo 2: **el rate vivo
(3.69%) corre por debajo de la banda fósil [5.90, 8.12] con solo 3 días de
historia** — vivo y lejos del kill, pero más flaco que lo que el catálogo
prometió. Las dos posturas defendibles: (a) deploy chico ya — el reloj de
realización solo corre con capital real, y el decay-kill protege la salida;
(b) esperar 2-4 bloques semanales de shadow para que la vigencia tenga N
antes de poner plata. Ambas son decisiones, no cálculos: el dossier no
autoriza, habilita.
