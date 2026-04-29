import React, { useState, type FormEvent } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './useAuth';
import { AuthError } from './api';
import './LoginPage.css';

export const LoginPage: React.FC = () => {
  const { user, login, isLoading } = useAuth();
  const location = useLocation();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (isLoading) {
    return <div className="login-loading">Loading…</div>;
  }

  if (user) {
    // Already authenticated — bounce to the page they came from (or root).
    const from =
      (location.state as { from?: { pathname?: string } } | null)?.from
        ?.pathname || '/';
    return <Navigate to={from} replace />;
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      // AuthContext updates user → re-renders → Navigate above takes over.
    } catch (err) {
      if (err instanceof AuthError) {
        if (err.status === 429) {
          setError('Too many login attempts. Try again in a few minutes.');
        } else if (err.status === 401) {
          setError('Invalid email or password.');
        } else {
          setError(`Login failed (${err.status}). ${err.message}`);
        }
      } else {
        setError('Network error. Check your connection.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-form" onSubmit={onSubmit}>
        <h1>Sign in</h1>
        <p className="login-subtitle">trading-spacial</p>

        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
            autoFocus
          />
        </label>

        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            minLength={1}
          />
        </label>

        {error && <div className="login-error" role="alert">{error}</div>}

        <button type="submit" disabled={submitting}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
};
