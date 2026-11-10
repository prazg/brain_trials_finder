/**
 * Integration test for docs/index.html.
 *
 * Loads the real page in a real DOM and runs real searches. ClinicalTrials.gov
 * and ISRCTN are hit over the network — that is deliberate. The whole point is
 * to catch the case where a register quietly changes a field name and the page
 * starts rendering blanks without erroring. Snapshots are served from disk.
 *
 *   npm install
 *   npm test                      # default scenarios
 *   npm test -- --quick           # one scenario, for a fast loop
 *
 * Exit code is non-zero if any scenario reports a failed source, a blank title,
 * a missing trial id, or zero results where results are expected.
 */

import { JSDOM, VirtualConsole } from "jsdom";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
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
    ];

const snapshots = {};
for (const name of ["eu_ctis.json", "ictrp.json"]) {
  const p = path.join(ROOT, "docs/data", name);
  if (fs.existsSync(p)) snapshots[name] = fs.readFileSync(p, "utf8");
  else console.warn(`  warning: docs/data/${name} missing — that source will report unavailable`);
}

const realFetch = globalThis.fetch;
async function routedFetch(input, init) {
  const url = String(input);
  for (const [name, body] of Object.entries(snapshots)) {
    if (url.endsWith("data/" + name) || url.endsWith("/" + name)) {
      return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
    }
  }
  if (/\.json(\?|$)/.test(url)) return new Response("", { status: 404 });
  const r = await realFetch(url, init);
  return new Response(await r.text(), {
    status: r.status,
    headers: { "content-type": r.headers.get("content-type") || "text/plain" },
  });
}

const vc = new VirtualConsole();
vc.on("jsdomError", (e) => {
  // jsdom does not implement navigation or object URLs; those are not our bugs.
  if (!/Not implemented|navigation/i.test(e.message)) console.error("  jsdom:", e.message);
});

const dom = new JSDOM(fs.readFileSync(path.join(ROOT, "docs/index.html"), "utf8"), {
  runScripts: "dangerously",
  url: "https://example.github.io/brain_trials_finder/",
  virtualConsole: vc,
  pretendToBeVisual: true,
  beforeParse(w) {
    w.Response = Response;
    w.Blob = Blob;
    w.URL.createObjectURL = () => "blob:test";
    w.URL.revokeObjectURL = () => {};
    w.fetch = (...a) => routedFetch(...a);
  },
});

const d = dom.window.document;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function settle(timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await wait(400);
    if (d.getElementById("goBtn").textContent === "Search") return true;
  }
  return false;
}

async function search(dx, region) {
  d.getElementById("dx").value = dx;
  d.getElementById("region").value = region;
  d.getElementById("q").dispatchEvent(new dom.window.Event("submit"));
  const ok = await settle();
  const cards = [...d.querySelectorAll(".trial")];
  const by = { CTG: 0, ISRCTN: 0, CTIS: 0, ICTRP: 0 };
  for (const c of cards) by[c.className.split("r-")[1]]++;
  return {
    settled: ok,
    cards,
    by,
    errors: [...d.querySelectorAll(".src.err")].map((e) => e.textContent.trim()),
    blankTitles: cards.filter((c) => !c.querySelector("h3").textContent.trim()).length,
    missingIds: cards.filter((c) => !c.querySelector(".id").textContent.trim()).length,
  };
}

const failures = [];
await settle();

console.log("\ndx / region                          total  CTG ISRCTN CTIS ICTRP  status");
console.log("-".repeat(80));

for (const [dx, region, minimum] of SCENARIOS) {
  const r = await search(dx, region);
  const notes = [];
  if (!r.settled) notes.push("TIMED OUT");
  if (r.errors.length) notes.push("SOURCE ERROR");
  if (r.blankTitles) notes.push(`${r.blankTitles} BLANK TITLES`);
  if (r.missingIds) notes.push(`${r.missingIds} MISSING IDS`);
  if (r.cards.length < minimum) notes.push(`BELOW MINIMUM (${minimum})`);

  console.log(
    `${(dx + " / " + region).padEnd(36)}${String(r.cards.length).padStart(5)}` +
      `${String(r.by.CTG).padStart(5)}${String(r.by.ISRCTN).padStart(7)}` +
      `${String(r.by.CTIS).padStart(5)}${String(r.by.ICTRP).padStart(6)}  ` +
      (notes.length ? notes.join(", ") : "ok")
  );
  for (const e of r.errors) console.log("      " + e);
  if (notes.length) failures.push(`${dx}/${region}: ${notes.join(", ")}`);
}

// CSV export must not throw.
try {
  d.getElementById("csvBtn").dispatchEvent(new dom.window.Event("click"));
  console.log("\nCSV export: ok");
} catch (e) {
  console.log("\nCSV export: FAILED — " + e.message);
  failures.push("CSV export threw: " + e.message);
}

console.log(
  failures.length
    ? `\n${failures.length} failure(s):\n  - ` + failures.join("\n  - ")
    : "\nAll scenarios passed."
);

await wait(300);
dom.window.close();
process.exit(failures.length ? 1 : 0);
