// ============================================================
// useLiveTicker — polls /ticker every `intervalMs` and returns
// a {symbol: price} map of live spot prices.
//
// Independent of the 5-min scan cadence. Server caches ~2.5s so
// polling at 3s here lands one upstream hit per tab cycle.
// ============================================================

import { useEffect, useState } from 'react';
import { getTicker } from '../api';

export function useLiveTicker(intervalMs: number = 3000): Record<string, number> {
  const [prices, setPrices] = useState<Record<string, number>>({});

  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      try {
        const resp = await getTicker();
        if (!cancelled && resp.prices) setPrices(resp.prices);
      } catch {
        // Swallow — keep the last known map. Failures are logged server-side.
      }
    };

    tick();
    const id = setInterval(tick, intervalMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [intervalMs]);

  return prices;
}
