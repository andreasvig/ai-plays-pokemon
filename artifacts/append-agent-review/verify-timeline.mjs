export default async function ({page, expect, screenshot}) {
 await page.goto('http://localhost:3420/history/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3');
 await page.waitForLoadState('networkidle');
 const labels = await page.locator('.thead .tn').allTextContents();
 expect(JSON.stringify(labels) === JSON.stringify(['Turn 1 (fresh)','Turn 2','Compaction 1','Turn 3 (fresh)','Turn 4','Compaction 2','Turn 5 (fresh)']), 'Exact chronology: five game turns and two independently numbered compactions');
 expect((await page.locator('.turn-by-turn, section').allTextContents()).join('').includes('(5 turns)'), 'Gameplay count remains five');
 await screenshot('timeline-collapsed');
 await page.getByRole('button', {name: /Compaction 1 /}).click();
 const compaction = page.locator('.compaction.open');
 expect((await compaction.innerText()).includes('66.9%'), 'Compaction has its own cache measurements');
 await compaction.locator('summary').filter({hasText:'Handover after turn 2'}).click();
 expect(await compaction.getByRole('heading', {name:'Memory after'}).isVisible(), 'Compaction row exposes its memory handover');
 await screenshot('timeline-compaction');
 await page.getByRole('button', {name: /Turn 3 \(fresh\)/}).click();
 const gameplay = page.locator('.turn.open:not(.compaction)');
 expect(!(await gameplay.locator('.diagnostics').innerText()).includes('Handover after'), 'Fresh gameplay does not duplicate compaction diagnostics');
 expect(await gameplay.locator('.trace-system').count() === 1, 'Fresh turn retains its system prompt');
}
