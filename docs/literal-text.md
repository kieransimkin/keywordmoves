# Literal source phrase extraction

Use `text-library --operation extract-literal` when candidates need to be
traceable to actual contiguous wording, including non-Latin lyrics.

```powershell
keywordmoves run text-library --operation extract-literal --input .\tests\fixtures\lyrics.txt --option max_ngram=3 --option limit=20 --format json
```

The dependency-free operation recognises Unicode letters, numbers, combining marks,
internal apostrophes, hyphens and Persian non-joiners. It counts phrases of one
to five words (`max_ngram`, default three). Stop words may occur inside a phrase;
they are never removed to manufacture adjacency. Phrases do not cross
punctuation, line breaks or files. Counts cover the complete input; at most 20
real source spans are retained per candidate, with an explicit truncation flag.
Numbered names such as `Rule 30` and `Paris 2024` remain literal phrases.
File offsets address UTF-8-decoded source text with original line endings retained
and an optional BOM excluded. They are character offsets, not byte offsets.
Keep the same decoding and newline settings when verifying them.
Offsets refer to the decoded original string; normalized phrases use NFC and
case folding. No score, search demand or popularity is inferred.

The existing `extract-local` and LLM operation retain their contracts. Their
filtered-token bigrams are candidate associations and need not be literal source
phrases. Use the new operation for lyric provenance. The English stop list does
not provide linguistic analysis of other languages: a fluent reviewer must
assess the resulting candidates.
