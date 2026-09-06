export default async function ({page, expect, screenshot}) {
 const url = 'http://localhost:3420/history/2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3';
 await page.goto(url);
 await page.waitForLoadState('networkidle');
 const panel = page.locator('.cache-overview');
 expect(await panel.getAttribute('open') === null, 'Cache overview collapsed by default');
 await panel.locator(':scope > summary').click();
 const values = await panel.locator('.cache-metrics strong').allTextContents();
 expect(JSON.stringify(values) === JSON.stringify(['52.6%','6 / 7','7 / 7']), 'Token reuse, request hits, and coverage remain distinct');
 expect(await panel.locator('tbody tr').count() === 5, 'All five phase/provider/segment groups visible');
 expect((await panel.locator('tbody').innerText()).includes('69.3%'), 'Compaction cache rate preserved in table');
 expect(!(await panel.innerText()).includes('input_read_fraction'), 'Raw JSON is hidden');
 await screenshot('cache-overview-readable');
 await panel.locator('.cache-raw > summary').click();
 expect(JSON.parse(await panel.locator('.cache-raw pre').innerText()).total.cached_tokens === 18301, 'Raw totals remain available');
 // Missing measurements must not appear as a real zero cache hit rate.
 await page.route('**/api/runs/*/trace', async route => {
  const response = await route.fetch(); const data = await response.json();
  data.cache = {...data.cache, measured_attempts:0, cached_tokens:0, measured_input_tokens:0, input_read_fraction:null, request_hit_fraction:null};
  data.cache_breakdown = {};
  await route.fulfill({response,json:data});
 });
 await page.reload(); await page.waitForLoadState('networkidle');
 await page.locator('.cache-overview > summary').click();
 expect(JSON.stringify(await page.locator('.cache-metrics strong').allTextContents()) === JSON.stringify(['Not reported','Not reported','0 / 7']), 'Missing cache measurements remain unknown, not zero percent');
 await page.unroute('**/api/runs/*/trace');
 await page.reload();
}
