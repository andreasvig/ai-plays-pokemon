export default async function ({page, expect, screenshot}) {
 const cases = [
  ['2026-09-06_10-14-47_protocol-probe__anthropic--claude-opus-5', 'anthropic', false],
  ['2026-09-06_10-17-45_protocol-probe__google--gemma-4-31b-it', 'deepinfra/turbo', true],
 ];
 for (const [id, endpoint, omitted] of cases) {
  await page.goto(`http://localhost:3420/history/${id}`);
  await page.waitForLoadState('networkidle');
  expect(await page.getByText('Protocol test using a recorded screenshot. No emulator actions were executed.').isVisible(), 'Probe is clearly distinguished from gameplay');
  await page.getByRole('button', {name:/Turn 2 /}).click();
  const panel=page.locator('.turn.open details.diagnostics');
  expect(await panel.getAttribute('open') === null, 'Technical details collapsed');
  await panel.locator(':scope > summary').click();
  const text=await panel.innerText();
  expect(text.includes(endpoint) && text.includes('Pinned route'), 'Configured route displayed');
  expect(text.includes('Internal reasoning use'), 'Internal use shown separately from local replay');
  if (omitted) expect(text.includes('Omitted by model policy'), 'Gemma omission does not masquerade as preserved thinking');
  else expect(text.includes('blocks · intact'), 'Claude replay remains visible');
  expect(!text.includes('request_id'), 'Raw technical JSON remains collapsed');
  await panel.scrollIntoViewIfNeeded();
  await screenshot(omitted ? 'profile-gemma-policy' : 'profile-claude-cache');
 }
}
