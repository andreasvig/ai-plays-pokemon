export default async function ({page, screenshot, expect}) {
 await page.goto('http://localhost:3420/history/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3');
 await page.waitForLoadState('networkidle');
 expect(await page.locator('.thead').count() === 5, 'Five game turns; compaction adds no game turn');
 expect((await page.getByRole('button', {name: /Turn 2 /}).innerText()).includes('b down down left left up'), 'Turn two shows its actual action, not replayed turn one');
 await page.getByRole('button', {name: /Turn 5 /}).click();
 const diagnostics = page.locator('.diagnostics');
 expect(await diagnostics.count() === 1, 'Expanded turn has one diagnostics panel');
 expect((await diagnostics.innerText()).includes('69.3%'), 'Compaction request shows measured cache reads');
 await diagnostics.locator('summary').filter({hasText:'Handover after turn 4'}).click();
 expect(await diagnostics.getByRole('heading', {name:'Memory before'}).isVisible(), 'Handover exposes previous memory');
 expect(await diagnostics.getByRole('heading', {name:'Memory after'}).isVisible(), 'Handover exposes replacement memory');
 await diagnostics.scrollIntoViewIfNeeded();
 await screenshot('03-handover');
 // Negative control: turn one has no compaction, rather than finding a panel elsewhere.
 await page.getByRole('button', {name: /Turn 5 /}).click();
 await page.getByRole('button', {name: /Turn 1 /}).click();
 expect(!((await page.locator('.diagnostics').innerText()).includes('Handover after')), 'First turn correctly has no compaction event');
 await screenshot('04-first-turn');
}
