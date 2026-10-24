# Companion

Full export of the Bitfocus Companion config from the stream PC (Companion 5.0.7): button pages, triggers, custom variables, connections, surfaces and the image library.

## Restoring

In Companion, go to **Import / Export**, choose **Import**, and pick `companion_full_config.json`.

These connection fields were replaced with `REDACTED` before committing, so re-enter them after importing:

- `obs` (OBS Studio): WebSocket password
- `vlc` (VLC): HTTP password
- `Spotify`: client secret and refresh token (re-authorize through the module's auth URL)

## Updating this export

With Companion running, export it as readable JSON:

```bash
curl -s "http://127.0.0.1:8000/int/export/full?format=json" -o companion_full_config.json
```

Then redact the fields above before committing.
