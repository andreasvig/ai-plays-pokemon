export default async function ({page, expect, screenshot}) {
 for (const [name, time, reasoning] of [
  ['gemma-guidance','10-38-33','Omitted by model policy'],
  ['gemma-replay','10-38-34','2 / 2 blocks'],
 ]) {
  const id=`2026-09-06_${time}_protocol-probe__google--gemma-4-31b-it__${name}`;
  await page.goto(`http://localhost:3420/history/${id}`);
  await page.waitForLoadState('networkidle');
  expect(await page.getByText('Protocol test using a recorded screenshot. No emulator actions were executed.').isVisible(), 'Recorded-screen test clearly labeled');
  await page.getByRole('button',{name:/Turn 2 /}).click();
  const panel=page.locator('.turn.open details.diagnostics');
  await panel.locator(':scope > summary').click();
  const text=await panel.innerText();
  expect(text.includes(name), 'Selected Gemma profile visible');
  expect(text.includes(reasoning), 'Correct raw-thinking replay policy visible');
  expect(text.includes('deepinfra/turbo'), 'Same serving endpoint');
  await panel.scrollIntoViewIfNeeded();
  await screenshot(name);
 }
}
