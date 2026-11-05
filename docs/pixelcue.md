# PixelCue visual keywords

Start PixelCue with a configured local model, then pass a single image, video
or archive supported by that service.

```sh
keywordmoves run pixelcue --operation extract --input cover.png --option limit=12
```

The service receives the resolved path through `POST /v1/keywords` and must
be able to read the file. KeywordMoves uses a direct loopback HTTP(S)
connection and rejects redirects. The default JSON response retains the
source, evidence labels and request ID. Use `--format keywords` for a bare
array of deduplicated phrases in their original order.

| Option | Default | Purpose |
| --- | --- | --- |
| endpoint | http://127.0.0.1:8765 | Loopback HTTP(S) origin |
| model | Server default | PixelCue model profile |
| timeout | 21600 | Positive finite request timeout in seconds |
| limit | All returned phrases | Positive maximum number of retained phrases |
| request_id | Generated UUID | Correlates the request with PixelCue progress |

The first service request may initialise a model. For a short probe, select a
ready model and a shorter timeout. Treat visual tags as proposals and collect
search-demand evidence separately. The adapter tests use a synthetic response
to verify the request and output formats without downloading a model.
