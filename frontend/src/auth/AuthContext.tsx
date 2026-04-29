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

  // Hydrate from /auth/me on first mount. If unauthenticated, try one
  // silent refresh (in case the access cookie expired but the refresh is
  // still valid). If that also fails, leave user=null.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const meResp = await authApi.me();
        if (cancelled) return;
        if (meResp) {
          setUser(meResp.user);
          return;
        }
        // Try one refresh
        const r = await authApi.refresh();
        if (cancelled) return;
        if (r) {
          setUser(r.user);
          // Re-fetch /me to get the canonical user record after refresh
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
