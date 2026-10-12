/** Automatic public-form collection on a caller-owned, supported browser tab.
 * No browser startup, profiles, hidden state, endpoints, CAPTCHA solver or login.
 * DOM evaluation is read-only. Python analysis uses the existing source parsers.
 */
const SOURCES = Object.freeze({
  ytkeywordtool: "https://ytkeywordtool.com/",
  "keywords-everywhere": "https://keywordseverywhere.com/tools/youtube-keyword-tool/",
});
const CURRENCIES = Object.freeze({GLB: "USD", US: "USD", GB: "GBP", CA: "CAD",
  AU: "AUD", IN: "INR", NZ: "NZD", ZA: "ZAR"});

export class CollectionError extends Error {
  constructor(code) { super(code); this.name = "CollectionError"; this.code = code; }
}
const bounded = (value, fallback, low, high) => {
  const n = value ?? fallback;
  if (!Number.isInteger(n) || n < low || n > high) throw new CollectionError("invalid-bounds");
  return n;
};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function state(tab) {
  // Read rendered UI and current form values only; no application state or fetch.
  return tab.playwright.evaluate(() => {
    const visible = e => !!e && e.getClientRects().length > 0 &&
      !e.closest('[hidden], [aria-hidden="true"], .hidden') &&
      getComputedStyle(e).visibility !== "hidden" && getComputedStyle(e).display !== "none";
    const get = id => document.getElementById(id);
    const text = id => visible(get(id)) ? get(id).innerText : "";
    const body = document.body?.innerText ?? "";
    const challenge = [...document.querySelectorAll('input[type="password"], iframe[src*="/bframe"], #challenge-form')].some(visible) ||
      /access denied|just a moment|verify you are|captcha/i.test(document.title);
    const total = get("kt-results-count")?.innerText ?? text("results-count").match(/^(\d+) keywords? found$/)?.[1] ?? "0";
    return {url: location.href, challenge, body: body.slice(0, 20000),
      seed: get("kt-seed-single")?.value ?? get("keyword-input")?.value,
      effectiveSeed: get("seed-input")?.value,
      country: get("kt-country")?.value,
      ytReady: visible(get("tool-results")), ytError: text("tool-error-message"),
      keReady: visible(get("kt-results")), keErrors: text("kt-errors-list"),
      running: visible(get("kt-cancel")),
      total: /^\d+$/.test(total.trim()) ? Number(total.trim()) : null,
      displayedRows: document.querySelectorAll("#kt-results-body > tr").length,
      quota: text("quota-banner"),
      html: document.querySelector("main")?.outerHTML ?? get("tool-results")?.outerHTML ?? "",
    };
  });
}

function allowedPage(url, source) {
  if (url === SOURCES[source]) return true;
  if (source !== "keywords-everywhere") return false;
  try {
    const p = new URL(url), base = new URL(SOURCES[source]);
    return p.origin === base.origin && p.pathname === base.pathname && !p.hash &&
      [...p.searchParams.keys()].length === 1 &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(p.searchParams.get("job") ?? "");
  } catch { return false; }
}

function check(s, source) {
  if (!allowedPage(s.url, source)) throw new CollectionError("unexpected-navigation");
  if (s.challenge) throw new CollectionError("access-challenge");
  if (source === "ytkeywordtool" && s.ytError) throw new CollectionError("provider-query-unavailable");
  if (source === "keywords-everywhere" && s.keErrors) throw new CollectionError("provider-query-unavailable");
}

/** One seed, bounded public UI interactions; partial harvests stay partial. */
export async function collectPublic(tab, request, runtime = {}) {
  if (!request || Object.keys(request).some(k => !["source", "seed", "country", "currency", "maxWaitSeconds", "maxHarvestSeconds", "maxRows"].includes(k)))
    throw new CollectionError("unknown-options");
  const {source, seed} = request;
  if (!Object.hasOwn(SOURCES, source) || typeof seed !== "string" || !seed.trim() || seed.length > 400 || /[\x00-\x1f]/.test(seed))
    throw new CollectionError("invalid-source-or-seed");
  const expectedUrl = SOURCES[source], requestedSeed = seed.trim();
  const maxWait = bounded(request.maxWaitSeconds, 45, 5, 120) * 1000;
  const maxHarvest = bounded(request.maxHarvestSeconds, 20, 5, 60) * 1000;
  const maxRows = bounded(request.maxRows, 30, 1, 200);
  const now = runtime.now ?? Date.now, wait = runtime.pause ?? pause;
  const country = request.country ?? "GLB", currency = request.currency ?? CURRENCIES[country];
  if (source === "keywords-everywhere" && (typeof country !== "string" || !Object.hasOwn(CURRENCIES, country) || currency !== CURRENCIES[country]))
    throw new CollectionError("invalid-country-or-currency");
  // Callers provide a dedicated idle tab. Never overwrite a running harvest.
  const beforeUrl = await tab.url();
  if (allowedPage(beforeUrl, source)) {
    const current = await state(tab);
    if (current.running) throw new CollectionError("tab-already-running");
    if (beforeUrl === expectedUrl) await tab.reload();
    else await tab.goto(expectedUrl);
  } else await tab.goto(expectedUrl);
  let s = await state(tab);
  check(s, source, expectedUrl);
  if (source === "keywords-everywhere") {
    let quota = s.quota.match(/(\d+)\s*\/\s*(\d+)\s*keyword lookups/i);
    const quotaStarted = now();
    while (!quota && now() - quotaStarted < Math.min(maxWait, 10000)) {
      await wait(1000);
      s = await state(tab);
      check(s, source, expectedUrl);
      quota = s.quota.match(/(\d+)\s*\/\s*(\d+)\s*keyword lookups/i);
    }
    if (!quota || Number(quota[2]) - Number(quota[1]) < maxRows + 10)
      throw new CollectionError("insufficient-or-unknown-free-quota");
    await tab.playwright.getByRole("combobox", {name: "Country", exact: true}).selectOption(country === "GLB" ? "" : country === "GB" ? "uk" : country.toLowerCase());
    await tab.playwright.getByRole("textbox", {name: "Seed keyword", exact: true}).fill(requestedSeed);
  } else await tab.playwright.getByRole("textbox", {name: "Video topic or title", exact: true}).fill(requestedSeed);
  s = await state(tab);
  check(s, source, expectedUrl);
  if (s.seed !== requestedSeed) throw new CollectionError("seed-not-selected");
  const selectedCountry = country === "GLB" ? "" : country === "GB" ? "uk" : country.toLowerCase();
  if (source === "keywords-everywhere" && s.country !== selectedCountry)
    throw new CollectionError("country-not-selected");
  let started = false, stopped = false;
  const startedAt = now();
  try {
    await tab.playwright.getByRole("button", {name: source === "ytkeywordtool" ? "Get Keywords" : "Discover keywords", exact: true}).click();
    started = true;
    while (true) {
      s = await state(tab);
      check(s, source, expectedUrl);
      if (s.seed !== requestedSeed || source === "keywords-everywhere" && s.country !== selectedCountry)
        throw new CollectionError("form-changed-during-collection");
      if (source === "ytkeywordtool" && s.ytReady && s.total > 0) break;
      if (source === "keywords-everywhere") {
        if (s.keReady && !s.running && s.total > 0) break;
        if (now() - startedAt >= maxHarvest || s.total >= maxRows) {
          if (s.running) {
            await tab.playwright.getByRole("button", {name: "Stop the running harvest", exact: true}).click();
            stopped = true;
          }
          break;
        }
      }
      if (now() - startedAt >= maxWait) throw new CollectionError("collection-timeout");
      await wait(1000);
    }
    if (source === "keywords-everywhere") {
      // Stop aborts prefix harvesting; allow its in-flight display/enrichment to settle.
      await wait(1500);
      s = await state(tab);
      check(s, source, expectedUrl);
      if (!s.keReady || s.running || !s.total) throw new CollectionError("no-settled-results");
      await tab.playwright.getByRole("combobox", {name: "Per page", exact: true}).selectOption("200");
      s = await state(tab);
    }
    check(s, source, expectedUrl);
    if (s.seed !== requestedSeed || source === "keywords-everywhere" && s.country !== selectedCountry)
      throw new CollectionError("form-changed-during-collection");
    if (!s.html || new TextEncoder().encode(s.html).length > 5000000 || !Number.isInteger(s.total) || s.total > 10000)
      throw new CollectionError("invalid-capture");
    if (source === "ytkeywordtool" && (typeof s.effectiveSeed !== "string" || !s.effectiveSeed.trim()))
      throw new CollectionError("effective-seed-missing");
    return {schema: "keywordmoves-public-browser/v1", source, source_url: expectedUrl, page_url: s.url,
      observed_at: new Date().toISOString(), seed: requestedSeed,
      ...(source === "ytkeywordtool" ? {effective_seed: s.effectiveSeed.trim()} : {}),
      ...(source === "keywords-everywhere" ? {country, currency} : {}), html: s.html,
      collection: {stopped_early: stopped, displayed_total: s.total,
        ...(source === "keywords-everywhere" ? {displayed_country: s.country} : {})}};
  } finally {
    // No retry/fallback after a failure. Stop an owned KE harvest when possible.
    if (started && source === "keywords-everywhere") {
      const final = await state(tab);
      if (allowedPage(final.url, source) && final.running)
        await tab.playwright.getByRole("button", {name: "Stop the running harvest", exact: true}).click();
    }
  }
}

/** Analyse without writing/importing HTML files. The executable is caller-owned. */
export async function analyseCapture(capture, {python = "python", cwd} = {}) {
  const {spawn} = await import("node:child_process");
  return new Promise((resolve, reject) => {
    let child;
    try { child = spawn(python, ["-m", "keywordmoves.browser_capture"], {shell: false, windowsHide: true, cwd}); }
    catch { reject(new CollectionError("analysis-process-unavailable")); return; }
    let output = "", length = 0;
    const timer = setTimeout(() => { child.kill(); reject(new CollectionError("analysis-timeout")); }, 30000);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", chunk => {
      length += Buffer.byteLength(chunk, "utf8");
      if (length > 10000000) { child.kill(); reject(new CollectionError("analysis-output-too-large")); }
      else output += chunk.toString("utf8");
    });
    child.stderr.resume();
    child.on("error", () => reject(new CollectionError("analysis-process-unavailable")));
    child.stdin.on("error", () => reject(new CollectionError("analysis-input-failed")));
    child.on("close", code => {
      clearTimeout(timer);
      if (code !== 0) reject(new CollectionError("analysis-failed"));
      else { try { resolve(JSON.parse(output)); } catch { reject(new CollectionError("analysis-json-invalid")); } }
    });
    child.stdin.end(JSON.stringify(capture));
  });
}

export async function collectAndAnalyse(tab, request, options = {}) {
  const capture = await collectPublic(tab, request);
  try {
    return await (options.analyse ?? analyseCapture)(capture, options);
  } catch (error) {
    // Preserve the already collected receipt for a supported local handoff;
    // an analysis-host failure must never cause another source query.
    error.capture = capture;
    throw error;
  }
}
