import React, {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';
import * as authApi from './api';
import type { AuthUser } from './api';

interface AuthContextValue {
  user: AuthUser | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<boolean>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Hydrate from /auth/me on first mount. Order:
  //   1. GET /setup/status — if setup_required && we're already on /setup,
  //      skip the auth probe entirely (no point trying to authenticate
  //      against a system with no users). If the call FAILS (network etc),
  //      assume setup_required=false and proceed normally — the real auth
  //      gate is the middleware on every other route, not this hint.
  //   2. GET /auth/me with cookies.
  //   3. On 401, one silent refresh attempt; if that also fails, user=null.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Step 1: setup status (fail-tolerant)
        let setupRequired = false;
        try {
          const r = await fetch('/api/setup/status', { credentials: 'include' });
          if (r.ok) {
            const j = await r.json();
            setupRequired = !!j.setup_required;
          }
        } catch (err) {
          // Network/backend slow — log and proceed as if setup is done.
          // The middleware is the real gate.
          // eslint-disable-next-line no-console
          console.warn('[auth] /setup/status failed; assuming setup complete:', err);
        }
        if (cancelled) return;

        if (setupRequired) {
          // Don't even probe /auth/me — there's no user to authenticate.
          // ProtectedRoute will see user=null and the SetupPage route will
          // catch / when present.
          return;
        }

        // Step 2: hydrate user
        const meResp = await authApi.me();
        if (cancelled) return;
        if (meResp) {
          setUser(meResp.user);
          return;
        }

        // Step 3: one silent refresh
        const ref = await authApi.refresh();
        if (cancelled) return;
        if (ref) {
          setUser(ref.user);
          const me2 = await authApi.me();
          if (!cancelled && me2) setUser(me2.user);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const resp = await authApi.login(email, password);
    setUser(resp.user);
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
    }
  }, []);

  // Public refresh — returns whether a fresh user object was obtained.
  // Used by the api.ts 401 interceptor.
  const refresh = useCallback(async (): Promise<boolean> => {
    const r = await authApi.refresh();
    if (r) {
      setUser(r.user);
      return true;
    }
    setUser(null);
    return false;
  }, []);

  const value = useMemo(
    () => ({ user, isLoading, login, logout, refresh }),
    [user, isLoading, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export { AuthContext };
