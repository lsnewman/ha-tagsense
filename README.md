# TagSense

A Home Assistant app (formerly "add-on") that tells you whether something is
in its usual place. Stick a printed AprilTag on it, such as a wheelie bin, a
car, a chair or a garage door. TagSense grabs frames from a camera that can
see the tag, looks for it, and publishes **present / absent / unknown** to
Home Assistant through MQTT discovery. You choose what the sensor is called.

It is meant as a cheap, deterministic alternative to an image classifier
(for example a Frigate custom model) for "is the bin out?" style questions.
Each frame check is a few tens of milliseconds of CPU on a cropped region, and
it either finds your specific tag or it doesn't.

> **Status: early.** It was developed and tested with a wheelie bin on one
> camera, in daylight. Night/IR performance is not yet known. See
> [Limitations](#limitations).

## How it works

1. **Grab a burst of frames.** A check fetches several frames (default 5, one
   second apart) from go2rtc or a Home Assistant camera entity.
2. **Discard broken frames.** Some camera streams occasionally deliver smeared
   frames full of vertical streaks. TagSense scores each frame and discards
   bad ones, so a corrupt frame is never treated as "absent". A frame where
   the tag is found always counts, whatever its score.
3. **Look for the tag** in a configurable crop of the frame, using OpenCV's
   AprilTag (`tag16h5`) detector. Decodes with an implausible shape are
   rejected (see `max_aspect`). Decodes of other tag IDs are ignored, but
   logged and drawn on the debug image as "phantoms".
4. **Decide, with debounce.**
   - Seeing the tag is strong evidence, so **present** is reported straight
     away.
   - Not seeing it is weak evidence (glare, darkness, a person in the way),
     so **absent** is only reported after several clean misses in a row.
   - Checks that fail to get frames lead to **unknown**, never absent.

Checks run on a timer, on demand from a **Check now** button, or both. Pressing
Check now when the object has just been moved triggers a short series of
confirmation checks, so absent is confirmed in about a minute and a half rather
than after several polls.

## Requirements

- Home Assistant OS or Supervised (apps need the Supervisor). Built for
  **amd64**.
- The **Mosquitto broker** app with the MQTT integration. TagSense gets the
  broker login from the Supervisor automatically.
- A camera that can see the tag, available as either:
  - a **go2rtc** stream (for example the go2rtc bundled with the Frigate app,
    port 1984). This is recommended because it gives full-resolution frames.
  - a **Home Assistant camera entity**, read through the camera proxy. These
    are often lower resolution, which leaves less margin for decoding.
- A printed **tag16h5 AprilTag** (see [The tag](#the-tag)).

## Installation

1. In Home Assistant, open **Settings → Apps → App store → ⋮ → Repositories**
   and add:
   ```
   https://github.com/lsnewman/ha-tagsense
   ```
2. Find **TagSense** in the store and click **Install**. The image is built on
   your machine, which takes a few minutes the first time.
3. On the **Configuration** tab, set `object_name`, then either `go2rtc_url`
   and `go2rtc_stream`, or switch `source` to `ha_camera` and set
   `camera_entity`. Then start the app.
4. A **TagSense** device appears under the MQTT integration.

## The tag

- **Family:** `tag16h5`, any ID from 0 to 29 (default 5). This family has big
  cells, so it decodes at small sizes and steep angles. Its trade-off is that
  random textures occasionally decode as a valid ID. The ID filter and shape
  gate handle that.
- **Quiet zone:** leave a white margin around the black border, about as wide
  as the border itself. Without it the tag will not decode.
- **Finish:** matte if possible. Gloss causes glare.
- **Placement:** face the tag towards the camera if you can. If it has to lie
  flat (a bin lid has to survive the collection truck, for example), the
  camera sees it at a steep angle. Detection still works, but with less margin.
- **Size:** bigger is better. Print size and camera resolution are the main
  ways to get more decode margin. As a reference, the development setup sees
  a flat tag at about 50 px across in a 1080p frame.

Generator tools for tag16h5 are easy to find, and OpenCV can produce one with
`cv2.aruco.generateImageMarker`.

## Configuration (app options)

These are set on the app's **Configuration** tab. Changes apply when the app
restarts.

### General

| Option | Default | What it does |
|---|---|---|
| `object_name` | `Object` | What the tag is on, such as `Bin`, `Car` or `Table`. It names the main sensor ("TagSense Bin"). The device stays "TagSense". The entity ID is set from the name when the sensor is first created, and later renames do not change it. You can rename the entity in Home Assistant at any time. |

### Frame source

| Option | Default | What it does |
|---|---|---|
| `source` | `go2rtc` | Where frames come from. `go2rtc` calls `<go2rtc_url>/api/frame.jpeg?src=<go2rtc_stream>`. `ha_camera` fetches `camera_entity` through Home Assistant's camera proxy. |
| `fallback_source` | `none` | A second source to try when a fetch from the main one fails. `none` disables it. Setting it to the same value as `source` has no effect. |
| `go2rtc_url` | *(empty)* | Base URL of go2rtc, for example `http://<frigate-hostname>:1984`. With the Frigate app, the hostname is shown on its app page. It changes if Frigate is reinstalled from a different repository. **Required when either source is `go2rtc`.** |
| `go2rtc_stream` | *(empty)* | The go2rtc stream name. **Required for `go2rtc`.** List them at `<go2rtc_url>/api/streams`. Choose the highest-resolution stream that connects directly to the camera. |
| `camera_entity` | *(empty)* | The camera entity used by `ha_camera`, for example `camera.driveway`. **Required for `ha_camera`.** |

### Detection

| Option | Default | What it does |
|---|---|---|
| `tag_id` | `5` | The tag16h5 ID to look for (0–29). Every other ID is ignored, and logged as a phantom. |
| `max_aspect` | `3.0` | **Shape gate.** A decode of `tag_id` counts only if its longest edge is at most this many times its shortest edge. A square tag stays fairly square even when seen at a steep angle (about 1.5 in the development setup, with a tag lying flat), while false decodes in gravel or texture tend to be thin slivers (about 6). Lower values reject more aggressively. Higher values are more permissive. `0` turns the gate off. The test does not depend on scale, so it works for any tag size or distance. |

### Checks and decision

| Option | Default | What it does |
|---|---|---|
| `burst_size` | `5` | Frames fetched per check (1–20). More frames make a check harder to fool with one bad frame, but each check takes longer and costs more CPU. |
| `burst_interval_s` | `1.0` | Seconds between frames in a burst. Spacing them out lets a passing person, car or headlight clear the tag. `0` grabs them back to back. |
| `present_min_hits` | `1` | How many frames in one burst must contain the tag before reporting **present** (1–3). `1` responds fastest. `2` or `3` give extra protection against a one-off false decode. A check with some hits but fewer than this is *inconclusive*: the state is kept and the miss count resets. Values above `burst_size` are lowered to `burst_size`. |
| `absent_checks` | `3` | Consecutive clean misses (checks with good frames but no tag) needed before reporting **absent** (1–20). Lower reacts faster, higher is more resistant to temporary obstruction. The count resets when the tag is seen. It also resets if the previous miss is older than three poll intervals (at least 5 minutes), so misses separated by an outage cannot add up to absent. |
| `confirm_delay_s` | `45` | After **Check now** finds no tag, confirmation checks are scheduled this many seconds apart until absent is confirmed or the tag is seen. Scheduled polls never start confirmations. |
| `unknown_after_failures` | `2` | Consecutive *failed* checks (no usable frame at all) before the state becomes **unknown** (1–5). Until then the last state is kept. `1` reports outages immediately. Higher values ride out brief stream hiccups. |

### Frame sanity check

| Option | Default | What it does |
|---|---|---|
| `sanity_min_ratio` | `0.5` | Rejects smeared frames. The score is the vertical pixel variation divided by the horizontal variation within the crop. Normal scenes, dark ones included, score about 1.0. Smeared vertical-streak frames score about 0.0. Raise it to reject more aggressively, lower it if good frames are being discarded (watch *Discard rate*). |
| `sanity_min_h` | `0.02` | Rejects frames where the crop is effectively uniform, such as a black or dead feed. These count as unusable, not as "absent". Real night frames carry sensor noise well above this. |

### Other

| Option | Default | What it does |
|---|---|---|
| `log_level` | `info` | `debug`, `info`, `warning` or `error`. At `info` there is one line per check, plus one per phantom decode. |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | *(unset)* | Only needed to use a broker other than the Supervisor's Mosquitto. Leave them unset otherwise. |

## Settings you can change live (entities)

These appear on the device page and take effect immediately, without a
restart. They are stored by the app and survive restarts.

| Entity | Default | What it does |
|---|---|---|
| **Poll interval** | `60` s | How often a check runs on its own. `0` means checks only run when **Check now** is pressed. Values from 1 to 9 are raised to 10. The timer restarts after each check, so the real spacing is the interval plus the check duration. |
| **Crop x1 / y1 / x2 / y2** | `0.65 / 0.45 / 1.0 / 1.0` | The part of the frame that is searched, as fractions of width and height (0 = left/top, 1 = right/bottom). The default is the bottom-right area of the development camera, so **set it for your scene**, or use `0 / 0 / 1 / 1` to search the whole frame (slower). It must cover **every** spot where the object might be. A tighter crop is faster and has fewer places for phantoms to appear, but if it is too tight, an object moved slightly reads as absent. If x2 ≤ x1 or y2 ≤ y1, the default is used and a warning is raised. |
| **Enabled** | on | Off pauses all checks. The main sensor goes unavailable and Status shows `unknown` with reason `disabled`. |
| **Check now** | — | Runs a check immediately. It is intended for automations, for example when Frigate sees a person leave the area. |

**Tuning the crop:** change a crop value, press **Check now**, and look at the
**Last crop** image. The tag is outlined in green, and any phantoms or rejected
decodes in orange.

## Entities

| Entity | Description |
|---|---|
| **`<object_name>`** (`binary_sensor`, occupancy) | `on` = present, `off` = absent. **Unavailable** when the state is unknown, when TagSense is disabled, or when the app is not running. Attributes: `last_seen`, `size_px`, `centre`, `area_px`, `source`. |
| **Status** (enum sensor) | `present`, `absent` or `unknown`, plus a `reason` attribute: `starting`, `restored`, `tag_seen`, `inconclusive`, `pending`, `no_tag`, `fetch_failed`, `all_frames_invalid`, `disabled`. The other attributes describe the last check (trigger, frames, valid, hits). |
| **Last crop** (image) | The latest crop with annotations. Green: the tag, with its ID, size and a red dot on corner 0. Orange: phantom or rejected decodes. |
| Diagnostics | Last source, frame resolution, last check, last error, warning, fetch time, detect time, fetch failures, discard rate, unique frames, phantom decodes, tag size, sanity ratio, miss streak, crop brightness, crop contrast. |

Some diagnostics need a little explanation:
- **Warning** is set when the tag is found at an unusual size or position
  compared with what TagSense has learned for your install (see below), when a
  decode is rejected by the shape gate, or when the crop is invalid. It clears
  on the next check without a warning.
- **Last error** keeps the most recent fetch error until a newer one replaces
  it.
- **Unique frames** lower than the burst size means the source served the same
  frame more than once.
- **Phantom decodes** counts decodes of other IDs in the latest check. Its
  attributes show where they were, and the most recent one ever seen.
- **Crop brightness / contrast** help explain misses at night.

**Learned reference:** TagSense learns where your tag usually appears, and how
big, from a running average of accepted hits. This needs no setup and
works at any resolution. After 10 hits it warns when a hit is more than 40%
off the usual size, or more than 0.08 of the frame from the usual position.
That pattern often means a phantom decode of your ID. It only warns and never
rejects anything. If the object's spot changes permanently, the average follows
it within about 20 hits. The *Warning* sensor's attributes show what has been
learned.

## Limitations

- **Night/IR is untested.** All tuning used daytime frames, plus synthetic
  darkened copies. Watch Crop brightness and Crop contrast next to any missed
  detections at night.
- **Thin decode margin.** A small tag seen at a steep angle is near the limit
  of what the detector can read. A larger print helps more than any setting.
- **Tested on one setup:** a TP-Link Tapo camera through Frigate's go2rtc,
  with a wheelie bin and one tag lying flat on its lid. Other objects and
  placements should work, but have not been tested.
- **Phantoms.** tag16h5 trades error-resistance for small size. The ID filter
  and shape gate protect the decision, but a false decode of your exact ID with
  a plausible shape is still possible. Phantoms are logged and drawn so you can
  see them.
- **Run it in shadow mode first.** If it replaces an existing detector, run both
  side by side for a few weeks, covering the situations you care about
  (night, rain, the times the object actually moves), before relying
  on it.

## Development

```sh
cd tagsense
pip install -r requirements.txt pytest
python -m pytest tests
python -m app.check ../test-frames --save-annotated /tmp/annotated
```

`app/check.py` runs the detector and sanity check over a folder of frames. It
expects subfolders named `present/`, `absent/` and `smear/` (or `corrupt/`), and
flags misses, phantoms and corrupt frames that were not rejected. The tests
that use real frames read `../test-frames` or `$TAGSENSE_TESTDATA`, and are
skipped if the folder is missing. Real frames are not committed to the repo.

Releasing: bump `version` in `tagsense/config.yaml`, or Home Assistant will not
offer the update.

## AI usage disclaimer

This project was built with substantial help from AI, and you should weigh
that when deciding whether to rely on it.

- **Design and early tuning:** the approach, the tag family choice and the
  first detector settings were worked out in conversation with an AI
  assistant. The maintainer ran test scripts on real camera frames and fed the
  results back. Several assumptions made by the AI during that phase were
  later shown to be wrong by measurement. One example: contrast enhancement
  (CLAHE) was expected to help and in fact hurt. The settings that shipped are
  the ones that held up in testing.
- **Code, tests and documentation:** the source code, test suite and
  documentation, including this README, were written by an AI coding agent
  (Anthropic's Claude, through Claude Code). The maintainer directed the work.
- **What the maintainer did:** set requirements and constraints, made or
  approved the design decisions and trade-offs, reviewed the implementation
  plan in several rounds, supplied the real camera frames used for
  calibration, and installed and tested each release on their own Home
  Assistant system.
- **How the numbers were checked:** thresholds such as the sanity ratio and
  the shape gate were set from measurements on real frames (on a small set,
  daytime only), not from the AI's reasoning alone. The test suite runs
  against both synthetic and real frames.
- **What has not been done:** there has been no independent human code review
  or security review. Testing covers one camera and daytime conditions.

Treat it as experimental software. Read the code before you rely on it for
anything that matters.

## Licence

MIT. See [LICENSE](LICENSE).
