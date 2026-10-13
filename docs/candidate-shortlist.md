# Explicit shortlist review

`candidate-shortlist/select` is an offline decision step after discovery and
source-native analysis. It applies supplied editorial judgments; it does not
independently validate fit, estimate demand, or establish a globally best set.
The module makes no network requests and grants no implementation permission.

Supply exactly two UTF-8 JSON files: a review and a saved KeywordMoves result.
The result must declare `metadata.target_subject` and `metadata.target_platform`.
Those are the intended selection scope; native `metadata.platform` and source
evidence geography, dates, units, bounds and scores retain their original meaning.
Candidate-level target declarations, when supplied, must agree. Reusing Google
planning evidence for a YouTube target does not turn it into YouTube demand.

The review has this strict shape:

```json
{
  "schema": "keywordmoves-shortlist-review/v1",
  "subject": "Example Song",
  "platform": "YouTube",
  "reviewed_at": "2026-10-13",
  "reviewer_kind": "analyst",
  "source_sha256": "EXACT_SHA256_OF_SAVED_POOL_BYTES",
  "decisions": [{
    "phrase": "songs about seaside memories",
    "fit": "direct",
    "intent": "listening",
    "clarity": "natural",
    "group": "seaside memories",
    "priority": 1,
    "rationale": "The supplied story concerns friends remembering the coast."
  }]
}
```

Compute the hash after writing the pool, including its actual encoding and line
endings. Unknown review fields, orphan decisions, duplicate NFC/case-folded
phrases, missing rationale or group, stale hashes and conflicting scope fail.
The emitted phrase spelling comes from the pool. Input files are each limited
to 20 MB, candidate and decision lists to 50,000; non-finite JSON and secret-like
configuration fields are rejected. `reviewer_kind` is a caller declaration
(`human`, `analyst` or `automated`), not a verified identity or endorsement.

`fit` accepts `direct`, `adjacent` or `reject`; `intent` accepts `listening`,
`navigation`, `mixed` or `information`; `clarity` accepts `natural` or `awkward`.
Priority is an integer 1–50,000. Smaller priorities are selected first, with
normalized phrase ordering for ties. Priority never replaces the original
candidate score or source metrics. The caller must assess audience, language,
genre and version fit rather than relying on high search estimates alone.

```sh
keywordmoves run candidate-shortlist --operation select --input review.json --input pool.json
python examples/candidate_shortlist.py --output-dir shortlist-demo
```

| Option | Default | Meaning |
| --- | --- | --- |
| `limit` | `5` | Maximum selected rows, integer 1–50. |
| `minimum` | `5` | Coverage target, integer 1–50, no greater than limit. |
| `max_per_group` | `2` | Cap per explicitly declared normalized intent group, 1–50. |
| `allow_adjacent` | `false` | Permit explicitly reviewed adjacent fit. |
| `intent` | `listening` | Select listening or navigation intent. |

Boolean numbers, unknown options and implicit truthy strings are invalid.
Only natural wording of the requested intent with eligible fit is selected.
Unreviewed phrases are excluded. Metadata records rejected phrases with reasons
(`unreviewed`, `fit_ineligible`, `different_intent`, `awkward_wording`,
`intent_group_cap` or `outside_shortlist_limit`), selection counts, coverage
state and shortfall, review/source/settings hashes and zero network requests.
Grouping depends on the caller's declared clusters; it is not automatic semantic
duplicate detection. An insufficient eligible pool stays a shortage, never a
padded recommendation.

Each selected row retains its evidence, score and other metadata, adding an
auditable `shortlist_review` with rationale and editorial priority. Lifecycle
statuses are not promoted; the selection has `implementation_authorised=false`.
Existing proposed, adopted, rejected and retired campaign decisions still need
their own reconciliation. `research-batch/collect` accepts this offline operation
with the same explicit inputs and options.
