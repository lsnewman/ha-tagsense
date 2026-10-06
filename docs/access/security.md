# Security

Access has had **no independent security review**. Read this page, confirm
events before unlocking, and keep a way in that does not depend on TagSense.

## What it protects against

| Threat | What happens |
|---|---|
| **Forged or altered codes** | Every code is signed (HMAC-SHA256, 80-bit tag); a changed character fails. |
| **Replayed codes** (a code filmed or photographed, or a camera recording) | Each pass code works once and only for about a minute; static codes have limited uses and an expiry. |
| **Fake MQTT events** | Anything on your broker can publish one. [Confirming events](events.md#confirming-events) stops them: TagSense only confirms events it really sent, once each. |
| **Guessing codes at the camera** | Bad codes lock the scanner out for 15 minutes (more than 3 in a scan, or 5 in 10 minutes). |

## What it does not protect against

- **Forwarded static codes:** cannot be prevented, which is why they expire,
  can be limited to one use, and can be revoked.
- **Someone holding your unlocked phone:** a code cannot tell who is showing it.
- **A lockout set off on purpose:** it keeps the door shut, never opens it, but
  it does stop codes working for 15 minutes.

## How it is built

- **TagSense never unlocks anything.** It holds no lock credentials; your
  automations act on its events.
- **Its own MQTT login**, separate from the one every app shares, so its
  traffic is clearly its own.
- **It fails closed.** If it cannot check a code (a storage error, say), the
  event is `unavailable`, never `verified`. If TagSense is down, nothing is
  confirmed.
- **Code text never appears** in events, logs or images. Codes that are not
  verified are shown only as a short fingerprint, and codes are blacked out in
  every saved image.
- **Secrets stay in `/data/access`**, readable only by the app. Backup never
  exports keys, passes, codes or the confirm token, and the
  [debug bundle](../health.md) contains none of them.
- **The confirm step** only answers requests from Home Assistant's own address
  that carry the secret token, and each event ID works once, within 30 s.
- **Only admins** can manage Access in the panel. Everyone else only sees their
  own pass.
