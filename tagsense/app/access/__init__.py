"""Access codes at the door: QR scanning on a second camera, verified events.

Off by default (`access_enabled`). Imported by app.main only when enabled, and
it shares no state with the bin logic: its own config store (/data/access),
its own frame sources and threads, and its own MQTT connection with its own
login, under tagsense/access/#. It only reuses stateless helpers (frame
fetchers, JPEG decoding, crop maths).

TagSense never unlocks anything: it emits events, and Home Assistant
automations decide what to do with them.
"""
