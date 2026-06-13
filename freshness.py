"""Frescura de estado vivo como TIPO (no un helper opcional). Liveness operacional.

LiveSnapshot envuelve un payload con su marca temporal OBLIGATORIA y clasifica
fresco/rancio/muerto. `to_response` SIEMPRE inyecta la frescura — el payload no se
puede emitir sin ella (la frescura vive en el CONTRATO, no en la disciplina del
lector). Eje SNAPSHOT, distinto de screener.valley_filter.classify_liveness
(liveness de SÍMBOLO sobre velas). Puro: sin red, sin DB. Spec §2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _edad_seg(generated_at: str) -> float | None:
    """Antigüedad en segundos de un ISO-8601 (tolera Z/offset/naive), o None si
    no parsea."""
    try:
        ts = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


@dataclass(frozen=True)
class LiveSnapshot:
    """Estado vivo + su marca temporal obligatoria. Inconstruible sin generated_at
    (aunque sea None explícito); to_response siempre emite la frescura."""
    payload: dict
    generated_at: str | None
    umbral_seg: float

    @property
    def estado(self) -> str:
        """'fresco' | 'rancio' | 'muerto'."""
        if not self.generated_at:
            return "muerto"
        edad = _edad_seg(self.generated_at)
        if edad is None:
            return "muerto"
        return "rancio" if edad > self.umbral_seg else "fresco"

    def to_response(self) -> dict:
        edad = _edad_seg(self.generated_at) if self.generated_at else None
        return {**self.payload, "frescura": {
            "estado": self.estado, "edad_seg": edad,
            "generated_at": self.generated_at, "umbral_seg": self.umbral_seg}}


def classify_freshness(generated_at: str | None, umbral_seg: float) -> str:
    """Atajo funcional. NO confundir con classify_liveness (eje símbolo)."""
    return LiveSnapshot(payload={}, generated_at=generated_at, umbral_seg=umbral_seg).estado
