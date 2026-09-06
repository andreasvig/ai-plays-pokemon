export default async function ({page, expect, screenshot}) {
 await page.goto('http://localhost:3420/history/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3');
 await page.waitForLoadState('networkidle');
 expect(await page.locator('.thead').count() === 7, 'Five gameplay rows and two compactions preserved');
 for (const number of [1,2]) {
  const button = page.getByRole('button', {name:new RegExp(`Compaction ${number} `)});
  await button.click();
  const body = page.locator('.compaction.open');
  expect(await body.locator('.trace-input').count() === 1, `Compaction ${number} has a normal Input card`);
  expect(await body.locator('.trace-output').count() === 1, `Compaction ${number} has one normal Output card`);
  await body.locator('.trace-input > summary').click();
  const input = await body.locator('.trace-input > pre').innerText();
  expect(input.includes('prepare a handover for yourself') && !input.includes('Top goal:'), `Compaction ${number} input is the new instruction, not repeated history`);
  await body.locator('.structured-output > summary').click();
  const output = JSON.parse(await body.locator('.structured-output > pre').innerText());
  expect(typeof output.continuation_summary === 'string' && typeof output.memory === 'object', `Compaction ${number} output is parseable summary and memory`);
  expect(!(await body.locator('.diagnostics').innerText()).includes('Compaction prompt and response'), 'No duplicate raw trace panel');
  if(number === 1) {
   await body.locator('.trace-section').scrollIntoViewIfNeeded();
   await screenshot('compaction-structured-output');
  }
  await button.click();
 }
}
