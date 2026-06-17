import { useEffect, useState } from 'react';
import type { PlanDerived, PlanLive, PlanConducta } from '../../../types';
import { getPlanDerive, getPlanLive, getPlanConducta } from '../../../api';

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

const idle = <T,>(): AsyncState<T> => ({ data: null, loading: false, error: null });
const pending = <T,>(): AsyncState<T> => ({ data: null, loading: true, error: null });

export interface JugadaState {
  derived: AsyncState<PlanDerived>;
  live: AsyncState<PlanLive>;
  conducta: AsyncState<PlanConducta>;
}

export function useJugada(symbol: string, livePrice: number | null): JugadaState {
  const [derived, setDerived] = useState<AsyncState<PlanDerived>>(idle);
  const [live, setLive]       = useState<AsyncState<PlanLive>>(idle);
  const [conducta, setConducta] = useState<AsyncState<PlanConducta>>(idle);

  useEffect(() => {
    if (!symbol) return;

    let cancel = false;

    // derived: solo si tenemos livePrice
    if (livePrice != null) {
      setDerived(pending());
      getPlanDerive(symbol, livePrice)
        .then((d) => { if (!cancel) setDerived({ data: d, loading: false, error: null }); })
        .catch((e: unknown) => {
          if (!cancel) {
            const msg = e instanceof Error ? e.message : 'Error al derivar el plan';
            setDerived({ data: null, loading: false, error: msg });
          }
        });
    } else {
      setDerived(idle());
    }

    // live
    setLive(pending());
    getPlanLive(symbol)
      .then((d) => { if (!cancel) setLive({ data: d, loading: false, error: null }); })
      .catch((e: unknown) => {
        if (!cancel) {
          const msg = e instanceof Error ? e.message : 'Error al cargar el estado vivo';
          setLive({ data: null, loading: false, error: msg });
        }
      });

    // conducta
    setConducta(pending());
    getPlanConducta(symbol)
      .then((d) => { if (!cancel) setConducta({ data: d, loading: false, error: null }); })
      .catch((e: unknown) => {
        if (!cancel) {
          const msg = e instanceof Error ? e.message : 'Error al cargar la conducta';
          setConducta({ data: null, loading: false, error: msg });
        }
      });

    return () => { cancel = true; };
  }, [symbol, livePrice]);

  return { derived, live, conducta };
}
