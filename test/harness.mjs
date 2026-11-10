/**
 * Shared helpers for the two test files. Loads docs/index.html in jsdom with
 * a fetch you supply, so the same real page can be driven offline (fixtures)
 * or against the live registers.
 */
import { JSDOM, VirtualConsole } from "jsdom";
import fs from "node:fs";
import path from "node:path";

export const ROOT = path.resolve(import.meta.dirname, "..");
export const wait = (ms) => new Promise((r) => setTimeout(r, ms));
export const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8");

/** Wrap a fetch so it honours the AbortSignal the page passes (a jsdom one). */
export function abortable(fn) {
  return (input, init = {}) =>
    new Promise((resolve, reject) => {
      const signal = init.signal;
      const fail = () => reject(new Error("aborted"));
      if (signal?.aborted) return fail();
      signal?.addEventListener("abort", fail);
      Promise.resolve(fn(String(input), init)).then(resolve, reject);
    });
}

export function loadPage({ fetch, hash = "", onError }) {
  const vc = new VirtualConsole();
  const errors = [];
  vc.on("jsdomError", (e) => {
    // jsdom does not implement navigation or object URLs; those are not our bugs.
    if (/Not implemented|navigation/i.test(e.message)) return;
    errors.push(e.message);
    (onError || ((m) => console.error("  jsdom:", m)))(e.message);
  });
  vc.on("error", (m) => errors.push(String(m)));

  const dom = new JSDOM(read("docs/index.html"), {
    runScripts: "dangerously",
    url: "https://example.github.io/brain_trials_finder/" + hash,
    virtualConsole: vc,
    pretendToBeVisual: true,
    beforeParse(w) {
      w.Response = Response;
      w.Blob = Blob;
      w.URL.createObjectURL = () => "blob:test";
      w.URL.revokeObjectURL = () => {};
      w.fetch = fetch;
    },
  });
  const d = dom.window.document;

  async function settle(timeoutMs = 60000) {
    const deadline = Date.now() + timeoutMs;
    await wait(30);
    while (Date.now() < deadline) {
      if (d.getElementById("goBtn").textContent === "Search") return true;
      await wait(40);
    }
    return false;
  }

  function set(id, value) {
    const el = d.getElementById(id);
    if (el.type === "checkbox") el.checked = !!value;
    else el.value = value;
  }

  async function search(fields = {}) {
    for (const [id, v] of Object.entries(fields)) set(id, v);
    d.getElementById("q").dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
    return settle();
  }

  function snapshot() {
    const cards = [...d.querySelectorAll(".trial")];
    const by = { CTG: 0, ISRCTN: 0, CTIS: 0, ICTRP: 0 };
    for (const c of cards) by[(c.className.match(/\br-(\w+)/) || [])[1]]++;
    return {
      cards,
      by,
      ids: cards.map((c) => c.querySelector(".id").textContent.trim()),
      errors: [...d.querySelectorAll(".src.err")].map((e) => e.textContent.trim()),
      sources: [...d.querySelectorAll(".src")].map((e) => e.textContent.trim()),
      count: d.getElementById("count").textContent.trim(),
      hidden: d.getElementById("hiddenNote").textContent.trim(),
      notices: [...d.querySelectorAll(".notice")].map((e) => e.textContent.trim()),
      blankTitles: cards.filter((c) => !c.querySelector("h3").textContent.trim()).length,
      missingIds: cards.filter((c) => !c.querySelector(".id").textContent.trim()).length,
      garbage: cards.filter((c) => /\b(undefined|NaN|null|Invalid Date)\b|\[object/.test(
        [...c.querySelectorAll("h3,.meta,.reasons,.meter,.sites summary")].map((n) => n.textContent).join(" "))).length,
    };
  }

  return { dom, d, w: dom.window, errors, settle, search, set, snapshot, close: () => dom.window.close() };
}
