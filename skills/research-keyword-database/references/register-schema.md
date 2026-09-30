# Keyword target register schema

The canonical file is
`Z:\My Songs\Keyword Targets\keyword-targets.csv`. Preserve this exact order:

```text
song_title,epk_url,release_date,public_state,keyword,intent,status,evidence_source,evidence_date,geography,trend_signal,confidence,target_surface,proposed_use,notes,last_reviewed,platform,seed_keyword,derivation_type,source_material,relationship_to_seed
```

## Field rules

| Field | Required meaning |
| --- | --- |
| `song_title` | Exact title from `catalogue.json`. |
| `epk_url` | Catalogue EPK target, even when not yet public. |
| `release_date` | Catalogue ISO release date. |
| `public_state` | Current catalogue-derived state; do not infer publication from a URL alone. |
| `keyword` | One exact phrase. Blank only for a catalogue placeholder. Preserve meaningful punctuation and casing, while deduplicating case-insensitively. |
| `intent` | Truthful audience purpose such as informational, navigational, discovery, listening, or mixed; be more specific where evidence supports it. |
| `status` | `research_pending`, `proposed`, `adopted`, `testing`, `retired`, or `rejected`. Only a blank-keyword catalogue placeholder should normally be `research_pending`. |
| `evidence_source` | Named first-party surface, public tool, file, model-assisted method, or combined dated sources. Do not use a vague universal score. |
| `evidence_date` | ISO access or observation date for the evidence currently summarised. |
| `geography` | Exact market such as `GB`, or blank only when geography is inapplicable or unavailable and that limit is stated. |
| `trend_signal` | Evidence summary with the real metric and unit, or explicit `unavailable`/`unvalidated`; never invent volume. |
| `confidence` | Calibrated `low`, `medium`, or `high` based on truthful fit and evidence. Model fluency alone does not increase it. |
| `target_surface` | Exact EPK URL, site page, Search Console page, or named social/platform surface. |
| `proposed_use` | Specific, natural use or test; never keyword stuffing or an assertion that a public change has happened. |
| `notes` | Evidence limits, risk, earlier state, decisions, unavailable routes, and dated lifecycle context. Preserve history. |
| `last_reviewed` | ISO date of the latest substantive review. |
| `platform` | Evidence platform or surface, especially for native discovery. Leave blank only for genuinely cross-platform or non-platform evidence. |
| `seed_keyword` | Existing exact phrase that prompted a related candidate. Blank only when there is no useful seed. |
| `derivation_type` | Provenance such as `catalogue-placeholder`, `existing-target`, `related-search`, `tool-discovered`, `text-extracted`, `llm-related`, `lyric-derived`, or another precise maintained label. |
| `source_material` | Canonical lyric path, export path, research surface, or other exact source. Do not paste unnecessary lyrics or private account data. |
| `relationship_to_seed` | Concise relationship such as initial seed, long-tail refinement, question variant, semantic concept, platform wording, or lyric theme. |

## Identity and merging

For non-empty keywords, use the case-folded tuple
`song_title + keyword + platform` as the duplicate-detection key. Normalise
surrounding whitespace but do not rewrite the phrase's meaningful punctuation.
A platform-specific record stays separate because its evidence is not
interchangeable with web search or another platform.

When fresh evidence concerns an existing key, update that canonical row and add
dated context without erasing the older state. If two sources provide unlike
metrics, name both and keep their units and limitations separate in the evidence
summary or linked report; do not average them.

## Lifecycle

- `research_pending`: catalogue item exists, but no defensible keyword has been
  recorded in that placeholder row.
- `proposed`: plausible and truthfully related, but not publicly implemented.
- `adopted`: Kieran has selected the target with dated decision evidence.
- `testing`: an authorised use is live and has a defined review metric/window.
- `retired`: previously used or retained, then deliberately stopped with a
  dated reason.
- `rejected`: reviewed and ruled out with a recorded reason.

Transmission, a saved CSV row, an LLM response, a Trends index, or a report does
not advance a lifecycle state by itself.

## Minimum validation

At the time this reference was created, the register had 54 catalogue songs,
54 blank-keyword `research_pending` placeholders, 48 non-empty `proposed` rows,
and no duplicate non-empty song/phrase/platform keys. These are a dated baseline
from 1 October 2026, not permanent required counts. Compare current counts with
the current catalogue and explain every intended delta.
