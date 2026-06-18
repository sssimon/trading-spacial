import { test, expect } from '@playwright/test';

test('la cabecera de régimen aparece y NO modula la lista de coins', async ({ page }) => {
  await page.addInitScript(() => localStorage.removeItem('vw_sym'));  // ver la lista, no una idea
  await page.goto('/');
  // La cabecera de régimen está presente con su frase honesta.
  await expect(page.getByText(/régimen del mercado/i)).toBeVisible();
  await expect(page.getByTestId('regime-estado')).toBeVisible();
  // Doctrina: el estado del régimen NO añade clases de color/énfasis a las tarjetas de coins.
  const cards = page.locator('[data-testid="pick-card"]');
  if (await cards.count() > 0) {
    const cls = await cards.first().getAttribute('class');
    expect(cls ?? '').not.toMatch(/alts|btc|regime/i);
  }
});
