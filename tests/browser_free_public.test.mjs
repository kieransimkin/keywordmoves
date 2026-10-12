import assert from "node:assert/strict";
import test from "node:test";
import {collectPublic} from "../src/keywordmoves/browser/free_public.mjs";

const KE = "https://keywordseverywhere.com/tools/youtube-keyword-tool/";
const YT = "https://ytkeywordtool.com/";
const JOB = "?job=00000000-0000-0000-0000-000000000000";

function fake({source = "keywords-everywhere", quota = "Today\n10/250 keyword lookups",
  challenge = false, existing = false, afterSubmit, hydrate = false} = {}) {
  let clock = 0, reads = 0, submitted = false, stopped = false;
  const actions = [];
  const s = {url: source === "keywords-everywhere" ? KE : YT, quota, challenge,
    seed: "", effectiveSeed: "example song", country: "", running: existing, total: 0, html: "<main>synthetic</main>",
    ytReady: false, keReady: false, ytError: "", keErrors: "", displayedRows: 0};
  const tab = {url: async () => s.url, reload: async () => actions.push("reload"),
    goto: async url => { actions.push("goto"); s.url = url; },
    playwright: {
      evaluate: async () => {
        reads++;
        if (submitted && !stopped) {
          if (afterSubmit) Object.assign(s, afterSubmit(s, clock));
          else Object.assign(s, {running: source !== "ytkeywordtool", total: 31,
            ytReady: source === "ytkeywordtool", keReady: source !== "ytkeywordtool",
            url: source === "ytkeywordtool" ? YT : KE + JOB});
        }
        return {...s, quota: hydrate && reads < 3 ? "" : s.quota};
      },
      getByRole: (role, {name}) => ({
        fill: async value => { actions.push(`fill:${name}`); s.seed = value; },
        selectOption: async value => { actions.push(`select:${name}`); if (name === "Country") s.country = value; },
        click: async () => {
          actions.push(`click:${name}`);
          if (name === "Stop the running harvest") { stopped = true; s.running = false; }
          else { submitted = true; }
        },
      }),
    },
  };
  return {tab, actions, runtime: {now: () => clock, pause: async ms => { clock += ms; }}};
}

test("normal job URL is retained, bounded harvest stopped and input verified", async () => {
  const {tab, actions, runtime} = fake();
  const capture = await collectPublic(tab, {source: "keywords-everywhere", seed: "example song"}, runtime);
  assert.equal(capture.page_url, KE + JOB);
  assert.equal(capture.collection.stopped_early, true);
  assert.equal(capture.collection.displayed_country, "");
  assert.equal(actions.filter(x => x === "click:Discover keywords").length, 1);
  assert.equal(actions.filter(x => x === "click:Stop the running harvest").length, 1);
  assert.ok(actions.includes("select:Per page"));
});

test("asynchronous quota hydration waits before submitting", async () => {
  const {tab, runtime} = fake({hydrate: true});
  assert.equal((await collectPublic(tab, {source: "keywords-everywhere", seed: "example song"}, runtime)).source, "keywords-everywhere");
});

for (const quota of ["", "Today\n230/250 keyword lookups", "Today\n250/250 keyword lookups"]) {
  test(`unknown or exhausted free quota does not submit: ${quota}`, async () => {
    const {tab, actions, runtime} = fake({quota});
    await assert.rejects(collectPublic(tab, {source: "keywords-everywhere", seed: "example song"}, runtime), /free-quota/);
    assert.ok(!actions.some(x => x.startsWith("click:")));
  });
}

test("visible challenge prevents all form actions", async () => {
  const {tab, actions, runtime} = fake({challenge: true});
  await assert.rejects(collectPublic(tab, {source: "keywords-everywhere", seed: "example song"}, runtime), /access-challenge/);
  assert.ok(!actions.some(x => x.startsWith("click:") || x.startsWith("fill:")));
});

test("pre-existing harvest is left with its owner", async () => {
  const {tab, actions, runtime} = fake({existing: true});
  await assert.rejects(collectPublic(tab, {source: "keywords-everywhere", seed: "example song"}, runtime), /already-running/);
  assert.deepEqual(actions, []);
});

test("source failure after job navigation stops the owned harvest", async () => {
  const {tab, actions, runtime} = fake({afterSubmit: () => ({url: KE + JOB, keErrors: "request failed", running: true})});
  await assert.rejects(collectPublic(tab, {source: "keywords-everywhere", seed: "example song"}, runtime), /query-unavailable/);
  assert.ok(actions.includes("click:Stop the running harvest"));
  assert.equal(actions.filter(x => x === "click:Discover keywords").length, 1);
});

test("mid-harvest country changes reject results and stop", async () => {
  const {tab, actions, runtime} = fake({afterSubmit: () => ({url: KE + JOB, running: true, country: "us"})});
  await assert.rejects(collectPublic(tab, {source: "keywords-everywhere", seed: "example song"}, runtime), /form-changed/);
  assert.ok(actions.includes("click:Stop the running harvest"));
});

test("YT results collect once and no inferred country is attached", async () => {
  const {tab, actions, runtime} = fake({source: "ytkeywordtool"});
  const result = await collectPublic(tab, {source: "ytkeywordtool", seed: "example song"}, runtime);
  assert.equal(result.collection.stopped_early, false);
  assert.equal(result.country, undefined);
  assert.equal(result.effective_seed, "example song");
  assert.equal(actions.filter(x => x === "click:Get Keywords").length, 1);
});

test("YT timeout makes no retry or fallback", async () => {
  const {tab, actions, runtime} = fake({source: "ytkeywordtool", afterSubmit: () => ({ytReady: false})});
  await assert.rejects(collectPublic(tab, {source: "ytkeywordtool", seed: "example song", maxWaitSeconds: 5}, runtime), /timeout/);
  assert.equal(actions.filter(x => x === "click:Get Keywords").length, 1);
});

test("arbitrary navigation after submission never becomes evidence", async () => {
  const {tab, runtime} = fake({source: "ytkeywordtool", afterSubmit: () => ({url: "https://other.invalid/", ytReady: true, total: 1})});
  await assert.rejects(collectPublic(tab, {source: "ytkeywordtool", seed: "example song"}, runtime), /unexpected-navigation/);
});

for (const values of [{maxHarvestSeconds: 100}, {maxRows: 0}, {maxRows: false},
  {country: "GB", currency: "USD"}, {source: "vidiq"}, {api_key: "unused"}]) {
  test(`invalid options fail before browser access: ${JSON.stringify(values)}`, async () => {
    await assert.rejects(collectPublic({}, {source: "keywords-everywhere", seed: "example song", ...values}), /invalid|unknown/);
  });
}
