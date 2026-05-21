import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import UserMenu from './UserMenu';
import type { AuthUser } from '../auth/api';

const _fakeUser: AuthUser = {
  id: 1,
  email: 'a@example.com',
  role: 'admin',
  is_active: true,
  last_login_at: null,
};

describe('UserMenu', () => {
  it('shows badge "1" on Conexiones when telegram is unconfigured', () => {
    render(
      <UserMenu
        open={true}
        user={_fakeUser}
        onClose={() => {}}
        onLogout={() => {}}
        onConnectionsOpen={() => {}}
        telegramConfigured={false}
      />
    );
    const conexionesBtn = screen.getByText('Conexiones').closest('button')!;
    expect(conexionesBtn.textContent).toContain('1');
  });

  it('hides badge on Conexiones when telegram is configured', () => {
    render(
      <UserMenu
        open={true}
        user={_fakeUser}
        onClose={() => {}}
        onLogout={() => {}}
        onConnectionsOpen={() => {}}
        telegramConfigured={true}
      />
    );
    const conexionesBtn = screen.getByText('Conexiones').closest('button')!;
    expect(conexionesBtn.textContent).not.toContain('1');
  });

  it('calls onConnectionsOpen when Conexiones is clicked', async () => {
    const onOpen = vi.fn();
    render(
      <UserMenu
        open={true}
        user={_fakeUser}
        onClose={() => {}}
        onLogout={() => {}}
        onConnectionsOpen={onOpen}
        telegramConfigured={false}
      />
    );
    await userEvent.click(screen.getByText('Conexiones'));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
