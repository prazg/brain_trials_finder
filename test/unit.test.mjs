/**
 * Offline tests for docs/index.html. No network: ClinicalTrials.gov and ISRCTN
 * are answered from test/fixtures (trimmed copies of real responses) and the
 * snapshots come from docs/data.
 *
 *   npm run test:unit
 *
 * These cover the parsing and behaviour that the live suite cannot pin down,
 * because the registers' contents change from day to day: date and phase
 * formats, country spellings, off-topic hits, cancelled searches, failures.
 */
import assert from "node:assert/strict";
import { loadPage, abortable, read, wait } from "./harness.mjs";

const fixtures = {
  ctg1: read("test/fixtures/ctg_page1.json"),
  ctg2: read("test/fixtures/ctg_page2.json"),
  isrctn: read("test/fixtures/isrctn.xml"),
  ctis: read("docs/data/eu_ctis.json"),
  ictrp: read("docs/data/ictrp.json"),
};

const json = (body, status = 200) =>
  new Response(body, { status, headers: { "content-type": "application/json" } });

/** A fake network. `over` lets a test break or delay individual registers. */
function network(over = {}) {
  const calls = [];
  const fetch = abortable(async (url) => {
    calls.push(url);
    if (over.delay) await wait(over.delay);
    if (url.includes("clinicaltrials.gov")) {
      if (over.ctg) return over.ctg(url);
      return json(url.includes("pageToken=") ? fixtures.ctg2 : fixtures.ctg1);
    }
    if (url.includes("isrctn.com")) {
      if (over.isrctn) return over.isrctn(url);
      return new Response(fixtures.isrctn, { status: 200, headers: { "content-type": "application/xml" } });
    }
    if (url.endsWith("data/eu_ctis.json")) return over.ctis ? over.ctis(url) : json(fixtures.ctis);
    if (url.endsWith("data/ictrp.json")) return over.ictrp ? over.ictrp(url) : json(fixtures.ictrp);
    return new Response("", { status: 404 });
  });
  return { fetch, calls };
}

let passed = 0;
const failures = [];
async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log("  ok    " + name);
  } catch (e) {
    failures.push(name);
    console.log("  FAIL  " + name + "\n        " + String(e.message).split("\n").join("\n        "));
  }
}

console.log("\nPure helpers");
{
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  const w = page.w;

  await test("isoDate reads every format the registers send", () => {
    assert.equal(w.isoDate("2026-03-10T16:58:20.443Z"), "2026-03-10");
    assert.equal(w.isoDate("24/07/2026"), "2026-07-24");       // CTIS search rows
    assert.equal(w.isoDate("01-09-2026"), "2026-09-01");       // CTRI via ICTRP
    assert.equal(w.isoDate("16 March 2026"), "2026-03-16");    // ICTRP last updated
    assert.equal(w.isoDate("2025/06/13"), "2025-06-13");
    assert.equal(w.isoDate("2026-11"), "2026-11");             // ClinicalTrials.gov partial date
    assert.equal(w.isoDate("soon"), "");
    assert.equal(w.isoDate(""), "");
    assert.equal(w.isoDate("45/13/2026"), "");
    assert.equal(w.fmtDate("2026-03-16"), "16 Mar 2026");
    assert.equal(w.fmtDate("2026-11"), "Nov 2026");
  });

  await test("phaseNums understands each register's phase vocabulary", () => {
    const n = (...p) => [...w.phaseNums(p)].join(",");
    assert.equal(n("PHASE1", "PHASE2"), "1,2");                                // ClinicalTrials.gov
    assert.equal(n("EARLY_PHASE1"), "1");
    assert.equal(n("NA"), "");
    assert.equal(n("Phase I/II"), "1,2");                                      // ISRCTN
    assert.equal(n("Not Specified"), "");
    assert.equal(n("Therapeutic exploratory (Phase II)"), "2");                // CTIS
    assert.equal(n("Phase I and Phase II (Integrated)- Other"), "1,2");
    assert.equal(n("Phase III and phase IV (Integrated)"), "3,4");
    assert.equal(n("2"), "2");                                                 // ICTRP, bare digits
    assert.equal(n("1-2"), "1,2");
    assert.equal(n("Phase 2/ Phase 3"), "2,3");
    assert.equal(n("Phase I,II"), "1,2");
    assert.equal(n("I (Phase I study)"), "1");
    assert.equal(n("0"), "");
    assert.equal(n("N/A"), "");
    assert.equal(n("Retrospective study"), "");
    assert.equal(w.phaseLabel({ phases: ["2"] }), "Phase 2");
    assert.equal(w.phaseLabel({ phases: ["EARLY_PHASE1"] }), "Early phase 1");
    assert.equal(w.phaseLabel({ phases: ["N/A"] }), "");
    assert.equal(w.phaseLabel({ phases: ["Pilot study"] }), "Pilot study");
  });

  await test("normCountry folds every spelling seen in the data", () => {
    for (const c of ["England", "Scotland", "Wales", "Northern Ireland"]) assert.equal(w.normCountry(c), "United Kingdom");
    for (const c of ["USA", "U.S.A", "the US", "United States of America"]) assert.equal(w.normCountry(c), "United States");
    assert.equal(w.normCountry("Turkey (Türkiye)"), "Turkey");
    assert.equal(w.normCountry("Iran (Islamic Republic of)"), "Iran");
    assert.equal(w.normCountry("Czech Republic"), "Czechia");
    assert.equal(w.normCountry("Korea, Republic of"), "South Korea");
    assert.equal(w.normCountry("Australlia"), "Australia");
    assert.equal(w.normCountry("  Germany "), "Germany");
  });

  await test("parseEnrolment does not concatenate group sizes", () => {
    assert.equal(w.parseEnrolment("100"), 100);
    assert.equal(w.parseEnrolment(42), 42);
    assert.equal(w.parseEnrolment("Group 1:5;Group 2:5;Group 3:5;"), 15);   // was 152535
    assert.equal(w.parseEnrolment("Case series:180;"), 180);
    assert.equal(w.parseEnrolment(""), null);
    assert.equal(w.parseEnrolment("about fifty"), null);
  });

  await test("ctisAges gives a maximum as well as a minimum", () => {
    assert.deepEqual({ ...w.ctisAges("65+ years, 18-64 years") }, { min: 18, max: null });
    assert.deepEqual({ ...w.ctisAges("0-17 years") }, { min: 0, max: 17 });
    assert.deepEqual({ ...w.ctisAges("18-64 years") }, { min: 18, max: 64 });
    assert.deepEqual({ ...w.ctisAges("0-17 years, 18-64 years") }, { min: 0, max: 64 });
    assert.deepEqual({ ...w.ctisAges("") }, { min: null, max: null });
  });

  await test("isrctnStatus: overrides, windows and study end", () => {
    const s = (...a) => w.isrctnStatus(...a);
    assert.equal(s("Stopped", "2021-03-31", "2024-03-30", "", "2026-11-10").recruiting, false);
    assert.equal(s("No longer recruiting", "2021-03-31", "2030-01-01", "", "2026-11-10").recruiting, false);
    assert.equal(s("Recruiting", "", "", "", "2026-11-10").tone, "open");
    assert.equal(s("", "2026-02-13", "2029-02-13", "", "2026-11-10").tone, "open");
    assert.equal(s("", "2027-01-01", "2029-01-01", "", "2026-11-10").tone, "soon");
    assert.equal(s("", "2025-12-01", "2026-04-30", "", "2026-11-10").recruiting, false);
    assert.equal(s("", "2020-01-01", "", "2024-01-01", "2026-11-10").recruiting, false);   // study over
    assert.equal(s("", "", "", "", "2026-11-10").tone, "unclear");
    assert.equal(s("", "2026-02-13", "", "", "2026-11-10").derived, true);
  });

  await test("ictrpRecruiting reads negatives before positives", () => {
    assert.equal(w.ictrpRecruiting("Recruiting"), true);
    assert.equal(w.ictrpRecruiting("Pending"), true);
    assert.equal(w.ictrpRecruiting("Open to Recruitment"), true);
    assert.equal(w.ictrpRecruiting("Not Yet Recruiting"), true);
    assert.equal(w.ictrpRecruiting("Recruiting stopped after recruiting started"), false);  // was true
    assert.equal(w.ictrpRecruiting("Not Recruiting"), false);
    assert.equal(w.ictrpRecruiting("Complete: follow-up continuing"), false);
    assert.equal(w.ictrpRecruiting("Active, not recruiting"), false);
    assert.equal(w.ictrpRecruiting(""), false);
  });

  await test("performance-status phrases are parsed, not just spotted", () => {
    assert.equal(w.ecogMax("ECOG performance status 0-1"), 1);
    assert.equal(w.ecogMax("ECOG performance status of 0, 1 or 2 at screening"), 2);
    assert.equal(w.ecogMax("ECOG PS ≤ 2"), 2);
    assert.equal(w.ecogMax("ECOG status documented"), null);
    assert.equal(w.kpsMin("Karnofsky performance status ≥ 70"), 70);
    assert.equal(w.kpsMin("KPS of at least 60%"), 60);
    assert.equal(w.kpsMin("Karnofsky score recorded"), null);
    assert.equal(w.kpsToEcog(90), 1);
    assert.equal(w.kpsToEcog(70), 2);
  });

  await test("cross-register identifiers are extracted and compared", () => {
    assert.deepEqual([...w.crossIds("NCT05629702 2021-005214-34")], ["NCT05629702", "2021-005214-34"]);
    assert.deepEqual([...w.crossIds("CA127-1029, TPX-0005-07, 2023-506464-14")], ["2023-506464-14"]);
    assert.deepEqual([...w.crossIds("IRAS ID: 1012556")], []);
    assert.equal(w.idKey("2023-506464-14-00"), w.idKey("2023-506464-14"));
    assert.deepEqual([...w.acronymsOf({ acronym: "5G-Master and 5G-PEARL", title: "x" })], ["5G-MASTER", "5G-PEARL"]);
  });

  await test("CSV cells cannot start a spreadsheet formula", () => {
    assert.equal(w.csvCell("=HYPERLINK(\"http://x\")"), "\"'=HYPERLINK(\"\"http://x\"\")\"");
    assert.equal(w.csvCell("+44 20"), "'+44 20");
    assert.equal(w.csvCell("plain"), "plain");
    assert.equal(w.csvCell("a,b"), '"a,b"');
  });

  await test("safeUrl keeps web links only", () => {
    assert.equal(w.safeUrl("javascript:alert(1)"), "");
    assert.equal(w.safeUrl("https://clinicaltrials.gov/study/NCT01"), "https://clinicaltrials.gov/study/NCT01");
  });

  page.close();
}

console.log("\nPage behaviour (fixtures)");

await test("default search renders all four registers with no errors", async () => {
  const page = loadPage({ fetch: network().fetch });
  assert.ok(await page.settle(), "search did not finish");
  const s = page.snapshot();
  assert.deepEqual(s.errors, []);
  assert.equal(s.sources.length, 4);
  assert.ok(s.by.CTG >= 4, "expected the UK glioblastoma fixture trials, got " + s.by.CTG);
  assert.equal(s.blankTitles + s.missingIds + s.garbage, 0, "blank or malformed card text");
  assert.equal(page.errors.length, 0, page.errors.join("; "));
  page.close();
});

await test("anti-GBM kidney trials are hidden, counted, and can be shown", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  await page.search({ region: "any", interventional: false, onlyRecruiting: false });
  let s = page.snapshot();
  assert.ok(!s.ids.includes("NCT07018024"), "anti-GBM disease trial should not be listed");
  assert.match(s.hidden, /only in passing/);
  page.d.getElementById("offBtn").click();
  s = page.snapshot();
  assert.ok(s.ids.includes("NCT07018024"), "…but must be reachable on request");
  assert.match(page.d.getElementById("t-NCT07018024").textContent, /Probably unrelated/);
  page.close();
});

await test("ISRCTN: stopped and expired windows are not shown as open", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  const s = page.snapshot();
  assert.ok(s.ids.includes("ISRCTN88772403"), "the open 5G platform trial should be listed");
  assert.ok(!s.ids.includes("ISRCTN14920547"), "override 'Stopped' must not be listed as recruiting");
  assert.ok(!s.ids.includes("ISRCTN82678458"), "recruitment window ended in April 2026");
  page.close();
});

await test("the same study in two registers is linked", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  const isrctn = page.d.getElementById("t-ISRCTN88772403").textContent;
  assert.match(isrctn, /Possibly the same study as\s+NCT07391215/);
  // NCT04094610 quotes EU CT number 2023-506464-14, which the CTIS snapshot holds.
  const ctg = page.d.getElementById("t-NCT04094610");
  if (fixtures.ctis.includes("2023-506464-14")) assert.match(ctg.textContent, /Also registered as\s+2023-506464-14/);
  page.close();
});

await test("load more keeps the other registers' chips and notes", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  const before = page.snapshot();
  const more = page.d.getElementById("moreBtn");
  assert.equal(more.hidden, false, "fixture has a second page");
  more.click();
  await page.settle();
  const after = page.snapshot();
  assert.equal(after.sources.length, 4, "source chips vanished after Load more: " + after.sources.join(" | "));
  assert.equal(after.notices.length, before.notices.length, "notes vanished after Load more");
  assert.equal(after.by.CTG, before.by.CTG + 1);
  assert.equal(after.by.ISRCTN, before.by.ISRCTN);
  page.close();
});

await test("keywords narrow every register, not just ClinicalTrials.gov", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  await page.search({ region: "any", kw: "zzznotaword" });
  const s = page.snapshot();
  assert.equal(s.by.ISRCTN + s.by.CTIS + s.by.ICTRP, 0, JSON.stringify(s.by));
  assert.match(s.hidden, /do not mention your keywords/);
  page.close();
});

await test("a search started during another replaces it", async () => {
  const net = network({ delay: 120 });
  const page = loadPage({ fetch: net.fetch });
  // Do not wait: change region twice while the first search is in flight.
  const region = page.d.getElementById("region");
  region.value = "eu";
  region.dispatchEvent(new page.w.Event("change"));
  await wait(20);
  region.value = "us";
  region.dispatchEvent(new page.w.Event("change"));
  assert.ok(await page.settle(), "did not settle");
  const s = page.snapshot();
  assert.match(s.count, /United States/, "results are labelled for the last region chosen: " + s.count);
  assert.equal(page.w.eval("state").intake.region, "us");
  assert.equal(s.sources.filter((t) => /loading/.test(t)).length, 0);
  page.close();
});

await test("one register failing degrades only that register", async () => {
  const page = loadPage({ fetch: network({ ctg: () => json("{}", 400) }).fetch });
  await page.settle();
  const s = page.snapshot();
  assert.equal(s.errors.length, 1);
  assert.match(s.errors[0], /ClinicalTrials\.gov unavailable.*HTTP 400.*Retry/);
  assert.ok(s.by.ISRCTN + s.by.CTIS + s.by.ICTRP > 0 || /No matches/.test(s.count));
  page.close();
});

await test("a 5xx is retried once before giving up", async () => {
  let n = 0;
  const page = loadPage({ fetch: network({ ctg: () => (++n === 1 ? json("{}", 503) : json(fixtures.ctg1)) }).fetch });
  await page.settle();
  assert.equal(n, 2);
  assert.deepEqual(page.snapshot().errors, []);
  page.close();
});

await test("everything failing says so instead of 'nothing matched'", async () => {
  const down = () => { throw new TypeError("Failed to fetch"); };
  const page = loadPage({ fetch: network({ ctg: down, isrctn: down, ctis: down, ictrp: down }).fetch });
  await page.settle();
  const text = page.d.getElementById("results").textContent;
  assert.match(text, /None of the registers could be reached/);
  assert.equal(page.d.getElementById("csvBtn").disabled, true);
  page.close();
});

await test("malformed register responses are reported, not thrown", async () => {
  const page = loadPage({
    fetch: network({
      ctg: () => new Response("<html>maintenance</html>", { status: 200 }),
      isrctn: () => new Response("<allTrials><oops", { status: 200 }),
    }).fetch,
  });
  await page.settle();
  const s = page.snapshot();
  assert.equal(s.errors.length, 2, s.errors.join(" | "));
  assert.equal(page.errors.length, 0, page.errors.join("; "));
  page.close();
});

await test("an old snapshot is flagged as old", async () => {
  const old = JSON.parse(fixtures.ctis);
  old.generated = new Date(Date.now() - 60 * 86400000).toISOString();
  const page = loadPage({ fetch: network({ ctis: () => json(JSON.stringify(old)) }).fetch });
  await page.settle();
  const s = page.snapshot();
  assert.ok(s.sources.some((t) => /EU CTIS.*60 days old/.test(t)), s.sources.join(" | "));
  assert.ok(s.notices.some((t) => /60 days old/.test(t)));
  assert.ok(page.d.querySelector(".src.warn"));
  page.close();
});

await test("snapshot date formats sort and display consistently", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  await page.search({ region: "any" });
  for (const t of page.w.eval("state").trials) {
    assert.match(t.updated, /^(\d{4}-\d{2}(-\d{2})?)?$/, t.id + " updated=" + t.updated);
    if (t.enrollment != null) assert.ok(t.enrollment < 100000, t.id + " enrolment " + t.enrollment);
  }
  const shown = [...page.d.querySelectorAll(".meta")].map((m) => m.textContent).join(" ");
  assert.doesNotMatch(shown, /Updated \d{2}\/\d{2}\/\d{4}/);
  page.close();
});

await test("phase filter is strict and explains what it hid", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  page.set("phase", "3");
  page.d.getElementById("phase").dispatchEvent(new page.w.Event("change"));
  await wait(300);
  for (const t of page.w.visible()) assert.ok(page.w.phaseNums(t.phases).includes(3), t.id + " " + t.phases);
  assert.match(page.snapshot().hidden, /a different phase|no stated phase/);
  page.close();
});

await test("patient fields re-rank at once without another request", async () => {
  const net = network();
  const page = loadPage({ fetch: net.fetch });
  await page.settle();
  const calls = net.calls.length;
  page.set("age", "4");
  page.d.getElementById("age").dispatchEvent(new page.w.Event("input"));
  await wait(300);
  assert.equal(net.calls.length, calls, "re-scoring must not refetch");
  assert.match(page.d.getElementById("results").textContent, /Minimum age 18/);
  page.close();
});

await test("the search is kept in the URL fragment and restored from it", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  await page.search({ dx: "Meningioma", region: "any" });
  assert.match(page.w.location.hash, /dx=Meningioma/);
  assert.match(page.w.location.hash, /region=any/);
  assert.equal(page.w.location.search, "", "patient details must not go in the query string");
  page.close();

  const again = loadPage({ fetch: network().fetch, hash: "#dx=Meningioma&region=any&age=71&bev=1" });
  await again.settle();
  assert.equal(again.d.getElementById("dx").value, "Meningioma");
  assert.equal(again.d.getElementById("age").value, "71");
  assert.equal(again.d.getElementById("bev").checked, true);
  assert.match(again.snapshot().count, /Meningioma · Anywhere/);
  again.close();
});

await test("hostile values in the fragment are ignored", async () => {
  const page = loadPage({ fetch: network().fetch, hash: "#dx=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E&region=mars&sort=evil" });
  await page.settle();
  assert.equal(page.d.getElementById("dx").value, "Glioblastoma");
  assert.equal(page.d.getElementById("region").value, "uk");
  assert.equal(page.d.querySelectorAll("img").length, 0);
  page.close();
});

await test("eligibility text is filled in when opened, and escaped", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  const det = page.d.querySelector("#t-NCT06630260 details.elig");
  assert.ok(det, "card should offer the eligibility text");
  assert.equal(det.querySelector(".elig-body").textContent, "");
  page.w.fillEligibility(det);
  assert.match(det.querySelector(".elig-body").textContent, /Inclusion|Criteria/i);
  assert.equal(det.querySelectorAll(".elig-body script").length, 0);
  page.close();
});

await test("CSV export carries a BOM, the header and one row per card", async () => {
  const page = loadPage({ fetch: network().fetch });
  await page.settle();
  const rows = page.w.sorted(page.w.visible());
  const csv = page.w.buildCSV(rows);
  assert.equal(csv.charCodeAt(0), 0xfeff);
  const lines = csv.slice(1).split("\r\n");
  assert.match(lines[0], /^register,id,title,status,status_basis,/);
  assert.ok(lines.length >= rows.length + 1);
  page.d.getElementById("csvBtn").click();   // must not throw
  page.close();
});

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) console.log("  - " + failures.join("\n  - "));
process.exit(failures.length ? 1 : 0);
