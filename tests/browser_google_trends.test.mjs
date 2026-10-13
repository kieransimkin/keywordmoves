import assert from "node:assert/strict";
import test from "node:test";
import {collectTrends, collectAndAnalyseTrends} from "../src/keywordmoves/browser/google_trends.mjs";

function fake(changes = {}) {
  let url = "about:blank", time = 0;
  const actions = [];
  const s = {challenge:false, unavailable:false, seed:"example song", countryButtons:["Worldwide", "All categories"],
    window:"Past 12 months", property:"YouTube Search", termType:"Search term", cards:1, ready:true, noData:false, ...changes};
  const tab = {url:async () => url, reload:async () => actions.push("reload"), goto:async x => {url=x; actions.push("goto");},
    playwright:{evaluate:async () => ({...s,url:changes.url ?? url}),
      locator:() => ({filter:() => ({count:async () => s.cards, locator:() => ({click:async () => {
        actions.push("download"); if (changes.clickError) throw Error("private URL");
        if (changes.afterClick) Object.assign(s,changes.afterClick);
      }})})}),
      waitForEvent:async () => {if (changes.downloadError) throw Error("private URL"); return {path:async () => "/synthetic/download.csv"};}}};
  const runtime = {now:() => time, pause:async ms => {time+=ms;}, readFile:async () =>
    new TextEncoder().encode("\ufeffCategory: All categories\nWeek,example song: (Worldwide)\n2026-10-04,25\n")};
  return {tab, runtime, actions};
}

test("one native download hands original bytes to a bounded receipt", async () => {
  const {tab,runtime,actions}=fake();
  const capture=await collectTrends(tab,{seed:"example song"},runtime);
  assert.equal(capture.controls.property,"YouTube Search");
  assert.ok(capture.csv.startsWith("\ufeff"));
  assert.deepEqual(actions,["goto","download"]);
});
for (const [changes,code] of [
  [{challenge:true},"access-unavailable"], [{unavailable:true},"access-unavailable"],
  [{noData:true},"source-data-unavailable"], [{seed:"changed"},"scope-changed"],
  [{property:"Web Search"},"scope-changed"], [{countryButtons:["United Kingdom","All categories"]},"scope-changed"],
  [{ready:false},"chart-timeout"], [{cards:2},"ambiguous-chart"],
  [{url:"https://evil.test/"},"unexpected-navigation"], [{afterClick:{seed:"other"}},"scope-changed"],
  [{downloadError:true},"download-unavailable"], [{clickError:true},"download-control-unavailable"],
]) test(code+JSON.stringify(changes),async()=>{
  const {tab,runtime,actions}=fake(changes);
  await assert.rejects(collectTrends(tab,{seed:"example song",maxWaitSeconds:5},runtime),new RegExp(code));
  assert.ok(actions.filter(x=>x==="download").length<=1);
});
test("unbounded or invalid downloaded text is rejected",async()=>{
  for(const bytes of [new Uint8Array(5000001),new Uint8Array([255])]){
    const {tab,runtime}=fake();runtime.readFile=async()=>bytes;
    await assert.rejects(collectTrends(tab,{seed:"example song"},runtime),/export-too-large|export-not-utf8/);
  }
});
test("invalid requests never navigate",async()=>{
  for(const request of [{seed:"a,b"},{seed:"example song",country:"XX"},{seed:"example song",chart:"regions"},
    {seed:"example song",maxWaitSeconds:121},{seed:"example song",api_key:"secret"}]){
    const {tab,runtime,actions}=fake();await assert.rejects(collectTrends(tab,request,runtime),/invalid-request/);
    assert.deepEqual(actions,[]);
  }
});
test("local analysis failure preserves the receipt without source retry",async()=>{
  const {tab,runtime,actions}=fake();
  await assert.rejects(collectAndAnalyseTrends(tab,{seed:"example song"},{readFile:runtime.readFile,
    analyse:async()=>{throw Error("analysis unavailable");}}),error=>{
    assert.equal(error.capture.schema,"keywordmoves-trends-browser/v1");return true;
  });
  assert.equal(actions.filter(x=>x==="download").length,1);
});
