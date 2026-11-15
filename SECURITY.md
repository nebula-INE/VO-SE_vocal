# VO-SE Security Notes

## Local web server

The bundled web server binds to `127.0.0.1` by default. `VOSE_HOST` can be set
to an explicit interface address only when remote-device access is intentional.
Do not expose this single-user API server to the public Internet: it does not
provide user authentication.

Cross-origin browser access is disabled unless exact origins are listed in the
comma-separated `VOSE_ALLOWED_ORIGINS` environment variable. Host headers are
restricted to loopback names unless explicit values are listed in
`VOSE_ALLOWED_HOSTS`. CORS and Host validation are not authentication.

## Voicebank archives and paths

Uploads are limited to 600 MiB. Chunked uploads are limited to 10 MiB per chunk
and 60 chunks. ZIP extraction rejects traversal/absolute paths, more than
100,000 entries, any member larger than 2 GiB, and total extracted content
larger than 2 GiB. Archives are staged before replacing an existing voicebank.

`oto.ini` WAV references are constrained to the owning voicebank directory.
The web sample API also resolves symlinks before serving and rejects paths
that resolve outside that directory.

## Local model checkpoints

Legacy `oto_cache.vose` pickle caches are ignored and rebuilt in JSON format.
Optimizer model checkpoints still use pickle for compatibility, but are not
loaded unless `VOSE_TRUST_PICKLE_MODELS=1` is set. Only set this for model files
you created locally and fully trust; unpickling can execute arbitrary code.
