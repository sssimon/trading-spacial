import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './useAuth';

interface Props {
  children: React.ReactNode;
  /** Optional — restrict to a specific role. */
  requireRole?: 'admin' | 'viewer';
}

export const ProtectedRoute: React.FC<Props> = ({ children, requireRole }) => {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div style={{ padding: 32, textAlign: 'center', color: '#888' }}>
        Loading…
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (requireRole && user.role !== requireRole) {
    return (
      <div style={{ padding: 32, color: '#c0392b' }}>
        Access denied. Required role: <strong>{requireRole}</strong>.
      </div>
    );
  }

  return <>{children}</>;
};
