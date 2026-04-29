import React, { useState, type FormEvent } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import './SetupPage.css';

const BASE = '/api';

export const SetupPage: React.FC = () => {
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  if (done) {
    return <Navigate to="/login" replace />;
  }

  if (!token) {
    return (
      <div className="setup-page">
        <div className="setup-card">
          <h1>Setup token missing</h1>
          <p>
            Open the URL printed by the server in your console output. It
            looks like <code>/setup?token=…</code>.
          </p>
        </div>
      </div>
    );
  }

  const validateClient = (): string | null => {
    if (password !== confirm) return 'passwords do not match';
    if (password.length < 12) return 'password must be at least 12 characters';
    if (!/[A-Za-z]/.test(password)) return 'password must contain at least one letter';
    if (!/[0-9]/.test(password)) return 'password must contain at least one digit';
    return null;
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    const clientErr = validateClient();
    if (clientErr) {
      setError(clientErr);
      return;
    }

    setSubmitting(true);
    try {
      const fd = new FormData();
      fd.append('token', token);
      fd.append('email', email);
      fd.append('password', password);
      fd.append('confirm_password', confirm);

      const resp = await fetch(`${BASE}/setup`, {
        method: 'POST',
        body: fd,
        headers: { Accept: 'application/json' },
      });

      if (resp.status === 201) {
        setDone(true);
        return;
      }
      if (resp.status === 404) {
        // Setup already completed elsewhere, or token rotated.
        setError(
          'Setup is no longer available. Either it was completed in another ' +
          'window, or the server restarted (token invalidated).',
        );
        return;
      }
      const body = await resp.json().catch(() => null);
      setError(body?.detail ?? `Server error (${resp.status})`);
    } catch (err) {
      setError('Network error. Check the API is running.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="setup-page">
      <form className="setup-card" onSubmit={onSubmit}>
        <h1>First-time setup</h1>
        <p className="setup-subtitle">
          Create the admin user. This page only exists before the first
          user is created.
        </p>

        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
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
            autoComplete="new-password"
            minLength={12}
            required
          />
        </label>

        <label>
          Confirm password
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            minLength={12}
            required
          />
        </label>

        <p className="setup-rules">
          Requirements: at least 12 characters, ≤ 72 bytes, must contain a
          letter and a digit.
        </p>

        {error && (
          <div className="setup-error" role="alert">{error}</div>
        )}

        <button type="submit" disabled={submitting}>
          {submitting ? 'Creating…' : 'Create admin and complete setup'}
        </button>
      </form>
    </div>
  );
};
