export default async function ({page, screenshot, expect}) {
 await page.goto('http://localhost:3420/history/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3');
 await page.waitForLoadState('networkidle');
 expect(await page.locator('.thead').count() === 5, 'All five gameplay turns are present');
 for (const turn of [1,2,3,4,5]) {
  const button = page.getByRole('button', {name: new RegExp(`Turn ${turn} `)});
  await button.click();
  const body = page.locator('.turn.open .tbody');
  const start = [1,3,5].includes(turn);
  expect(await body.locator('.trace-system').count() === (start ? 1 : 0), `Turn ${turn}: system prompt only at segment start`);
  expect(await body.locator('summary').filter({hasText: /^Conversation context$/}).count() === (start ? 1 : 0), `Turn ${turn}: context only at segment start`);
  const input = body.locator('.trace-input').filter({has: page.locator('.step-label', {hasText: /^Input$/})});
  await input.locator('summary').click();
  const text = await input.locator('pre').innerText();
  expect(!text.includes('Earlier conversation') && !text.includes('Top goal:') && !text.includes('Memory from the last handover:'), `Turn ${turn}: Input contains only current observation`);
  if (turn === 2 || turn === 3) {
   if (start) await body.locator('summary').filter({hasText: /^Conversation context$/}).click();
   await body.scrollIntoViewIfNeeded();
   await screenshot(`conversation-turn-${turn}`);
  }
  await button.click();
 }
}
