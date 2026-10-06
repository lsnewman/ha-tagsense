# TagSense design spec

This document records how TagSense works, why it works that way, and the
measurements behind its numbers. The user docs are `tagsense/DOCS.md` (the app's
Documentation tab) and `README.md`, and the history is `tagsense/CHANGELOG.md`.
Keep this file in step when a design decision or a tuned value changes.

## Repo rules

- No hostnames, IP addresses of the development network, tokens, passwords or
  other secrets in the repo, the docs, test data or commit messages.
  Examples use placeholders such as `<frigate-hostname>`.
- Real camera frames live in `test-frames/` (gitignored) and are never
  committed. Tests that need them are skipped if the folder is missing
  (`TAGSENSE_TESTDATA` overrides the path). Images in `docs/` are blurred
  real frames (street, plates, letterboxes, reflections, timestamp) and are
  approved by the maintainer before they are committed.
- Bump `version` in `tagsense/config.yaml` with every release, or Home
  Assistant will not offer the update.
- New detection behaviour must be off or identical by default: existing
  installs must see no change unless they opt in.
- After installation, everything is set in the TagSense panel. App options
  (the Configuration tab) are only for things needed before the panel can
  work, such as `go2rtc_url` and the MQTT overrides (including the access
  feature's on switch and its dedicated MQTT login), and for global tuning.
  Never add a per-object setting as an app option. Never let a setting stop
  the app from starting, because then it can't be fixed from the panel.

## Architecture

A Home Assistant app (Supervisor add-on), Python 3.12 on Debian slim (no
OpenCV wheel exists for musl). It uses OpenCV `opencv-python-headless`
5.0.0.93, paho-mqtt and requests, and runs on amd64 and aarch64.

| Module | Role |
|---|---|
| `main.py` | Options, the `App`: builds camera workers and tracked objects, live reload, MQTT callbacks |
| `objects.py` | Object config, validation, `/data/objects.json` |
| `sources.py` | Frame fetchers: go2rtc `api/frame.jpeg`, HA camera proxy, fallback |
| `cameras.py` | One worker thread per camera; a burst is fetched once and judged by every object on that camera, with early exit |
| `analysis.py`, `sanity.py` | Per-frame pipeline: smear check, then detection |
| `detector.py` | AprilTag detection on a crop, shape gate part 1, tag families, annotation |
| `tracker.py` | One object: settings, decision, scheduler, learned reference, rotation, publishing |
| `decision.py` | Debounced present/absent/unknown state machine |
| `reference.py` | Learned usual size and position, and the 0° orientation |
| `rotation.py` | Lid-plane rotation angle, circular mean, stepped value with hysteresis |
| `mqtt_ha.py` | MQTT client and HA discovery; one HA device per object |
| `web.py`, `web/index.html` | Ingress panel and its JSON API (ingress proxy IP only) |
| `checklog.py`, `snapshots.py` | The 24 h chart log and the state-change images |
| `ha_notify.py` | HA persistent notification for the rejection alert |
| `tagprint.py` | Printable PNG/SVG tags |
| `check.py`, `sweep.py` | Offline tools over a folder of frames |

Data in `/data`: `options.json` (Supervisor), `objects.json`, and
`objects/<id>/` holding `settings.json`, `state.json`, `reference.json`,
`checks.jsonl` and `snapshots/`.

MQTT: `tagsense/availability` (LWT); per object `tagsense/<id>/...` for
state, attributes, diagnostics, settings (`set/<key>` in, `setting/<key>`
out) and commands (`cmd/<name>`). Discovery is under
`homeassistant/<component>/tagsense_<id>/<key>/config`.

## Check and decision

1. A check fetches up to `burst_size` frames, `burst_interval_s` apart. If
   every object on the camera is already present and its first
   `present_min_hits` frames all hit, the burst stops early.
2. Each frame is decoded and scored for smear. Undecodable, smeared and flat
   frames are discarded. A hit always counts, whatever the score.
3. No usable frame means the check failed. After `unknown_after_failures`
   failed checks in a row, the state is `unknown`. **A missing or bad frame
   never produces absent.**
4. At least `present_min_hits` accepted hits means present. Fewer (but more
   than 0) is inconclusive: the state is kept and the miss streak resets.
5. Usable frames with no accepted hit is a clean miss. `absent_checks` misses
   in a row means absent. The streak resets if the previous miss is older
   than 3 poll intervals (at least 5 min).
6. Reads rejected by the shape gate are not the tag, so a check whose only
   reads were rejected is a miss. This was decided on 2026-10-04: a repeating
   phantom of the target ID must never hold the state at present. Three such
   checks in a row raise the *Tag rejected* alert.
7. *Check now* that finds no tag schedules confirmation checks every
   `confirm_delay_s` until absent is confirmed or the tag is seen.

## Detector tuning (tag16h5)

The pipeline crops, converts to grey, upscales 2x (cubic), then runs
`ArucoDetector` with `DICT_APRILTAG_16h5` and these parameters:

| Parameter | Value | OpenCV default |
|---|---|---|
| `perspectiveRemovePixelPerCell` | 8 | 4 |
| `perspectiveRemoveIgnoredMarginPerCell` | 0.15 | 0.13 |
| `maxErroneousBitsInBorderRate` | 0.5 | 0.35 |
| `cornerRefinementMethod` | `CORNER_REFINE_APRILTAG` | none |
| `aprilTagQuadDecimate` | 1.0 | 0.0 |
| `cornerRefinementMaxIterations` | 30 | 30 |

Evidence from the original tuning sweep (development camera, tag lying flat
on a bin lid, about 50 px across in 1080p):

- These parameters won the sweep. The `adaptiveThreshWinSize*`,
  `minMarkerPerimeterRate` and `polygonalApproxAccuracyRate` tweaks produced a
  phantom ID in full-frame tests, so they must not be added.
- Contrast enhancement (CLAHE) was expected to help and in fact hurt
  decoding. It is not used.
- The 2x upscale gives the quad finder enough pixels at about 50 px tags.

### Shape gate

- **Part 1, aspect** (`max_aspect`, default 2.0; it was 3.0 until 0.4.1),
  longest edge over shortest:
  - The real tag measured 1.45-1.52 by day and up to 2.1 once at night under
    IR (2026-10-02 01:35, rejected; the maintainer kept 2.0 for now, and 2.5
    is the suggested fallback).
  - Gravel phantoms measured 2.2-6, and a large 105 px phantom 4.5.
- **Part 2, size** (`min_size_ratio`, default 0.5), active after 10 learned
  hits: phantoms were 15-40% of the real tag's size (8-20 px against 50 px).
  There is deliberately no upper size limit, so an object can move closer.
- **Phantom rate before the gate:** in the first real logs (2026-10-01),
  about one read of a non-target ID every 9 minutes, 8-20 px, aspect 2.2-4.
  `present_min_hits` 2 means a single stray read cannot flip the state.

### Smear sanity check

- The score is the vertical pixel variation divided by the horizontal one,
  within the crop:
  - Smeared (vertical-streak) frames scored ≤ 0.01.
  - Good frames and darkened copies scored ≥ 0.93.
  - Real IR night frames scored 0.75-0.86.
- Thresholds: `sanity_min_ratio` **0.2** (it was 0.5 until 0.5.0), and
  `sanity_min_h` 0.02 for a uniform (dead) feed.
- Why 0.2 (2026-10-06, new 5 MP camera, tight crop around the bin):
  - With the bin present the score was 0.83-1.07.
  - With the bin out overnight it was about 0.59.
  - At sunrise, with long shadows over the empty crop, it fell to 0.39-0.47.
    Every frame was discarded and the bin read *unknown* for 2.5 hours (from
    06:53 to 09:23) instead of *absent*.
  - Real smear scores ≤ 0.01, so 0.2 still rejects it with a wide margin.

### Night/IR (development camera)

- Day crop contrast is about 65, against about 14 at night under IR.
- The first night (2026-10-01/02) gave about 800 checks, all present. 60
  checks hit 4/5 frames and 9 hit 3/5; day checks were always 5/5.
- A daytime IR test (brightness 96 → 56, contrast 67 → 47, tag 48 px)
  gave 5/5.

## Rotation (0.4.3)

- The raw image-plane angle is wrong under perspective: a 90° turn showed as
  117-119° and 270° as about 295°, while 180° was fine.
- The method used: map the current corners through the inverse of the
  reference homography (reference corners to the unit square) onto the lid
  plane, then take the mean rotation of the four corner vectors against the
  unit square.
- On real frames turned digitally in place, the error was within 1° at every
  45° step.
- Readings are combined with a circular mean over the accepted reads of a
  check, then snapped to `rotation_steps` with min(5°, step/4) hysteresis.
- The aruco corner order is fixed to the tag pattern: corner 0 is the red dot
  in `annotate()`.
- Not yet validated: a real bin physically turned 180°.

## Tag families (0.5.0)

Each object has a `tag_family` field (in `objects.json`, set in the panel's
object form; missing means tag16h5, so older files load unchanged). The ID
is validated against that object's family when it is saved, so a mismatch
can never stop the app at startup. On a shared camera, two objects clash
only if both the family and the ID match, and "known" IDs (other objects'
tags, which are not logged as ignored reads) are per family. The ID counts and grid
sizes are read from OpenCV at runtime (`family_info`), and a test pins them
against 5.0.0.93:

| Family | IDs | Grid with border | Correctable bits |
|---|---|---|---|
| tag16h5 | 30 | 6x6 | 2 |
| tag25h9 | 35 | 7x7 | 4 |
| tag36h10 | 2320 | 8x8 | 4 |
| tag36h11 | 587 | 8x8 | 5 |

- The detector parameters are the tag16h5 ones for every family. Only tag16h5
  is tuned and tested on real frames.
- With the default family, `python -m app.check ../test-frames` output was
  identical to 0.4.3, timings aside.

### Sweep results

These results are from `python -m app.sweep ../test-frames --ablation --full-frame`, run on 2026-10-04 with 19 present, 2 absent and 2 smear frames from the development camera. The real tag is about 50 px across.

**Synthetic.** Each family's tag was drawn on a lid at random positions inside the crop, over real backgrounds, 20 trials per cell. The table shows decodes by tag size (the longer diagonal, in px):

| Family, condition | 16 | 20 | 24 | 28 | 32 | 40 | 50 | 64 |
|---|---|---|---|---|---|---|---|---|
| tag16h5 plain | 13 | 16 | 18 | 20 | 20 | 20 | 20 | 20 |
| tag16h5 blur | 0 | 1 | 8 | 15 | 19 | 20 | 20 | 20 |
| tag16h5 IR-like | 15 | 18 | 19 | 20 | 20 | 20 | 20 | 20 |
| tag25h9 plain | 8 | 13 | 16 | 17 | 20 | 20 | 20 | 20 |
| tag25h9 blur | 0 | 0 | 2 | 7 | 16 | 20 | 20 | 20 |
| tag25h9 IR-like | 9 | 11 | 19 | 18 | 20 | 20 | 20 | 20 |
| tag36h10 plain | 8 | 9 | 17 | 20 | 16 | 20 | 20 | 20 |
| tag36h10 blur | 0 | 0 | 0 | 1 | 6 | 19 | 20 | 20 |
| tag36h10 IR-like | 9 | 4 | 17 | 19 | 19 | 20 | 20 | 20 |
| tag36h11 plain | 9 | 11 | 18 | 20 | 17 | 20 | 20 | 20 |
| tag36h11 blur | 0 | 0 | 0 | 1 | 13 | 19 | 20 | 20 |
| tag36h11 IR-like | 14 | 8 | 17 | 20 | 18 | 20 | 20 | 20 |

With blur, tag16h5 is reliable from about 32 px, tag25h9 from about 32-40 px, and the 6x6 families from about 40 px. That is roughly 1.25x the size of tag16h5.

**Transplant.** The family's tag was warped onto the real tag's corners in each of the 19 present frames, then shrunk about its centre:

| Family | x1 | x0.85 | x0.7 | x0.6 | x0.5 | x0.4 |
|---|---|---|---|---|---|---|
| tag16h5 | 19 | 19 | 19 | 19 | 19 | 19 |
| tag25h9 | 19 | 19 | 19 | 19 | 19 | 13 |
| tag36h10 | 19 | 19 | 19 | 19 | 18 | 7 |
| tag36h11 | 19 | 19 | 19 | 19 | 19 | 17 |

This table is optimistic, for two reasons. The pasted tag is a crisp render. And the 19 frames are near-duplicates, the same scene and the same position, so in practice they are one sample. The sweep now also has a blurred transplant variant, which has not been run in full yet.

**Phantoms.** These are decodes of any ID, with the real tag excluded:
- In the frames' crops (absent, smear, present) every family scored 0.
- Over whole frames, tag16h5 had 1 phantom in the present frames and the other families 0. This matches the gravel phantoms seen in real logs.
- On 2000 random gravel-like textures (seed 11): tag16h5 had **40**, tag25h9 **1**, tag36h10 **0** and tag36h11 **0**. 200 textures were too few to show anything, since every family scored 0.

**Ablation.** Each tuned parameter was reset to the OpenCV default, one at a time, and tested on the crisp transplant. Everything was 19/19 down to x0.5, so the differences only show at x0.4:

| Reset to default | tag16h5 | tag25h9 | tag36h10 | tag36h11 |
|---|---|---|---|---|
| (tuned) | 19 | 13 | 7 | 17 |
| perspectiveRemovePixelPerCell 4 | 18 | 9 | 6 | 16 |
| cornerRefinementMethod none | 16 | 18 | 4 | 10 |
| each of the other four | unchanged | unchanged | unchanged | unchanged |

- At x0.6 and x0.5, `perspectiveRemovePixelPerCell` 4 also cost tag36h10 (5/19 at x0.6), tag25h9 (15/19 at x0.6) and tag36h11 (14/19 at x0.5).
- So 8 pixels per cell and the AprilTag corner refinement are worth keeping for every family. tag25h9 at x0.4 is the only case where the refinement hurt.
- No setting changed the texture phantom count. That ablation used only 200 textures, so it is not conclusive.

**Conclusions:**
- The tag16h5 parameters are a reasonable default for every family.
- The larger families trade about 1.25x the printed size for far fewer phantoms.
- No family is called supported until it has been tested on real frames (see below).

### Still needed before calling any other family "supported"

Print a tag25h9 and a tag36h11 tag at the same size as the bin's tag16h5 tag,
place them on the lid, and capture day and IR-night frames into
`test-frames/<family>/{present,absent}`. Then run `app.check --family` and
`app.sweep` on them. Until then the docs say "untested on a real camera".

## Access codes (0.5.0)

Purpose: a visitor presses the doorbell and holds up a code, TagSense
verifies it and emits an event. **TagSense never unlocks anything**: Home
Assistant automations decide. The app holds no lock credentials and has no
path to any lock.

### Separation from the bin sensor

- `app/access/` is imported by `main.py` only when `access_enabled` is on.
  A test checks that no `app.access*` module is loaded otherwise.
- It shares no state with the bin logic:
  - its own store (`/data/access/`, mode 0700),
  - its own frame sources and threads (one `Scanner` per camera, even when a
    bin object uses the same camera),
  - its own MQTT connection.

  It reuses only stateless helpers: the frame fetchers, JPEG decoding and
  crop maths.
- If it cannot start (missing login, broker refused), it stays off and the
  panel shows why. The bin sensor runs regardless.
- Off: no access connection, no subscriptions, and `/api/access/*` returns
  404.

### MQTT and the threat it answers

- **The shared login.** The Supervisor's MQTT login (`addons`) is shared by
  every app. Anything that can publish to an access event topic can fake a
  "verified" event, so access uses a **separate connection with its own
  required login** (`access_mqtt_username`/`access_mqtt_password`, client ID
  `tagsense-access`). It refuses to run with the same username as the main
  connection.
- **Broker ACLs do not work.** The plan was a Mosquitto ACL letting only that
  login write these topics. Tested on 2026-10-05, it is ineffective:
  - The Home Assistant Mosquitto app (7.x) authenticates through go-auth with
    an HTTP backend whose `/superuser` and `/acl` endpoints are its own nginx
    returning 200 for every user. So every user is a superuser, and the ACL
    file is never consulted.
  - A fake `verified` event was published and received with Home Assistant's
    own login, an HA-user login (`mqtt_user`) and a broker-only login
    (`acltest`) that had explicit `topic deny` lines.
  - The app's docs also require `homeassistant` and `addons` to have
    unrestricted access.
  - **Consequence:** any MQTT client can forge an access event. Hence
    confirm-back (below).
- **Confirm-back** (`confirm.py`, the `/api/confirm/<id>` route in
  `web.py`):
  - Each `verified` event carries `event_id` (128-bit random, from
    `secrets`).
  - Home Assistant's `rest_command` POSTs it with `Authorization: Bearer
    <token>`. The token is 256-bit, stored in `keys.json` and shown to
    admins.
  - The ID is confirmed **once**, **within 30 s**, then forgotten. It lives
    in memory only, so a restart confirms nothing.
  - The route is reachable only from `172.30.32.1` (HA Core's address on the
    hassio network) and is checked before the ingress-only rule. The address
    is not trusted alone: host-network apps share it, hence the token,
    compared in constant time.
  - It is not reachable through ingress (no route there), so a panel user
    can't confirm either.
  - Tests cover: once only, expiry, wrong or missing token, a wrong address,
    the ingress path, and token rotation.
  - Verified live on 2026-10-05 (0.5.0b9, development install) with a
    `rest_command` and an `event.received` automation:
    - A real pass scanned at the door was confirmed, and the automation ran.
    - A fake `verified` event published with `mosquitto_pub` (made-up
      `event_id`) triggered the automation but was refused by TagSense, and
      the automation stopped at its condition.
- **Topics:**
  - `tagsense/access/availability` (LWT)
  - `<sid>/event` (event entity; **never retained**, so a restart cannot
    replay it)
  - `<sid>/image` (not retained)
  - `<sid>/scanning` (retained)
  - `<sid>/cmd/scan` (button)

  A retained message on a command topic is ignored, so a stale retained
  press can never start a scan.
- **HA's own login** (`homeassistant`) keeps read/write on everything: it
  must press Scan and read events, and Mosquitto's `deny` cannot be
  write-only. HA is trusted anyway, since it is what controls the lock.

### Scanning (stage 2)

- **Trigger:** the Scan button (from a doorbell automation) or the panel
  opens a window of `window_s` (5-120 s, default 20). A press during an open
  window extends it. Frames are fetched `frame_interval_s` apart (default
  0.3 s) and decoded in the scanner's crop. There is no continuous scanning.
- **Decoding:** crop, grey, then **ZXing** (`zxing-cpp` 3.1.1, Apache-2.0),
  QR and Micro QR. See "Choosing the decoder" below.
- **End of the window:** it closes at the first read. Stage 3 will close it
  on a verified code instead.
- **The payload never leaves the scanner:**
  - Events and logs carry its length and an 8-hex fingerprint (the start of
    its SHA-256).
  - Images published to HA, and the panel's live frame, have the decoded code
    blacked out (the quad grown 1.25x).
  - Raw frames are kept only with the per-scanner `debug_frames` setting:
    the last 50, deleted after 24 h, in `/data/access/debug/`.

### Frame capture

go2rtc's `api/frame.jpeg` converts on every request: it waits for the next
keyframe, then decodes it to JPEG (in ffmpeg). Measured on 2026-10-05 against
the development go2rtc, with a 5 MP Reolink RLC-510WA (2560x1920, H.264, pulled
by go2rtc over HTTP-FLV, on Wi-Fi):

| Method | First frame | After that |
|---|---|---|
| `frame.jpeg` | 5-6 s | 5-6 s every frame (about 20 s through the panel) |
| `api/stream.mp4` held open (cold: nobody watching) | 6-8 s | about 20 decoded/s, about 7 checked/s at full frame |
| the same, warm (stream already running, e.g. in Frigate) | 1.3-3.0 s | same |
| `rtsp://<host>:8554/<stream>` | 404 from this go2rtc; not pursued | |

So a scan holds the stream open (`app/access/stream.py`): a reader thread
decodes continuously (OpenCV's bundled FFmpeg) and keeps only the newest
frame, and the scan loop checks the newest frame each time. The backlog is
never processed. The cold start is the FLV source starting in go2rtc plus the
keyframe wait; the warm start is the keyframe wait alone. Checking at about
7/s is limited by the QR decode on a whole 5 MP frame, so a tighter scan area
checks more often. If the stream does not open or ends, the rest of the
window uses snapshots, and a snapshot fetch never runs more than 2 s past the
end of the window. HA cameras always use snapshots (camera_proxy).

### Choosing the decoder

OpenCV's `QRCodeDetector` and `QRCodeDetectorAruco` (with a 2x upscale) were
used first. They were replaced by ZXing on 2026-10-05, after real frames from
the development Reolink (the sub stream, 896x672) of a phone held up to the
camera:

| | OpenCV (both detectors, 2x) | ZXing |
|---|---|---|
| A frame where the bright screen bled into the dark modules (the finder squares lost their 1:1:3:1:1 proportions), easily read by online readers | not even detected | read, 1 ms |
| 50 consecutive scan frames of a short code | 0 read | 13 read |
| Time per frame (real frames) | median 21 ms | median 1 ms |
| Micro QR (pinned 5.0.0.93) | not read even when clean | read |

ZXing then OpenCV as a fallback read no extra real frames, so the app uses
ZXing alone. In the synthetic sweep below OpenCV did better in some cells,
notably an artificial diagonal glare band that ZXing never read. Real frames
take precedence, but if real glare failures appear, adding the OpenCV
fallback is a one-line change in `QrDecoder` (the sweep compares both).

Micro QR would not help: the largest (M4, 17 modules) holds at most 21
letters/digits, too few for a signed code with an 80-bit tag. Signed codes
target QR version 1 (21 modules, 25 letters/digits at low error correction),
or version 2 at most.

### QR reading: measurements

`python -m app.sweep --qr` uses synthetic 1280x720 frames with a tilted
code, 10 trials per cell, on 2026-10-04:

| Condition | 2 px/module | 2.5 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| plain | 6-10/10 | 6-8 | 8-10 | 10 | 10 |
| blur (σ 1) | 0 | 0 | 7-9 | 10 | 10 |
| dim (IR-like) | 6-9 | 7-10 | 10 | 10 | 10 |
| screen glare | 0 | 0 | 3 | 4-5 | 9 |

(Ranges cover a 25-character payload, QR version 1, and a 45-character
payload, version 2.)

- The decode takes a median of 38 ms on a whole frame.
- A code therefore needs about 4-5 px per module, i.e. about 100-130 px
  across in the frame.
- **Glare is the limiting case**, which is why the docs tell visitors to turn
  the screen brightness up and tilt the phone.
- Keeping signed payloads within version 2 matters.
- **Not yet measured on the real doorbell:** distance, day and night. Use
  `debug_frames` to collect `test-frames/qr/`, then run `app.sweep --qr
  ../test-frames`.

### Signed codes (stage 3)

**Formats** (`app/access/codes.py`): both use only Base32 characters (in the
QR alphanumeric set), fit QR version 2 at error correction M (25 modules), and
read back with ZXing in tests:
- rotating, 23 chars: `TR` + version + handle(4) + MAC(16), with
  MAC = HMAC-SHA256(person secret, `TR|v|handle|step`) truncated to 80 bits,
  step = unix/30. The step is not in the code.
- static, 34 chars: `TS` + version + code_id(8) + expiry(7, minutes since
  2025-01-01) + MAC(16), with MAC = HMAC-SHA256(static key,
  `TS|v|id|expiry`) truncated to 80 bits. A server-side record (label,
  `valid_from`, uses left, revoked) is also required.

**Store** (`store.py`): `/data/access/{keys,people,static,state}.json`,
mode 0600 in a 0700 directory, written tmp + fsync + rename. A write failure
raises.

**Verifier** (`verifier.py`), the only place a code is judged, in this order:
1. Clock before 2025: `unavailable`.
2. Scanner locked: `locked_out`, without parsing the code.
3. Not our format: `unrecognised`.
4. Rotating:
   - an unknown handle, a disabled person or the wrong version gives
     `invalid`;
   - the MAC is checked in constant time for step and step-1 only;
   - a step at or below the last one used gives `replayed`;
   - otherwise the new last step is persisted and the result is `verified`.
5. Static:
   - the key version and MAC are checked;
   - the record must exist, not be revoked, and match the code's version and
     expiry, otherwise `revoked`;
   - before the start gives `not_yet_valid`, which does not count towards
     lockout;
   - after the expiry gives `expired`;
   - no uses left gives `replayed`;
   - otherwise the decrement is persisted and the result is `verified`.

Any exception, including a failed write, gives `unavailable`. Lockout counts
`invalid`, `expired`, `replayed` and `revoked`: more than 3 distinct bad codes
in a window, or more than 5 in 10 minutes, locks the scanner for 15 minutes.
The lockout is persisted and cleared only in the panel. A locked scanner
doesn't fetch frames at all.

**Scan window** (`scanner.py`):
- ZXing returns every code in a frame, and each distinct code is judged once
  per window.
- The window ends at `verified`, `locked_out` or `unavailable`.
- Foreign and `invalid` codes are reported by fingerprint only.
- Images black out every code, and debug frames black out TagSense codes.
- **Auto capture:** HA camera snapshots until the go2rtc stream's first
  frame, then the stream.

**Issuing** (`issue.py`, admin panel only): static codes need an expiry or
uses; with no expiry, a backstop of 90 days (adjustable, at most 365)
applies; there is an optional start; codes are valid for at least 10 minutes.
Passes are bound to an HA user ID; re-enrolling bumps the secret's version.

**Who is using the panel** (`users.py`, `web.py`):
- Ingress adds `X-Remote-User-Id`. `web.py` only accepts the ingress proxy's
  address, so that header can't be forged by anyone else.
- Admin status comes from Core's WebSocket `config/auth/list` (Supervisor
  token, cached 60 s): the owner or the `system-admin` group.
- `panel_admin: false`. One guard before any route: only `whoami`,
  `pass_info` and `pass_png` are open to non-admins, so new routes are
  admin-only by default.
- A missing header, a failed lookup or an unknown user all mean "not an
  admin". A test walks every route as a non-admin and anonymously.
- `trust_admin` exists only as a code parameter for tests and demos.
- Verified on a real install (0.5.0b7) on 2026-10-05.

**Home Assistant side:** an event entity's state (its last event time) is
restored when HA or TagSense restarts, so the documented automation ignores
changes from `unavailable`/`unknown` and requires the event to be under 30 s
old.

**Threat model:**
- Forged or altered codes fail the MAC.
- Replay is limited by single-use pass steps and static uses or expiry.
- A forwarded static code can't be prevented, only limited (expiry, uses,
  revoke).
- Fake MQTT events **cannot** be prevented at the broker (see "Broker ACLs
  do not work"). Automations that matter confirm each event back to TagSense
  (one-time ID, 30 s, token, HA Core's address only); plain notifications
  accept the risk.
- TagSense holds no lock credentials.
