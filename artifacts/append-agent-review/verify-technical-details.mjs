export default async function ({page, expect, screenshot}) {
 await page.goto('http://localhost:3420/history/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3');
 await page.waitForLoadState('networkidle');
 for (const label of [/Turn 2 /, /Compaction 1 /]) {
  const button = page.getByRole('button', {name:label});
  await button.click();
  const body = page.locator('.turn.open .tbody');
  const technical = body.locator('details.diagnostics');
  expect(await technical.count() === 1 && await technical.getAttribute('open') === null, 'Technical details is collapsed by default');
  expect(!(await body.innerText()).includes('Reasoning replay:'), 'Replay metadata hidden when collapsed');
  expect(!(await body.innerText()).includes('Trace (0 tool calls)') && !(await body.innerText()).includes('Conversation continues'), 'Repetitive trace filler removed');
  expect(await body.locator('.trace-input').count() === 1 && await body.locator('.trace-output').count() === 1, 'Input and output cards retained');
  await screenshot(label.source.includes('Turn') ? 'technical-collapsed-gameplay' : 'technical-collapsed-compaction');
  await technical.locator(':scope > summary').click();
  expect((await technical.innerText()).includes('Reasoning replay:') && (await technical.innerText()).includes('Cache:'), 'Technical metadata accessible on expand');
  await technical.locator('.diagnostics-body > details > summary').last().click();
  expect(await technical.locator('pre').last().isVisible(), 'Raw diagnostic JSON still expandable');
  await button.click();
 }
}
