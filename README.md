# TagSense

[![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flsnewman%2Fha-tagsense)
[![Open TagSense in my Home Assistant](https://my.home-assistant.io/badges/supervisor_addon.svg)](https://my.home-assistant.io/redirect/supervisor_addon/?addon=744e9206_tagsense)

A Home Assistant app (formerly "add-on") that tells you whether something is
in its usual place. Stick a printed AprilTag on it, such as a wheelie bin, a
car, a chair or a garage door. TagSense grabs frames from a camera that can
see the tag, looks for it, and publishes **present / absent / unknown** to
Home Assistant through MQTT discovery. One app can watch several tagged
objects, on one or more cameras, and each appears as its own device. It also
reports how far each tag is turned, so you can tell when a bin comes back the
wrong way round. A
**TagSense panel** in the sidebar is where you add objects and draw each
one's search area on a live camera frame.

![The TagSense panel: drag the search area on a live frame, with the last check beside it](docs/panel-object.png)

It is meant as a cheap, deterministic alternative to an image classifier
(for example a Frigate custom model) for "is the bin out?" style questions.
Each frame check is a few tens of milliseconds of CPU on a cropped region, and
it either finds your specific tag or it doesn't.

> **Status: early.** It was developed and tested with a wheelie bin on one
> camera, by day and at night under the camera's IR. It has had one user. See
> [Limitations](#limitations).

## How it works

1. **Grab a burst of frames.** A check fetches several frames (default 5, one
   second apart) from go2rtc or a Home Assistant camera entity. When several
   objects share a camera, one burst serves all of them, and each object looks
   for its own tag in its own crop.
2. **Discard broken frames.** Some camera streams occasionally deliver smeared
   frames full of vertical streaks. TagSense scores each frame and discards
   bad ones, so a corrupt frame is never treated as "absent". A frame where
   the tag is found always counts, whatever its score.
3. **Look for the tag** in a configurable crop of the frame, using OpenCV's
   AprilTag detector (`tag16h5` by default; see [Tag families](#tag-families)). Decodes with an implausible shape are
   rejected: too elongated (`max_aspect`) or far smaller than usual
   (`min_size_ratio`). Decodes of other tag IDs are ignored, but
   logged and drawn on the debug image as `ignored: id N`.
4. **Decide, with debounce.**
   - Seeing the tag is strong evidence, so **present** is reported straight
     away.
   - Not seeing it is weak evidence (glare, darkness, a person in the way),
     so **absent** is only reported after several clean misses in a row.
   - Checks that fail to get frames lead to **unknown**, never absent.
   - Reads rejected by the shape gate count as misses. If that happens in
     three checks in a row, an alert explains why, in case the real tag is
     being rejected.
   - When the object is already present and the first frames all hit, the
     rest of the burst is skipped, so most checks fetch only
     `present_min_hits` frames.
5. **Measure the rotation.** While the tag is seen, a *Rotation* sensor
   reports how far it is turned from its 0° position (for example, a bin put
   back the wrong way round), measured on the tag's own surface so
   perspective does not distort it. See [Rotation](#rotation).

Checks run on a timer, on demand from a **Check now** button, or both. Pressing
Check now when the object has just been moved triggers a short series of
confirmation checks, so absent is confirmed in about a minute and a half rather
than after several polls.

## Access codes at the door (optional)

TagSense can also read QR codes from a doorbell camera. A visitor rings and
holds up a code on their phone, and TagSense reports whether it is valid:
- a rotating **pass** for Home Assistant users;
- a time-limited **static code** to send to a tradesperson.

It is off by default and needs its own MQTT login. It **never unlocks
anything**: your automations decide what a verified code does, and for a lock
they can ask TagSense to confirm each event first. See the app's
Documentation tab. A ready-made blueprint for a lock (confirm, unlock, lock
again after the auto-lock time) is one click away:

[![Import the TagSense unlock blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Flsnewman%2Fha-tagsense%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ftagsense%2Funlock_on_confirmed_code.yaml)

## Requirements

- Home Assistant OS or Supervised (apps need the Supervisor). Built for
  **amd64** and **aarch64** (Raspberry Pi 4/5).
- The **Mosquitto broker** app with the MQTT integration. TagSense gets the
  broker login from the Supervisor automatically.
- A camera that can see the tag, available as either:
  - a **go2rtc** stream (for example the go2rtc bundled with the Frigate app,
    port 1984). This is recommended because it gives full-resolution frames.
  - a **Home Assistant camera entity**, read through the camera proxy. These
    are often lower resolution, which leaves less margin for decoding.
- A printed AprilTag, **tag16h5** by default (see [The tag](#the-tag)).

## Installation

1. Click **Add repository** to add this repository to your Home Assistant:

   [![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flsnewman%2Fha-tagsense)

   Or open **Settings → Apps → App store → ⋮ → Repositories** and add
   `https://github.com/lsnewman/ha-tagsense`.
2. Find **TagSense** in the store (or click
   [![Open TagSense in my Home Assistant](https://my.home-assistant.io/badges/supervisor_addon.svg)](https://my.home-assistant.io/redirect/supervisor_addon/?addon=744e9206_tagsense))
   and click **Install**. The image is built on
   your machine, which takes a few minutes the first time.
3. On the **Configuration** tab, set `go2rtc_url` if you will use go2rtc
   streams. Then start the app.
4. Open **TagSense** in the sidebar, click **Add object**, and give it a name,
   the tag ID and the camera. Then draw its search area (see
   [The TagSense panel](#the-tagsense-panel)).
5. Each object appears as a **TagSense <name>** device under the MQTT
   integration.

## The TagSense panel

The app adds a **TagSense** entry to the Home Assistant sidebar. Only
logged-in Home Assistant users can open it.

- **Overview:** every object with its state, the reason, when it was last
  checked, the latest annotated crop, and a **Check now** button.
- **Add / edit object:** name, ID, tag ID and camera. go2rtc streams and Home
  Assistant cameras are listed for you to pick from. Changes apply straight
  away, without restarting the app.
- **Search area editor:** a live frame from the object's camera, with the
  search area drawn as a box. Drag it to move it, drag a corner to resize it,
  or drag on the image to draw a new box. The last detection (green) and the
  learned usual position (cyan) are drawn on the frame, so you can see where
  the tag actually is. **Fit to tag** sets the box to the learned position
  plus 3x the tag size on each side. Saving runs a check straight away. An
  arrow shows which way the tag's top points now (solid green) and at 0°
  (dashed cyan).
- **Settings:** Enabled, Poll interval, Rotation steps and **Set current
  orientation as 0°**. These are the same settings as the MQTT entities, so
  changes show up in both places.
- **Recent checks:** the last 50 checks since the app started (trigger,
  result, hits, tag aspect, what was reported, warnings, and ignored or
  rejected reads), plus the last ignored or rejected read with its image.
- **Last 24 hours:** a chart of the hit rate, crop contrast, tag aspect
  (against `max_aspect`) and rotation in 10-minute steps, over a band showing
  the reported state. It is kept across restarts. Hover for the details of each step.

  ![24-hour chart: hit rate, crop contrast and tag aspect, over the reported state](docs/panel-chart.png)

- **State changes:** the checked image each time the reported state or the
  stepped rotation changed (the last 20 are kept).
- **Tag rejected banner:** shown when the tag was read but rejected in 3
  checks in a row, with the reason and a **Reset learned position** button.
- **Export / import** (overview page): all objects and their settings as
  text, for a backup or another install. Importing adds new objects and
  updates ones with the same ID, and never deletes.
- **Diagnostics:** fetch and detect times, discard rate, sanity ratio, crop
  brightness and contrast, fetch errors, and how much position data has been
  learned.
- **Print tag:** the object's tag (in its family) as a **PNG** for paper, or an
  **SVG** sized in millimetres for a cutter or a 3D print. The white margin
  can be turned off. See [The tag](#the-tag).
- **Delete:** removes the object, its Home Assistant entities and its saved
  state.

## The tag

- **Family:** `tag16h5` by default, any ID from 0 to 29 (default 5). This
  family has big cells, so it decodes at small sizes and steep angles. Its
  trade-off is that random textures occasionally decode as a valid ID. The
  ID filter and shape gate handle that. Other families can be chosen per
  object in the panel, but are untested on a real camera: see
  [Tag families](#tag-families).
- **Quiet zone:** the black border needs a light area around it, about one
  cell wide (a cell is a sixth of the black square). Without it the tag will
  not decode. The printed tags include a white margin for this. Leave it off
  only if the tag will sit on a light surface, such as a white bin lid.
- **Finish:** matte if possible. Gloss causes glare.
- **Placement:** face the tag towards the camera if you can. If it has to lie
  flat (a bin lid has to survive the collection truck, for example), the
  camera sees it at a steep angle. Detection still works, but with less margin.
- **Size:** bigger is better. Print size and camera resolution are the main
  ways to get more decode margin. As a reference, the development setup sees
  a flat tag at about 50 px across in a 1080p frame.

The panel's **Print tag** card on each object's page produces:

- **PNG** for paper: print it as large as the object allows, and laminate it
  for outdoor use.
- **SVG** in millimetres: set the black square's size. It contains two
  separate, non-overlapping shapes, `black` (the tag) and `white` (the inner
  white cells, plus the margin if enabled). Import it into a slicer and give
  each shape its own filament for a two-colour 3D print, or use just the black
  shape with a vinyl cutter.

## Tag families

Each object's form in the panel has a **Tag family** choice next to the tag
ID. The default is tag16h5, and existing objects stay on it. The
detector settings were tuned on real frames for **tag16h5 only**. The others
use the same settings and have **only been tested on synthetic frames**.

| Family | IDs | Data grid | Phantom resistance | Size needed |
|---|---|---|---|---|
| `tag16h5` (default) | 0–29 | 4x4 | lowest | smallest |
| `tag25h9` | 0–34 | 5x5 | much better | a little bigger |
| `tag36h10` | 0–2319 | 6x6 | high | about 1.25x |
| `tag36h11` | 0–586 | 6x6 | highest | about 1.25x |

- A smaller grid means bigger cells, so the tag decodes when it is smaller,
  further away or blurred. It also means a lower Hamming distance, so random
  texture decodes as a valid tag more often. On 2000 random textures,
  tag16h5 produced 40 false decodes, tag25h9 1, and the 36-bit families
  none.
- A larger grid needs more pixels per cell. In the synthetic tests the 6x6
  families needed about 1.25x the tag size of tag16h5 to decode as reliably
  when blurred.

The tag ID list in the form follows the family, and *Print tag* prints in the
object's family. Objects with different families can share a camera. If you
switch an object to a new tag, press *Reset learned position* once it is in
place. The measurements and how they were made are in [SPEC.md](SPEC.md).

## Objects

Objects are managed in the TagSense panel and stored by the app in
`/data/objects.json`. Each object has:

| Field | What it does |
|---|---|
| Name | What the tag is on. It names the device ("TagSense Bin") and its main sensor. It can be changed at any time. |
| ID | A short, stable identifier (lowercase letters, digits, `_`), used in entity IDs, MQTT topics and the data folder. It is made from the name unless you type one, and **cannot be changed later**. Renaming the object keeps its ID, so its entities and history stay. |
| Tag family | The AprilTag family of the printed tag: `tag16h5` (the default, and the only one tested on a real camera), `tag25h9`, `tag36h10` or `tag36h11`. See [Tag families](#tag-families). |
| Tag ID | The tag ID on this object: 0–29 for tag16h5, 0–34 for tag25h9, 0–2319 for tag36h10, 0–586 for tag36h11. Two objects on the same camera need different tags (same family and ID). The same ID on different cameras is fine. |
| Camera | A **go2rtc stream** (fetched as `<go2rtc_url>/api/frame.jpeg?src=<stream>`; choose the highest-resolution stream that connects directly to the camera) or a **Home Assistant camera** entity, read through the camera proxy. Tick **Fallback** to try the other source when the main one fails. A go2rtc object then falls back to its Home Assistant camera entity (often lower resolution), and a Home Assistant camera object to its go2rtc stream. |

Objects on the same stream (or camera entity) share one camera worker: each
burst is fetched once and judged by all of them, each with its own crop and
tag. Different cameras are checked in parallel. The other objects' tags on a
shared camera are recognised, so they are not logged as ignored reads.

## Configuration (app options)

These are set on the app's **Configuration** tab, apply to every object, and
take effect when the app restarts.

### Frame source

| Option | Default | What it does |
|---|---|---|
| `go2rtc_url` | *(empty)* | Base URL of go2rtc, for example `http://<frigate-hostname>:1984`. With the Frigate app, the hostname is shown on its app page. It changes if Frigate is reinstalled from a different repository. **Required if any object uses `go2rtc`.** |

### Detection

| Option | Default | What it does |
|---|---|---|
| `max_aspect` | `2.0` | **Shape gate, part 1.** A read of an object's own tag counts only if its longest edge is at most this many times its shortest edge. A square tag stays fairly square even at a steep angle (1.45–1.52 in the development setup, with a tag lying flat), while phantom reads in gravel measured 2.2–6. Lower values reject more. `0` turns it off. It does not depend on scale. |
| `min_size_ratio` | `0.5` | **Shape gate, part 2.** Once 10 sightings have been learned, a read of an object's own tag smaller than this fraction of its learned usual size is rejected. Phantom reads in texture are usually tiny (15–40% of the real tag in the development setup), while a moved object rarely shrinks by half. `0` turns it off. |

### Checks and decision

| Option | Default | What it does |
|---|---|---|
| `burst_size` | `5` | Frames fetched per check (1–20). More frames make a check harder to fool with one bad frame, but each check takes longer and costs more CPU. |
| `burst_interval_s` | `1.0` | Seconds between frames in a burst. Spacing them out lets a passing person, car or headlight clear the tag. `0` grabs them back to back. |
| `present_min_hits` | `2` | How many frames in one burst must contain the tag before reporting **present** (1–3). `2` (the default) means a single stray read cannot flip the state, because a phantom rarely repeats across frames while a real tag shows in all of them. `1` responds to the faintest sighting. A check with some hits but fewer than this is *inconclusive*: the state is kept and the miss count resets. Values above `burst_size` are lowered to `burst_size`. |
| `absent_checks` | `3` | Consecutive clean misses (checks with good frames but no tag) needed before reporting **absent** (1–20). Lower reacts faster, higher is more resistant to temporary obstruction. The count resets when the tag is seen. It also resets if the previous miss is older than three poll intervals (at least 5 minutes), so misses separated by an outage cannot add up to absent. |
| `confirm_delay_s` | `45` | After **Check now** finds no tag, confirmation checks are scheduled this many seconds apart until absent is confirmed or the tag is seen. Scheduled polls never start confirmations. |
| `unknown_after_failures` | `2` | Consecutive *failed* checks (no usable frame at all) before the state becomes **unknown** (1–5). Until then the last state is kept. `1` reports outages immediately. Higher values ride out brief stream hiccups. |

### Frame sanity check

| Option | Default | What it does |
|---|---|---|
| `sanity_min_ratio` | `0.2` | Rejects smeared frames. The score is the vertical pixel variation divided by the horizontal variation within the crop. Most scenes score about 1.0, but a tight crop of an empty, streaky scene can drop to about 0.4 (seen with low morning sun). Smeared vertical-streak frames score about 0.0. Raise it to reject more aggressively, lower it if good frames are being discarded (watch *Discard rate*). Until 0.5.0 the default was 0.5. |
| `sanity_min_h` | `0.02` | Rejects frames where the crop is effectively uniform, such as a black or dead feed. These count as unusable, not as "absent". Real night frames carry sensor noise well above this. |

### Other

| Option | Default | What it does |
|---|---|---|
| `log_level` | `info` | `debug`, `info`, `warning` or `error`. At `info` there is one line per check, plus one per ignored or rejected decode. |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | *(unset)* | Only needed to use a broker other than the Supervisor's Mosquitto. Leave them unset otherwise. |

## Settings you can change live (entities)

Each object's device page has its own copy of these, and the panel shows the
same settings. They take effect immediately, without a restart, and are
stored by the app so they survive restarts.

| Entity | Default | What it does |
|---|---|---|
| **Poll interval** | `60` s | How often a check runs on its own. `0` means checks only run when **Check now** is pressed. Values from 1 to 9 are raised to 10. The timer restarts after each check, so the real spacing is the interval plus the check duration. |
| **Crop x1 / y1 / x2 / y2** | `0.65 / 0.45 / 1.0 / 1.0` | The part of the frame that is searched, as fractions of width and height (0 = left/top, 1 = right/bottom). The default is the bottom-right area of the development camera, so **set it for your scene**: the panel's search area editor is the easiest way. `0 / 0 / 1 / 1` searches the whole frame (slower). It must cover **every** spot where the object might be. A tighter crop is faster and has fewer places for phantoms to appear, but if it is too tight, an object moved slightly reads as absent. If x2 ≤ x1 or y2 ≤ y1, the default is used and a warning is raised. |
| **Enabled** | on | Off pauses all checks. The main sensor goes unavailable and Status shows `unknown` with reason `disabled`. |
| **Check now** | — | Runs a check immediately. It is intended for automations, for example when Frigate sees a person leave the area. |
| **Reset learned position** | — | Forgets the learned usual size and position. Use it after moving the object, e.g. further from the camera. The size gate and position warnings stay off until 10 new hits are learned. It also forgets the 0° rotation, which is then taken from the next sighting. |
| **Rotation steps** | `4` | 1-36. The *Rotation* sensor is rounded to this many evenly spaced values: 4 = 0/90/180/270°, 8 = 45° steps, 1 = always 0 (effectively off). Unavailable while the object is not present. |
| **Set current orientation as 0°** | — | The way the tag is turned now becomes 0°. Until it is pressed, 0° comes from the first sighting. Unavailable while the object is not present. |

**Tuning the crop:** change a crop value, press **Check now**, and look at the
**Last crop** image. The tag is outlined in green, and any phantoms or rejected
decodes in orange.

## Entities

Each object gets its own device, **TagSense `<name>`**, with the entities
below. Entity IDs follow the device, for example `binary_sensor.tagsense_bin`,
`sensor.tagsense_bin_status` and `button.tagsense_bin_check_now`.

| Entity | Description |
|---|---|
| **TagSense `<name>`** (`binary_sensor`, occupancy) | `on` = present, `off` = absent. **Unavailable** when the state is unknown, when TagSense is disabled, or when the app is not running. Attributes: `last_seen`, `size_px`, `centre`, `area_px`, `tag_id`, `source`. |
| **Status** (enum sensor) | `present`, `absent` or `unknown`, plus a `reason` attribute: `starting`, `restored`, `tag_seen`, `inconclusive`, `pending`, `no_tag`, `fetch_failed`, `all_frames_invalid`, `disabled`. The other attributes describe the last check: frames, valid, hits, and the trigger (`startup`, `poll`, `manual`, `confirm`, or `shared` when another object on the same camera requested the burst). |
| **Tag rejected** (`binary_sensor`, problem) | On after 3 checks in a row in which the tag was read but every read was rejected by the shape gate (those checks count as misses). A Home Assistant notification with the reason (too small or too skewed), and what to change if the object really is there, appears at the same time. Both clear by themselves after a check without rejected reads. |
| **Rotation** (sensor, °) | How far the tag is turned from its 0° position, clockwise as seen by the camera, stepped by *Rotation steps*. Attributes: `angle` (exact, 0-360) and `steps`. **Unavailable** while the object is not present. See [Rotation](#rotation). |
| **Last crop** (image) | The latest crop with annotations. Green: the tag, with its ID, size and a red dot on corner 0. Orange: ignored reads of other IDs and rejected reads. |
| Diagnostics | Last source, frame resolution, last error, warning, fetch time, detect time, fetch failures, discard rate, unique frames, discarded decodes, tag size, tag aspect, sanity ratio, miss streak, crop brightness, crop contrast. |

Some diagnostics need a little explanation:
- **Warning** is set when the tag is found at an unusual size or position
  compared with what TagSense has learned for your install (see below), when a
  decode is rejected by the shape gate, or when the crop is invalid. It clears
  on the next check without a warning.
- **Last error** keeps the most recent fetch error until a newer one replaces
  it.
- **Unique frames** lower than the burst size means the source served the same
  frame more than once.
- **Discarded decodes** counts the ignored reads of other IDs and the
  rejected reads of the object's own tag in the latest check. Its attributes
  show where they were, and the most recent one ever seen.
- **Tag aspect** is the worst edge ratio of the accepted reads in the latest
  check. Its history (day and night) shows how close the real tag comes to
  `max_aspect`.
- **Crop brightness / contrast** help explain misses at night.

**Learned reference:** TagSense learns where your tag usually appears, and how
big, from a running average of accepted hits. This needs no setup and
works at any resolution. After 10 hits it warns when a hit is more than 40%
off the usual size, or more than 0.08 of the frame from the usual position.
That pattern often means a phantom decode of your ID. It only warns and never
rejects anything. If the object's spot changes permanently, the average follows
it within about 20 hits. The *Warning* sensor's attributes show what has been
learned.

## Rotation

Added in 0.4.3 for bins that come back turned round. TagSense measures how
far the tag is turned on its own surface (the lid), not in the image, so
perspective does not distort it: on real frames a 90° turn measured within
1°, where the raw image angle was nearly 30° out. All accepted reads in a
check are averaged, then rounded to *Rotation steps*. A new step is only
taken once the angle is 5° past the halfway point, so it does not flicker.

There is no built-in "turned" sensor. Build one with a template, for example:

```yaml
template:
  - binary_sensor:
      - name: "Bin turned round"
        state: "{{ is_state('sensor.tagsense_bin_rotation', '180') }}"
        availability: "{{ has_value('sensor.tagsense_bin_rotation') }}"
```

**The tag must stay visible in every orientation** you want to tell apart,
so it belongs on the lid. A tag on the side of a bin disappears when the bin
is turned 180°; telling those apart would need a second tag with another ID
(a second object) on the opposite side.

## Upgrading

- **To 0.5.0:**
  - **Nothing changes for your objects** unless you opt in: existing objects
    stay on tag16h5, and access codes are off by default.
  - **The TagSense sidebar entry now shows for every Home Assistant user**,
    but non-admins only see their own door pass.
  - **Moving from a "TagSense (dev)" install:** export from it and import
    into this one. The objects bring their history, and the access scanners
    their settings. Passes, static codes and the confirm token are never
    exported: re-add the passes, re-issue the codes, and update `secrets.yaml`
    and the `rest_command` URL (this app's hostname) from the *Confirm events*
    card.
- **To 0.4.3:** new *Rotation*, *Rotation steps* and *Set current orientation
  as 0°* entities appear. 0° is set from the first sighting after the update;
  press the button if the object was not in its normal orientation then.
- **To 0.4.2:** the *Last check* sensor is removed (it wrote to the logbook on
  every check; the time is still the `last_check` attribute of *Status*), and
  *Phantom decodes* is renamed *Discarded decodes* with the same entity ID.
- **From 0.3.x:** install 0.4.1 first, so the objects in the old `objects`
  option are imported into the panel (0.4.2 no longer has that option), or
  add them again in the panel.
- **From 0.2.x or earlier:** install 0.3.x first, so its migration runs.
  0.4.0 no longer reads the old single-object options.

## Limitations

- **Night/IR is tested on one camera only.** On the development camera, IR
  night frames have about a fifth of the daytime crop contrast, and 3-5 of 5
  frames hit instead of 5 of 5, which is still enough. Rain, fog and
  headlight glare have not been specifically tested. Watch Crop brightness
  and Crop contrast next to any missed detections at night.
- **Only tag16h5 is tested on a real camera.** The other tag families
  have only been tested on synthetic frames.
- **Thin decode margin.** A small tag seen at a steep angle is near the limit
  of what the detector can read. A larger print helps more than any setting.
- **Tested on one setup:** a TP-Link Tapo camera through Frigate's go2rtc,
  with a wheelie bin and one tag lying flat on its lid. Other objects and
  placements should work, but have not been tested.
- **Rotation is only tested on simulated turns.** The angle was checked on
  real frames with the tag turned digitally in place (within 1° at every 45°
  step), not yet on a bin actually turned round, where the tag also moves.
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
python -m app.sweep ../test-frames --ablation   # per-family margins and phantoms
```

`app/sweep.py` compares the tag families and the detector parameters. Its
sections are synthetic tags, real-geometry transplants, phantom counts and an
ablation of each tuned parameter. The results are recorded in
[SPEC.md](SPEC.md).

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
  the shape gate were set from measurements on real frames (day and IR
  night, from one camera), not from the AI's reasoning alone. The test suite runs
  against both synthetic and real frames.
- **What has not been done:** there has been no independent human code review
  or security review. Testing covers one camera, by day and at night.

Treat it as experimental software. Read the code before you rely on it for
anything that matters.

## Licence

MIT. See [LICENSE](LICENSE).
