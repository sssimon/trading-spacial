// recap.ts
import type { ValleyEval, SrLevels, Dossier } from '../../types';

export const vidaRecap = (v: ValleyEval | null): string =>
  !v ? '—' : v.estado === 'no_disponible' ? '—' : v.candidata === false ? 'No en la parte baja' : 'En la parte baja de su rango';

export const nivelesRecap = (n: SrLevels | null): string => {
  if (!n || n.estado === 'no_disponible') return '—';
  if (n.zonas.length === 0) return 'Sin paredes claras';
  const d = n.ubicacion.dentro_de;
  return d ? (d.tipo === 'soporte' ? 'En un piso' : 'En un techo') : 'En el medio';
};

export const dossierRecap = (d: Dossier | null): string =>
  !d ? '—' : d.estado_general === 'rastreable' ? 'Se sabe quién' : d.estado_general === 'opaco' ? 'Sin rastro público' : '—';
