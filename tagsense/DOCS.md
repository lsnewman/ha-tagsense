# TagSense

TagSense detects whether a tagged object (a wheelie bin, a car, a chair, ...)
is in place from a printed AprilTag (family **tag16h5** by default) stuck on it. It grabs camera frames, checks
them, and publishes **present / absent / unknown** to Home Assistant via MQTT
discovery. Each frame check takes about 40 ms of CPU on a cropped region.

## Before you rely on it

- **Run it in shadow mode first.** Keep your existing detector (for example a
  Frigate classifier) running alongside TagSense for a few weeks, including
  the situations you care about (night, rain, the times the object moves). Compare the two before you base automations on TagSense
  or retire the other detector.
- **Night/IR is tested on one camera.** IR night frames worked on the
  development camera (lower contrast, 3-5 of 5 frames hit). Rain, fog and
  headlight glare have not been specifically tested. Watch the *Crop
  brightness* and *Crop contrast* diagnostics next to any missed detections
  at night.
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
object**, give it a name, the tag family and ID printed on it, and the camera that sees
it (a go2rtc stream or a Home Assistant camera). Then draw its search area on
the live frame. Changes apply straight away.

- The **ID** is made from the name unless you type one, and cannot be changed
  later. Renaming keeps the ID, so the entities stay the same.
- Objects on the same camera need different tag IDs, and share each burst of
  frames.
- **Fallback** (per object): if the main source fails, the other one is tried,
  either the HA camera entity for a go2rtc object, or the go2rtc stream for a
  camera object.
- **Print tag** on the object's page: PNG for paper, or SVG in millimetres for
  a cutter or two-colour 3D print. The white margin can be turned off if the
  tag sits on a light surface.
- **Fit to tag** in the search-area editor sets the box to the learned tag
  position, plus 3x the tag size on each side. It still needs *Save*.
- **Export / import** (bottom of the overview): all objects and their
  settings as text. Importing adds new objects and updates ones with the same
  ID; objects not in the text are kept.
- **Last 24 hours** chart and **State changes** snapshots are on each
  object's page.
- **Rotation:** the search-area editor draws an arrow from the tag's centre
  through its top edge (solid green: now; dashed cyan: at 0°). **Set current
  orientation as 0°** and **Rotation steps** are in the Settings card.

### Global options

| Option | Default | Notes |
|---|---|---|
| `go2rtc_url` | *(empty)* | e.g. `http://<frigate-hostname>:1984`. The Frigate app's hostname is on its app page, and it changes if Frigate is reinstalled under another slug. Required if any object uses go2rtc. |
| `max_aspect` | `2.0` | Shape gate: a read of the object's tag whose longest/shortest edge ratio is above this is rejected (gravel phantoms decode as slivers, measured 2.2-6; the real tag about 1.5 by day). 0 disables it. Use the *Tag aspect* sensor's history to set it. |
| `min_size_ratio` | `0.5` | Shape gate: once 10 sightings are learned, a read of the object's tag smaller than this fraction of its usual size is rejected. 0 disables it. |
| `burst_size` | `5` | Frames per check |
| `burst_interval_s` | `1.0` | Seconds between frames in a burst |
| `present_min_hits` | `2` | Frames in one burst that must decode the tag to report present. 2 stops a single stray read flipping the state. |
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
  `area_px`, `aspect`, `source`.
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
- **Tag rejected** (problem binary sensor, diagnostic): on after 3 checks in a
  row in which the tag was read but every read was rejected by the shape gate
  (those checks count as misses). A Home Assistant notification with the
  reason, and what to change if the object really is there, appears at the
  same time. Both clear by themselves after a check without rejected reads.
- **Reset learned position** (button): forget the learned usual size and
  position, e.g. after moving the object further from the camera. The size
  gate and warnings stay off until 10 new hits are learned.
- **Rotation** (sensor, °): how far the tag is turned from its 0° position,
  clockwise as seen by the camera, stepped by *Rotation steps*. Attributes:
  `angle` (the exact angle, 0-360) and `steps`. Measured on the tag's own
  surface, so perspective does not distort it; all accepted reads in a check
  are averaged, and the value moves to a new step only once it is 5° past the
  halfway point. It keeps its last value between sightings.
- **Rotation steps** (number, 1-36, default 4): 4 = 0/90/180/270°, 8 = 45°
  steps, 1 = always 0 (effectively off).
- **Set current orientation as 0°** (button): the way the tag is turned now
  becomes 0°. Until it is pressed, 0° comes from the first sighting, and
  *Reset learned position* forgets it too.
- The three rotation entities are **unavailable** while the object is not
  present.
- **Enabled** (switch) and **Poll interval** (number, seconds; 0 = manual only,
  otherwise at least 10).
- **Crop x1/y1/x2/y2** (numbers, 0–1): the normalised region searched for the
  tag. It must cover everywhere the object might be. The default is
  `0.65, 0.45, 1.0, 1.0`.
- **Search area**: easiest to set in the TagSense panel by dragging a box on a
  live frame. The crop numbers below are the same setting.
- **Last crop** (image): the latest crop annotated with the tag outline, the
  ID and size, and a red dot on corner 0. Any other tag ID decoded during the
  burst (`ignored`: no object on this camera uses that ID), or a decode of the target ID rejected by the
  shape gate, is outlined in orange. To tune the crop, change a crop
  number, press *Check now*, and look at the image.
- **Diagnostics**: last source, frame resolution, last error,
  warning (the tag decoded far from the usual size or position, which may be
  a phantom; "usual" is learned from past hits, warnings start after 10 of
  them, and the learned values are attributes of this sensor), fetch time, detect time, fetch failures, discard rate, unique
  frames (duplicates mean the source served a stale frame), discarded decodes
  (ignored other tag IDs and rejected reads this check; attributes list where,
  plus the last one ever seen), tag size, tag aspect (the worst edge ratio of
  the accepted reads; compare with `max_aspect`), sanity
  ratio, miss streak, crop brightness and contrast.

## Rotation: "has it been turned round?"

There is no built-in "turned" sensor: what counts as turned is up to you.
For example, a template binary sensor (Settings > Devices & services >
Helpers > Template, or YAML):

```yaml
template:
  - binary_sensor:
      - name: "Bin turned round"
        state: "{{ is_state('sensor.tagsense_bin_rotation', '180') }}"
        availability: "{{ has_value('sensor.tagsense_bin_rotation') }}"
```

Or trigger an automation on the *Rotation* sensor changing to `180`.

The tag must stay visible in every orientation you want to tell apart, so
put it on the lid. A tag on one side of the object disappears when it is
turned 180°; that would need a second tag with another ID, as a second
object, on the opposite side.

## How a check decides

1. Fetch up to `burst_size` frames. If the object is already present and the
   first `present_min_hits` frames all hit, the rest are skipped. Each frame is decoded and scored for smearing.
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
   A read rejected by the shape gate is not the tag, so a check whose only
   reads were rejected is a miss too. Three such checks in a row also raise
   the *Tag rejected* alert, in case the gate is rejecting the real tag.

State and settings are stored in `/data` and survive restarts.

## Tag families

The detector settings were tuned on real frames for **tag16h5 only**. The
other families use the same settings and are **untested on a real camera**.
They have passed synthetic tests only (see `SPEC.md` in the repository).

- **Smaller grid (tag16h5, 4x4 data cells):** big cells, so it decodes at the
  smallest size and with the most blur. The cost is a low Hamming distance:
  random texture (gravel, foliage) decodes as a valid tag more often. The
  ID filter and shape gate are there because of this.
- **Bigger grids (tag25h9 5x5, tag36h10/36h11 6x6):** much more resistant to
  phantoms, but each cell is smaller at the same printed size. Print them
  bigger: in the synthetic tests, the 6x6 families needed about 1.25x the tag
  size of tag16h5 to decode as reliably when blurred.
- tag36h11 is the usual choice in robotics. tag36h10 has more IDs, but less
  error resistance.

The family is chosen **per object**, in the panel's object form, next to the
tag ID. The default is tag16h5. The tag ID list follows the family (IDs
0-29 for tag16h5, 0-34 for tag25h9, 0-2319 for tag36h10, 0-586 for tag36h11),
and *Print tag* prints in the object's family. Objects with different
families can share a camera, even with the same ID. If you switch an
existing object to a new tag, press *Reset learned position* once the new
tag is in place.

## Tag and print notes

- Use the object's family (tag16h5 by default), with a white quiet zone
  about as wide as the black border. A matte finish is best.
- A larger print is the main way to gain decode margin at oblique angles.
