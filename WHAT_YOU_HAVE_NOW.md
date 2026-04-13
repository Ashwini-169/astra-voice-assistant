# What You Have Now

You now have a local, interruption-aware voice assistant stack tuned for both speed and speech quality.

## Current strengths
- Fast first speech chunk in streaming mode.
- Smoother phrase-level playback after the first chunk.
- Runtime control over speech speed and chunking without restart.
- Multiple TTS backend endpoints configurable at runtime.

## Runtime controls you can use today
- `GET http://127.0.0.1:8003/settings`
- `POST http://127.0.0.1:8003/settings`
- `POST http://127.0.0.1:8003/settings/reset`
- `GET http://127.0.0.1:8003/streaming-config`

## Recommended balanced profile
```json
{
  "edge_base_rate_pct": 8,
  "chunk_initial_words": 5,
  "chunk_steady_words": 14,
  "chunk_max_chars": 140
}
```

## If speech is still too broken
- Increase `chunk_steady_words` to 16-18.
- Increase `chunk_max_chars` to 160-200.

## If speech starts too slowly
- Decrease `chunk_initial_words` to 4.

## If speech is too slow
- Increase `edge_base_rate_pct` to 10-12.

## Operational caveat
Duplex mode depends on stable microphone input device access. If you see `MME error 1`, fix device permissions/availability first.
