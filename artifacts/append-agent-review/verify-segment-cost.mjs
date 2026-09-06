export default async function ({page, expect, screenshot}) {
 const id='2026-09-05_23-47-09_append-smoke__gpt-5-6-sol-medium_continued_from_turn_3';
 await page.goto(`http://localhost:3420/history/${id}`);
 await page.waitForLoadState('networkidle');
 const response=await page.request.get(`http://localhost:3420/api/runs/${id}/trace`);
 const data=await response.json();
 await page.locator('.cache-overview > summary').click();
 expect(await page.getByRole('columnheader', {name:'Segment total cost'}).count() === 1, 'Segment cost column present');
 const cells=page.locator('.segment-cost');
 expect(await cells.count() === 3, 'One total per segment, shared across gameplay and compaction');
 for(let i=0;i<3;i++) expect(await cells.nth(i).innerText() === `$${data.segment_costs[String(i+1)].total_cost_usd.toFixed(6)}`, `Segment ${i+1} matches request cost aggregation`);
 expect(await cells.first().getAttribute('rowspan') === '2', 'Segment one total spans both request types');
 expect(Math.abs(data.segment_costs['1'].total_cost_usd - .0283225) < .00000001, 'First segment includes both gameplay requests and compaction');
 await screenshot('segment-total-cost');
}
