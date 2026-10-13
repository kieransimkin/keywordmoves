/** Native Google Trends YouTube Search CSV collection on an authorised idle tab. */
import {CollectionError, analyseCapture} from "./free_public.mjs";

const SOURCE = "https://trends.google.com/trends/explore";
const COUNTRIES = {GLB: "Worldwide", GB: "United Kingdom", US: "United States",
  CA: "Canada", AU: "Australia", IN: "India", NZ: "New Zealand", ZA: "South Africa"};
const WINDOWS = {"today 12-m": "Past 12 months", "today 3-m": "Past 90 days", "today 5-y": "Past 5 years"};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function state(tab, chart) {
  return tab.playwright.evaluate(chart => {
    const visible = e => e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden";
    const text = e => (e?.innerText ?? "").replace(/\s+/g, " ").trim();
    const title = chart === "interest" ? "Interest over time" : "Related queries";
    const cards = [...document.querySelectorAll(".widget-template")].filter(e => text(e).startsWith(title));
    const inputs = [...document.querySelectorAll('input[aria-label="Add a search term"]')].filter(visible);
    const buttons = [...document.querySelectorAll('button, [role="button"]')].filter(visible).map(text);
    return {url: location.href,
      challenge: /captcha|access denied|just a moment|verify you are/i.test(document.title) ||
        [...document.querySelectorAll('input[type="password"], iframe[src*="/bframe"], #captcha-form')].some(visible),
      unavailable: /unusual traffic|too many requests|access denied/i.test(document.body?.innerText ?? ""),
      sourceError: /there was a problem displaying this page|something went wrong/i.test(document.body?.innerText ?? ""),
      seed: inputs.length === 1 ? inputs[0].value : null, seeds: inputs.map(e=>e.value),
      countryButtons: buttons,
      window: text(document.querySelector('md-select[aria-label^="Select time period:"]')),
      property: text(document.querySelector('md-select[aria-label^="Select stories location:"]')),
      termType: text(document.querySelector('.explorepage-content-header')).includes("Search term") ? "Search term" : null,
      termCount: (text(document.querySelector('.explorepage-content-header')).match(/Search term/g) ?? []).length,
      cards: cards.length, ready: cards.length === 1 && visible(cards[0].querySelector('button[title="CSV"]')),
      noData: cards.some(e => /not enough data|no data|try again later|something went wrong/i.test(text(e))),
    };
  }, chart);
}

function check(s, expected, country, window, seed, ready = false) {
  const requested = Array.isArray(seed) ? seed : [seed];
  const queryText = requested.join(",");
  let p;
  try { p = new URL(s.url); } catch { throw new CollectionError("unexpected-navigation"); }
  const q = p.searchParams;
  if (p.origin !== "https://trends.google.com" || p.pathname !== "/trends/explore" || p.hash ||
      [...q.keys()].some(k => !["q", "date", "gprop", "hl", "geo", "cat"].includes(k)) ||
      [...q.keys()].some(k => q.getAll(k).length !== 1) || q.get("q") !== queryText ||
      q.get("gprop") !== "youtube" || q.get("hl") !== "en" ||
      (q.get("date") ?? "today 12-m") !== window || (q.get("geo") ?? "") !== (country === "GLB" ? "" : country) ||
      (q.get("cat") ?? "0") !== "0") throw new CollectionError("unexpected-navigation");
  if (s.challenge || s.unavailable) throw new CollectionError("access-unavailable");
  if (s.noData || s.sourceError) throw new CollectionError("source-data-unavailable");
  if (ready && (JSON.stringify(s.seeds ?? [s.seed]) !== JSON.stringify(requested) ||
      requested.length > 1 && s.termCount !== requested.length || s.window !== WINDOWS[window] || s.property !== "YouTube Search" ||
      s.termType !== "Search term" || !s.countryButtons.includes(COUNTRIES[country]) ||
      !s.countryButtons.includes("All categories"))) throw new CollectionError("scope-changed");
}

async function readDownload(path) {
  const fs = await import("node:fs/promises");
  const file = await fs.open(path, "r");
  try {
    const size = (await file.stat()).size;
    if (size > 5000000) throw new CollectionError("export-too-large");
    // Read at most the bound plus one byte, even if the downloaded file grows.
    const bytes = new Uint8Array(5000001);
    const {bytesRead} = await file.read(bytes, 0, bytes.length, 0);
    if (bytesRead > 5000000 || bytesRead !== size) throw new CollectionError("export-changed");
    return bytes.subarray(0, bytesRead);
  } finally { await file.close(); }
}

export async function collectTrends(tab, request, runtime = {}) {
  if (!request || Object.keys(request).some(k => !["seed", "seeds", "country", "window", "chart", "maxWaitSeconds"].includes(k)))
    throw new CollectionError("invalid-request");
  const {seed, country = "GLB", window = "today 12-m", chart = "interest", maxWaitSeconds = 45} = request;
  const words = request.seeds ?? [seed];
  if (request.seeds && Object.hasOwn(request,"seed") || !Array.isArray(words) || words.length < 1 || words.length > 5 ||
      new Set(words.map(w=>typeof w === "string" ? w.toLocaleLowerCase() : w)).size !== words.length ||
      words.some(w=>typeof w !== "string" || !w.trim() || w !== w.trim() || w.length > 200 || /[,\x00-\x1f]/.test(w) || /^\/(m|g)\//.test(w)) ||
      words.length > 1 && chart !== "interest" || !Object.hasOwn(COUNTRIES, country) ||
      !Object.hasOwn(WINDOWS, window) || !["interest", "related"].includes(chart) ||
      !Number.isInteger(maxWaitSeconds) || maxWaitSeconds < 5 || maxWaitSeconds > 120)
    throw new CollectionError("invalid-request");
  if (!tab?.playwright?.waitForEvent) throw new CollectionError("browser-download-unavailable");
  const url = new URL(SOURCE);
  for (const [k,v] of Object.entries({date: window, q: words.join(","), gprop: "youtube", hl: "en", ...(country === "GLB" ? {} : {geo:country})}))
    url.searchParams.set(k,v);
  if (await tab.url() === url.href) await tab.reload(); else await tab.goto(url.href);
  const now = runtime.now ?? Date.now, wait = runtime.pause ?? pause, start = now();
  let s;
  while (true) {
    s = await state(tab, chart); check(s, url.href, country, window, words);
    if (s.ready) break;
    if (now() - start >= maxWaitSeconds * 1000) throw new CollectionError("chart-timeout");
    await wait(1000);
  }
  check(s, url.href, country, window, words, true);
  const card = tab.playwright.locator(".widget-template").filter({hasText: chart === "interest" ? /Interest over\s*time/ : "Related queries"});
  if (await card.count() !== 1) throw new CollectionError("ambiguous-chart");
  // Handle rejection immediately: failed clicks must not leave an unhandled event promise.
  const pending = tab.playwright.waitForEvent("download", {timeoutMs: maxWaitSeconds * 1000})
    .then(download => download.path({timeoutMs: maxWaitSeconds * 1000}))
    .then(path => ({path}), () => ({error:true}));
  try { await card.locator('button[title="CSV"]').click({timeoutMs: 5000}); }
  catch { throw new CollectionError("download-control-unavailable"); }
  const download = await pending;
  if (download.error || typeof download.path !== "string") throw new CollectionError("download-unavailable");
  const bytes = await (runtime.readFile ?? readDownload)(download.path);
  if (!(bytes instanceof Uint8Array) || bytes.length > 5000000) throw new CollectionError("export-too-large");
  let csv;
  try { csv = new TextDecoder("utf-8", {fatal:true, ignoreBOM:true}).decode(bytes); }
  catch { throw new CollectionError("export-not-utf8"); }
  s = await state(tab, chart); check(s, url.href, country, window, words, true);
  if (!s.ready) throw new CollectionError("chart-changed");
  const comparison = words.length > 1;
  return {schema: comparison ? "keywordmoves-trends-browser/v2" : "keywordmoves-trends-browser/v1", source: "google-trends", source_url: SOURCE,
    page_url: s.url, observed_at: new Date().toISOString(), ...(comparison ? {keywords:words} : {seed:words[0]}), country, window, chart, csv,
    controls: {...(comparison ? {keywords:words} : {seed:words[0]}), country:COUNTRIES[country], window:WINDOWS[window], property:"YouTube Search",
      category:"All categories", term_type:"Search term"}};
}

export async function collectAndAnalyseTrends(tab, request, options = {}) {
  const capture = await collectTrends(tab, request, {readFile: options.readFile});
  try { return await (options.analyse ?? analyseCapture)(capture, options); }
  catch (error) { error.capture = capture; throw error; }
}
