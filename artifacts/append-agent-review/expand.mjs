export default async function ({page, screenshot, log}) {
 await page.goto('http://localhost:3420/history/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3');
 await page.waitForLoadState('networkidle');
 await page.getByRole('button', {name: /Turn 5 /}).click();
 await page.getByText('Conversation, cache & compaction').scrollIntoViewIfNeeded();
 await screenshot('02-turn-five');
 log(await page.locator('.diagnostics').innerText());
}
