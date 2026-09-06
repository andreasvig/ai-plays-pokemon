export default async function ({page, expect, screenshot}) {
 await page.goto('http://localhost:3420/history/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3');
 await page.waitForLoadState('networkidle');
 for (const turn of [1,2]) {
  const button = page.getByRole('button', {name:new RegExp(`Turn ${turn} `)});
  await button.click();
  const panel = page.locator('.turn.open details.diagnostics');
  expect(await panel.getAttribute('open') === null, 'Technical details stays collapsed by default');
  await panel.locator(':scope > summary').click();
  for (const heading of ['Cost & speed','Cache reuse','Conversation continuity']) expect(await panel.getByRole('heading', {name:heading}).isVisible(), `${heading} is readable without JSON`);
  const text = await panel.innerText();
  expect(!text.includes('not_reported') && !text.includes('request_id'), 'Raw field names and IDs hidden');
  if(turn === 1) {
   expect(text.includes('No earlier reasoning blocks'), 'Fresh turn does not imply a successful reasoning replay');
   expect(text.includes('$0.009218') && text.includes('4.76 s') && text.includes('0.0%'), 'First request cost, latency, and zero cache read match event');
  } else expect(text.includes('58.5%') && text.includes('2,808') && text.includes('3 / 3 blocks'), 'Next turn shows measured cache reuse and replay');
  await panel.scrollIntoViewIfNeeded();
  await screenshot(`readable-metrics-turn-${turn}`);
  await panel.locator('.raw-event > summary').click();
  expect(JSON.parse(await panel.locator('.raw-event pre').innerText()).turn === turn, 'Raw source event remains available');
  await button.click();
 }
}
