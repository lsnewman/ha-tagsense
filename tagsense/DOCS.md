# TagSense

TagSense detects whether a tagged object (a wheelie bin, a car, a chair, ...)
is in place from a printed AprilTag (family **tag16h5**) stuck on it. It grabs camera frames, checks
them, and publishes **present / absent / unknown** to Home Assistant via MQTT
discovery. Each frame check takes about 40 ms of CPU on a cropped region.

## Before you rely on it

- **Run it in shadow mode first.** Keep your existing detector (for example a
  Frigate classifier) running alongside TagSense for a few weeks, including
  the situations you care about (night, rain, the times the object moves). Compare the two before you base automations on TagSense
  or retire the other detector.
- **Night/IR calibration is provisional.** The sanity thresholds and detector
  settings were tuned on daytime frames, plus synthetic darkened copies. Real
  IR, dusk/dawn, headlight, glare and rain frames have not been tested. Watch
  the *Crop brightness* and *Crop contrast* diagnostics next to any missed
  detections at night.
- **Compare the CPU cost.** Each check fetches `burst_size` frames and runs
  detection on the crop. Use the *Fetch time* and *Detect time* diagnostics,
  plus the app's CPU graph, to compare against what it replaces.

## Requirements

- The MQTT integration with the Mosquitto broker app. Credentials are fetched
  from the Supervisor automatically.
- A frame source:
  - **go2rtc** (recommended): for example, the go2rtc bundled with the Frigate
    app on port 1984. Give the full stream resolution.
  - **ha_camera**: any HA camera entity, through the camera proxy. This is
    often lower resolution (Frigate's detect stream), so it has less decode
    margin.

## Configuration

### Objects

Objects are managed in the **TagSense panel** in the sidebar: click **Add
object**, give it a name, the tag ID printed on it, and the camera that sees
it (a go2rtc stream or a Home Assistant camera). Then draw its search area on
the live frame. Changes apply straight away.

- The **ID** is made from the name unless you type one, and cannot be changed
  later. Renaming keeps the ID, so the entities stay the same.
- Objects on the same camera need different tag IDs, and share each burst of
  frames.
- The `objects` option on this tab is only used once, to import objects on
  the first start of 0.4.0. After that it is ignored.

### Global options

| Option | Default | Notes |
|---|---|---|
| `go2rtc_url` | *(empty)* | e.g. `http://<frigate-hostname>:1984`. The Frigate app's hostname is on its app page, and it changes if Frigate is reinstalled under another slug. Required if any object uses go2rtc. |
| `fallback_source` | `none` | Used when an object's primary fetch fails |
| `max_aspect` | `3.0` | Shape gate: a decode of `tag_id` whose longest/shortest edge ratio is above this is rejected (gravel phantoms decode as thin slivers). The tag seen obliquely measured about 1.5. 0 disables it. |
| `burst_size` | `5` | Frames per check |
| `burst_interval_s` | `1.0` | Seconds between frames in a burst |
| `present_min_hits` | `1` | Frames in one burst that must decode the tag to report present |
| `absent_checks` | `3` | Consecutive clean-miss checks before reporting absent |
| `confirm_delay_s` | `45` | Delay between confirmation checks after a *Check now* miss |
| `unknown_after_failures` | `2` | Consecutive checks with no usable frame before reporting unknown |
| `sanity_min_ratio` | `0.5` | Smear threshold (see below) |
| `sanity_min_h` | `0.02` | Below this the crop is uniform (dead feed) and the frame is discarded |
| `mqtt_*` | | Optional overrides. Leave unset to use the Supervisor's broker. |

## Entities

Each object is its own device, **TagSense `<name>`**, with these entities:

- **TagSense `<name>`** (`binary_sensor`, occupancy): on = present, off = absent. It is
  **unavailable** when the state is unknown, when TagSense is disabled, or
  when the app is not running. Attributes: `last_seen`, `size_px`, `centre`,
  `area_px`, `source`.
- **Status** (enum sensor, diagnostic): `present` / `absent` / `unknown`. The
  `reason` attribute is one of:
  - `starting`: no state yet
  - `restored`: state carried over from before a restart, until the first good check
  - `tag_seen`
  - `inconclusive`: fewer hits than `present_min_hits`
  - `pending`: misses seen, but not yet enough for absent
  - `no_tag`: absent confirmed
  - `fetch_failed`
  - `all_frames_invalid`
  - `disabled`
- **Check now** (button): runs a check immediately. If the tag is not found,
  confirmation checks follow every `confirm_delay_s` until the object is
  confirmed absent or the tag is seen. Automations (for example, a Frigate
  person-left-zone event) only need to press this button.
- **Enabled** (switch) and **Poll interval** (number, seconds; 0 = manual only,
  otherwise at least 10).
- **Crop x1/y1/x2/y2** (numbers, 0–1): the normalised region searched for the
  tag. It must cover everywhere the object might be. The default is
  `0.65, 0.45, 1.0, 1.0`.
- **Search area**: easiest to set in the TagSense panel by dragging a box on a
  live frame. The crop numbers below are the same setting.
- **Last crop** (image): the latest crop annotated with the tag outline, the
  ID and size, and a red dot on corner 0. Any other tag ID decoded during the
  burst (a phantom candidate), or a decode of the target ID rejected by the
  shape gate, is outlined in orange. To tune the crop, change a crop
  number, press *Check now*, and look at the image.
- **Diagnostics**: last source, frame resolution, last check, last error,
  warning (the tag decoded far from the usual size or position, which may be
  a phantom; "usual" is learned from past hits, warnings start after 10 of
  them, and the learned values are attributes of this sensor), fetch time, detect time, fetch failures, discard rate, unique
  frames (duplicates mean the source served a stale frame), phantom decodes
  (other tag IDs decoded this check; attributes list where, plus the last one
  ever seen), tag size, sanity
  ratio, miss streak, crop brightness and contrast.

## How a check decides

1. Fetch `burst_size` frames. Each frame is decoded and scored for smearing.
   The score is the ratio of vertical to horizontal pixel differences in the
   crop. Corrupt "vertical streak" frames score about 0, real scenes (dark
   ones included) about 1. Smeared, flat and undecodable frames are discarded.
   **A tag hit always counts**, even if the score is low.
2. If no frame is usable, the check *failed*. The state is unchanged until
   `unknown_after_failures` consecutive failed checks, and then it is
   reported as unknown. **A missing or bad frame never produces "absent".**
3. If enough frames hit, the state is present. A hit count below
   `present_min_hits` is *inconclusive*: the state is kept and the miss streak
   resets.
4. A check with usable frames and no hit is a clean miss. Absent is reported
   after `absent_checks` consecutive misses. The streak resets if the last
   miss is older than 3 poll intervals (at least 5 minutes), so an outage
   between misses cannot add up to "absent".

State and settings are stored in `/data` and survive restarts.

## Tag and print notes

- Use tag16h5, with a white quiet zone about as wide as the black border.
  A matte finish is best.
- A larger print is the main way to gain decode margin at oblique angles.
