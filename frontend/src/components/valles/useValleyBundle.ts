import { useEffect, useRef, useState } from 'react';
import type { ValleyEval, SrLevels, Dossier } from '../../types';
import { getValleyEval, getLevels, getDossier } from '../../api';

export interface AsyncState<T> { data: T | null; loading: boolean; error: boolean; }
const loadingState = <T,>(): AsyncState<T> => ({ data: null, loading: true, error: false });

export interface ValleyBundle {
  vida:     AsyncState<ValleyEval>;
  niveles:  AsyncState<SrLevels>;
  dossier:  AsyncState<Dossier>;
  refreshDossier: () => void;
}

export function useValleyBundle(symbol: string): ValleyBundle {
  const [vida, setVida]       = useState<AsyncState<ValleyEval>>(loadingState);
  const [niveles, setNiveles] = useState<AsyncState<SrLevels>>(loadingState);
  const [dossier, setDossier] = useState<AsyncState<Dossier>>(loadingState);
  const [refreshN, setRefreshN] = useState(0);
  const forceRef = useRef(false);

  useEffect(() => {
    if (!symbol) return;
    let active = true;
    setVida(loadingState()); setNiveles(loadingState());
    getValleyEval(symbol)
      .then((d) => { if (active) setVida({ data: d, loading: false, error: false }); })
      .catch(() => { if (active) setVida({ data: null, loading: false, error: true }); });
    getLevels(symbol)
      .then((d) => { if (active) setNiveles({ data: d, loading: false, error: false }); })
      .catch(() => { if (active) setNiveles({ data: null, loading: false, error: true }); });
    return () => { active = false; };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    let active = true;
    const force = forceRef.current; forceRef.current = false;
    setDossier(loadingState());
    getDossier(symbol, force)
      .then((d) => { if (active) setDossier({ data: d, loading: false, error: false }); })
      .catch(() => { if (active) setDossier({ data: null, loading: false, error: true }); });
    return () => { active = false; };
  }, [symbol, refreshN]);

  return { vida, niveles, dossier, refreshDossier: () => { forceRef.current = true; setRefreshN((n) => n + 1); } };
}
