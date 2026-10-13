# Require listening intent and topic together

One generic shared word such as “songs” does not establish relevance. Conversely,
“what is political satire” may match the topic but describe a different intent.
Since 0.5.7, candidate-focus accepts an optional `require_groups` rule:

```powershell
keywordmoves run candidate-focus --operation related --keyword "political songs" `
  --input source-result.json --option min_overlap=0 `
  --option "require_groups=song|songs|music|anthem;political|satire|protest"
```

Each semicolon-separated group requires one of its pipe-separated literal phrase
alternatives. This example requires listening context AND political context.
Use 1-10 groups, each with 1-20 non-empty alternatives; omit the option to retain
the original behaviour. Empty groups/alternatives, non-text or values over 20,000
characters are rejected before filtering. The scalar string also composes in a
research-batch plan without new network requests.

The existing include/exclude rules and exact Unicode term matching still apply.
There is no stemming, inferred synonym, translation, automatic exclusion list,
semantic approval or universal demand score. Write explicit alternatives for
the intended audience; source overlap alone cannot certify truthful song fit.
Every rejected row records `required_context_group_absent` when appropriate.
Original source values, scores, units, dates and geography remain unchanged.
The settings and their SHA-256 explain the exact local rule used.

Run `python -m pytest tests/test_candidate_focus.py` for synthetic regressions
covering both-topic requirements, retained evidence and invalid group syntax.
