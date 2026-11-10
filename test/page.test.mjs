/**
 * Integration test for docs/index.html.
 *
 * Loads the real page in a real DOM and runs real searches. ClinicalTrials.gov
 * and ISRCTN are hit over the network — that is deliberate. The whole point is
 * to catch the case where a register quietly changes a field name and the page
 * starts rendering blanks without erroring. Snapshots are served from disk.
 *
 *   npm install
 *   npm run test:live                  # default scenarios
 *   npm run test:live -- --quick       # one scenario, for a fast loop
 *
 * Exit code is non-zero if any scenario reports a failed source, a blank title,
 * a missing trial id, malformed card text, or fewer results than its floor.
 * The offline behaviour tests live in unit.test.mjs.
 */

import fs from "node:fs";
import path from "node:path";
import { ROOT, loadPage, wait } from "./harness.mjs";

const quick = process.argv.includes("--quick");

// dx, region, minimum results we expect to see
const SCENARIOS = quick
  ? [["Glioblastoma", "uk", 5]]
  : [
      ["Glioblastoma",       "uk",      5],
      ["Glioblastoma",       "any",    40],
      ["Glioblastoma",       "eu",     10],
      ["Glioblastoma",       "us",     20],
      ["Glioblastoma",       "india",   1],
      ["Glioma (any grade)", "china",   3],
      ["Medulloblastoma",    "any",     5],
      ["Meningioma",         "any",     5],
      ["Brain metastases",   "uk_eu",   5],
      ["Any CNS tumour",     "india",   1],
      ["Any CNS tumour",     "asiapac", 10],
    ];

const snapshots = {};
for (const name of ["eu_ctis.json", "ictrp.json"]) {
  const p = path.join(ROOT, "docs/data", name);
  if (fs.existsSync(p)) snapshots[name] = fs.readFileSync(p, "utf8");
  else console.warn(`  warning: docs/data/${name} missing — that source will report unavailable`);
}

const realFetch = globalThis.fetch;
function routedFetch(input, init = {}) {
  const url = String(input);
  for (const [name, body] of Object.entries(snapshots)) {
    if (url.endsWith("data/" + name) || url.endsWith("/" + name)) {
      return Promise.resolve(new Response(body, { status: 200, headers: { "content-type": "application/json" } }));
    }
  }
  if (/\.json(\?|$)/.test(url)) return Promise.resolve(new Response("", { status: 404 }));
  // The page hands over a jsdom AbortSignal, which Node's fetch will not
  // accept, so bridge it to a native controller.
  const ctl = new AbortController();
  init.signal?.addEventListener("abort", () => ctl.abort());
  return realFetch(url, { signal: ctl.signal }).then(async (r) =>
    new Response(await r.text(), {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") || "text/plain" },
    }));
}

const page = loadPage({ fetch: routedFetch });
const failures = [];
await page.settle(90000);

console.log("\ndx / region                          total  CTG ISRCTN CTIS ICTRP  status");
console.log("-".repeat(80));

for (const [dx, region, minimum] of SCENARIOS) {
  const settled = await page.search({ dx, region });
  const r = page.snapshot();
  const notes = [];
  if (!settled) notes.push("TIMED OUT");
  if (r.errors.length) notes.push("SOURCE ERROR");
  if (r.blankTitles) notes.push(`${r.blankTitles} BLANK TITLES`);
  if (r.missingIds) notes.push(`${r.missingIds} MISSING IDS`);
  if (r.garbage) notes.push(`${r.garbage} MALFORMED CARDS`);
  if (r.cards.length < minimum) notes.push(`BELOW MINIMUM (${minimum})`);

  console.log(
    `${(dx + " / " + region).padEnd(36)}${String(r.cards.length).padStart(5)}` +
      `${String(r.by.CTG).padStart(5)}${String(r.by.ISRCTN).padStart(7)}` +
      `${String(r.by.CTIS).padStart(5)}${String(r.by.ICTRP).padStart(6)}  ` +
      (notes.length ? notes.join(", ") : "ok")
  );
  for (const e of r.errors) console.log("      " + e);
  if (r.hidden) console.log("      " + r.hidden.replace(/\s+/g, " ").slice(0, 150));
  if (notes.length) failures.push(`${dx}/${region}: ${notes.join(", ")}`);
}

// CSV export must not throw.
try {
  page.d.getElementById("csvBtn").dispatchEvent(new page.w.Event("click"));
  console.log("\nCSV export: ok");
} catch (e) {
  console.log("\nCSV export: FAILED — " + e.message);
  failures.push("CSV export threw: " + e.message);
}
if (page.errors.length) failures.push("script errors: " + page.errors.join("; "));

console.log(
  failures.length
    ? `\n${failures.length} failure(s):\n  - ` + failures.join("\n  - ")
    : "\nAll scenarios passed."
);

await wait(300);
page.close();
process.exit(failures.length ? 1 : 0);
