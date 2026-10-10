# Changelog

Bump `version` in `config.yaml` with every release, or Home Assistant will not offer the update.

## 0.5.2 (in development)

- **Mirrored tags:** a new **Mirrored** option on each object, for a camera
  that shows the tag left-right reversed (a mirror or flip setting on the
  camera, or a mirrored print). Until a tag has been seen once, a check that
  finds nothing also looks for it mirrored, on one frame only, and says
  *found mirrored* on the Last crop image and in Recent checks.

## 0.5.1

**Upgrading:** nothing to do. Your detection settings are copied into the
panel's new **Settings** page on the first start, and everything else carries
on as before. To get the unlock blueprint's support for several locks, import
it again (your automation keeps its settings).

- **Documentation site:** <https://lsnewman.github.io/ha-tagsense/>, built
  from the repository's `docs/` folder and published when a release reaches
  `main`. It is organised around the two parts, **TagSense Presence** and
  **TagSense Access**, with search, and talks about a camera and starting a
  scan rather than only a doorbell. The README and this Documentation tab are
  now short overviews that link to it. The app's store description mentions
  both parts.
- **Blueprint: several locks.** The unlock blueprint's *Locks* field now
  takes one lock or more (for example a deadbolt and a handle lock on one
  door). They are unlocked together and, after the auto-lock time, every one
  still unlocked is locked again. The wait for "unlocked" now carries on
  after a minute, so one slow or offline lock cannot stop the others being
  locked again. Automations already made from it keep working. Import the
  blueprint again to get this.
- **Health page.** One place to see whether everything is working: a short
  list of anything to look at, the MQTT and Home Assistant connections, each
  camera's last frame and errors, each object's last 24 hours (checks,
  failures, discarded frames, state changes), the access scanners and the
  recent log. **Download debug bundle** gives a zip to attach to a bug
  report; it never contains passwords, tokens, keys, passes or codes.
- **Use in automations.** Each object's page lists its entity ids, read
  from Home Assistant so renamed entities are right, with copy buttons. It
  also has ready-made automations to paste into Home Assistant's YAML editor:
  not in place at a set time, gone for a long time, back in place, back but
  turned the wrong way, and check straight away when something else happens.
- Access wording no longer assumes a doorbell. It talks about a camera and
  starting a scan; a doorbell is one example.
- **Access: setup checklist and *Test my setup*.** The Access page shows a
  checklist ticked from what TagSense has seen: login connected, scanner
  added, a pass or code exists, a test passed, a doorbell automation started
  a scan, Home Assistant confirmed an event. *Test my setup* on a scanner runs
  a test scan with your pass or a 1-use test code, and explains the result.
  Test events carry `test: true` and are never confirmable, so they cannot
  unlock anything. The blueprint now skips them before the confirm step
  (re-import it to get this; the old one already refused them at the
  confirm step).
- **Links to a page.** Each panel page now has its own address in Home
  Assistant, for example `/<panel>/pass` for My pass or `/<panel>/obj/bin`
  for an object, so a dashboard button can open it directly. The browser's
  Back button and bookmarks follow the panel's pages. The My pass page shows
  its shortcut.
- **Detection settings moved into the panel (Settings).** The options that
  apply to every object (shape gates, frames per check, hits and misses
  needed, confirmation delay, frame sanity thresholds, log level) are now
  changed under **Settings**, and take effect without a restart. On its
  first start, 0.5.1 copies your current values from the Configuration tab,
  so nothing changes on upgrade. After that the Configuration tab's copies
  are ignored, and they are now optional: a new install does not show them,
  and you can clear yours once 0.5.1 has started. A later release removes
  them. Only `go2rtc_url`, the MQTT override and the access login
  stay there. A bad value is refused when you save it and can never stop the
  app from starting. Export / import now carries these settings too.
- **New panel layout.** TagSense is now two parts, **Presence** (tagged
  objects) and **Access** (door codes), with **Settings** beside them. On a
  computer they sit in a sidebar, with each object and scanner listed under
  its section; on a phone they become tabs. Buttons such as *Add object* and
  *Add scanner* moved onto their section's page. Export / import moved to
  **Settings → Backup**. The Access section is always listed: while it is off
  it explains what it does and how to turn it on.
- **Smear threshold default lowered from 0.5 to 0.2** (`sanity_min_ratio`).
  With a tight search area and the object gone, low morning sun made good
  frames score about 0.4. They were discarded as smeared, and the object read
  *unknown* for hours instead of *absent*. Real smeared frames score about
  0.01, so 0.2 still catches them. **Existing installs keep their saved value**:
  if you have seen long *unknown* periods with the *Discard rate* near 100%,
  set *Smear threshold* to 0.2 in the panel under **Settings**.
- Panel: an object's page always loads its chart, history and snapshots when
  opened. Before, returning to the page before the next check (e.g. right
  after an import) showed "No checks yet." over a full history.

## 0.5.0

**Upgrading:** nothing changes for your objects unless you opt in. Existing
objects stay on tag16h5, and access codes are off by default. One visible
change: the TagSense sidebar entry now shows for **every** Home Assistant
user, but non-admins only see their own door pass (or "Nothing here for you").

### For everyone

- **Tag family per object.** The object form has a *Tag family* choice:
  `tag16h5` (the default), `tag25h9`, `tag36h10` or `tag36h11`. The tag ID
  list, *Print tag* and export/import follow it, and objects with different
  families can share a camera.
  - Only tag16h5 is tuned and tested on real frames; the others are tested on
    synthetic frames only.
  - The trade-off: bigger grids resist phantoms far better (on 2000 random
    textures: tag16h5 40 false decodes, tag25h9 1, the 36-bit families none),
    but need about 1.25x the printed size.
- **Export/import carries history:** each object's last 24 hours of chart
  history and its learned position. Importing merges history without
  doubling, and takes the learned position only if it is newer, so moving
  between installs and back is safe. With access codes on, the scanners'
  settings are included too, but never keys, passes or codes.
- **Export/import on a new install:** the card shows even before any object
  exists.
- **`go2rtc_url` without `http://`** now works.
- **Configuration tab:** every option now has a readable name and a
  description.

### Access codes at the door (optional, off by default)

A visitor rings and holds up a code on their phone. TagSense verifies it and
reports an event to Home Assistant; it **never unlocks anything** itself.

- **Passes:** rotating codes, every 30 s, each code works once. They belong to
  Home Assistant users, who open them on the new *My pass* page, with an
  optional white-on-black mode for screens that glare on camera.
- **Static codes** for visitors: an optional start time, an expiry and/or a
  number of uses, a backstop expiry, and revoking. Shown as a picture with a
  ready-to-send message.
- **Verification:** signed codes (HMAC-SHA256), checked in one place that
  fails closed, with replay protection and a lockout after repeated bad codes.
- **Confirm before acting:** each `verified` event carries a one-time
  `event_id`. An automation can confirm it with TagSense, once and within
  30 s, through a `rest_command` with a token, before unlocking. This is
  needed because the Home Assistant Mosquitto app does not enforce ACLs, so
  any MQTT client could publish a fake event. That was tested, as was the
  confirm step refusing a fake.
- **Blueprint "unlock on a confirmed code":** it confirms, unlocks, waits
  until the lock reports unlocked, then locks again after the auto-lock time,
  with optional extra actions that cannot block the relock. One-click import.
- **Scanners** (e.g. the doorbell) are set up in the panel: camera, scan area
  (drag to set), scan window and capture mode.
  - **Auto capture:** snapshots from the HA camera until the go2rtc stream
    runs. go2rtc is read as a video stream, not one JPEG at a time.
  - **Decoding** uses ZXing (new dependency `zxing-cpp`), which reads phone
    screens that OpenCV could not.
  - Codes are blacked out in every image.
- **Who can do what:** admin status comes from Home Assistant (new dependency
  `websocket-client`). If it cannot be checked, nobody counts as an admin.
- **Setting up:** access needs its own MQTT login
  (`access_mqtt_username`/`access_mqtt_password`). The bin sensor does not
  load any of it while it is off. See the Documentation tab.

### Development

- New `SPEC.md`: the design, the tuning evidence and the threat model.
- New `app/sweep.py`: tag-family and QR measurements.

## 0.4.3

- **Rotation sensor** (requested on Reddit: the bin men turn the bin round).
  A new *Rotation* sensor reports how far the tag is turned from its 0°
  position, in degrees, stepped by a new per-object **Rotation steps** setting
  (1-36, default 4: 0/90/180/270°; 8 = 45° steps; 1 = always 0). The exact
  angle is the `angle` attribute. The turn is measured on the tag's own
  surface rather than in the image, so perspective does not distort it (a
  90° turn measured within 1° on real frames). All accepted reads in a check
  are averaged, and the value only moves to a new step once it is 5° past
  the halfway point, so it does not flicker.
- **Set current orientation as 0°**: a button in the panel and on the HA
  device. Until it is pressed, 0° is taken from the first sighting. *Reset
  learned position* also forgets it.
- The rotation entities are unavailable while the object is not present.
  There is no built-in "turned" sensor: build one with a template, e.g.
  rotation is 180 (see the docs).
- Panel: arrows on the tag in the search-area editor (its top now, and at
  0°), a Rotation row in *Last check*, the steps setting and the set-as-0°
  button in *Settings*, a Rotation column in *Recent checks*, a Rotation
  track on the 24-hour chart, and a *State changes* snapshot whenever the
  stepped rotation changes.
- The tag must stay visible in every orientation you want to tell apart, so
  it belongs on the lid. A tag on one side cannot see a 180° turn; that would
  need a second tag (another object) on the opposite side.

## 0.4.2

- **Rejection alert.** When the object's tag is read but every read is
  rejected by the shape gate in 3 checks in a row, a new *Tag rejected*
  problem sensor turns on, a Home Assistant notification appears (one per
  object, updated rather than duplicated, dismissed by itself once a read is
  accepted or a clean check follows) and the panel shows a banner. The
  message says why (too small or too skewed) and what to change if the object
  really is there. Rejected reads still count as misses: they are not the
  tag, and a repeating phantom must never hold the state at present.
- **Reset learned position**: a button in the panel and on the HA device.
  Use it after moving an object, e.g. further from the camera. The size gate
  and position warnings stay off until 10 new hits are learned.
- **Early burst exit.** When an object is already present and the first
  `present_min_hits` frames all hit, the rest of the burst is skipped (about
  60% fewer camera fetches). Misses and uncertain checks still fetch the full
  burst. On a shared camera, every object must be satisfied.
- **Tag aspect** diagnostic sensor (the worst edge ratio of the accepted reads
  in a check), for calibrating `max_aspect` against real day and night data.
  It is also shown in the panel, in the history and on the chart.
- Panel: a **Last 24 hours** chart (hit rate, crop contrast, tag aspect
  against the limit, and the reported state; kept across restarts), **State
  changes** snapshots (the checked image at each state change; the last 20 are
  kept), a **Fit to tag** button in the search-area editor, and **Export /
  import** of objects and their settings as text.
- Reads of other tag IDs are now labelled `ignored: id N (not this object's
  tag)` instead of "phantom id N", so they no longer look as if they were
  counted. The *Phantom decodes* sensor is renamed *Discarded decodes*; its
  entity ID is unchanged.
- Fixed: after changing a setting in the panel, the old retained MQTT command
  could be replayed (and briefly applied) on the next start.
- Removed the *Last check* sensor, which wrote to the logbook on every check.
  The time is still the `last_check` attribute of *Status* and is shown in
  the panel.
- Removed the `objects` app option, which was only used to import objects
  from 0.3.x. Upgrading from 0.3.x: go through 0.4.1 first, or add the
  objects again in the panel.
- aarch64 (Raspberry Pi 4/5) builds.

## 0.4.1

- Print tag: SVG export sized in millimetres, with separate non-overlapping
  `black` and `white` shapes for cutters and two-colour 3D printing.
- Print tag: the white quiet-zone border is optional (PNG and SVG), for tags
  placed on a light surface.
- Fallback is now set per object in the panel ("if this camera fails, try the
  other source"). The global `fallback_source` option is removed.
- Phantom protection, from the first day of real logs (phantom reads in
  gravel were 8-20 px with aspect 2.2-4, against a 50 px, 1.5-aspect tag):
  - new `min_size_ratio` (default 0.5): reads of the object's own tag smaller
    than half its learned usual size are rejected
  - `max_aspect` default lowered from 3.0 to 2.0
  - `present_min_hits` default raised from 1 to 2, so one stray read cannot
    flip the state
  - rejected reads say why (aspect or size) in the log, Warning sensor and
    panel history.
- Panel polish: print card with preview, diagnostics label wrapping.

## 0.4.0

- **TagSense panel** in the HA sidebar (ingress):
  - overview of all objects with state, last image and Check now
  - add, edit and delete objects live, without restarting the app; go2rtc
    streams and HA cameras are offered in a list
  - search area editor: drag a box on a live frame, with the last detection
    and the learned usual position drawn on it
  - enabled and poll interval settings, recent check history (last 50),
    last phantom read with its image, and diagnostics
  - printable tag16h5 PNG for any ID, with the quiet zone
- Objects are now stored in /data/objects.json. The `objects` option is
  imported once on first start, then ignored.
- Removed: support for the 0.2 single-object options and the pre-0.3 MQTT
  clean-up. Upgrade through 0.3.x if coming from 0.2.

## 0.3.0

- **Multiple objects.** New `objects` list: each entry has a name, optional
  `id`, `tag_id` and its own source. Each object is its own HA device
  ("TagSense <name>") with the full entity set, its own crop, poll interval,
  state and learned reference.
- Objects on the same camera share bursts: one fetch, judged by each object
  with its own crop and tag. Different cameras run in parallel. Other objects'
  tags on a shared camera are not reported as phantoms.
- **Breaking: entity IDs.** The old single "TagSense" device is removed on
  first start; entities move to "TagSense <name>" (e.g.
  `sensor.tagsense_status` -> `sensor.tagsense_bin_status`). The main sensor
  keeps `binary_sensor.tagsense_<id>`.
- 0.2 single-object options still work while `objects` is empty (deprecated).
  State, settings and learned reference migrate to the first object.
- New `shared` check trigger in the status attributes; `tag_id` added to the
  main sensor's attributes.

## 0.2.0

- Object-agnostic: new `object_name` option names the main sensor (e.g. "Bin",
  "Car"). Device stays "TagSense"; existing entity IDs are unchanged.
- Size/position warnings now use a reference learned from accepted hits
  (moving average, stored in /data, active after 10 hits) instead of values
  hardcoded for the development camera. Learned values are shown as
  attributes of the Warning sensor.
- `go2rtc_stream` and `camera_entity` no longer default to the development
  camera's names; the app exits with a clear error if the one it needs is empty.
- Docs rewritten to be object-agnostic.

## 0.1.3

- Shape gate: a decode of the target ID whose longest/shortest edge ratio
  exceeds `max_aspect` (default 3.0; 0 = off) is rejected and does not count
  as a hit. Real tag measured 1.45-1.52; the gravel phantom was ~6.
  Scale-invariant, so no tag size is assumed.
- Rejected decodes are logged as warnings, shown on the Warning sensor and
  drawn in orange as "rejected id N".
- Phantom log lines now include edge lengths, aspect ratio and area.

## 0.1.2

- Phantom candidates (decodes of any other tag16h5 ID) are now logged with
  position and size on every check, not only alongside a hit; drawn in orange
  on the Last crop image; and counted by a new *Phantom decodes* diagnostic
  (attributes: this check's decodes and the last one seen).

## 0.1.1

- Publish attributes before state, so a state change in HA never carries the
  previous reason/attributes.

## 0.1.0

- First version: go2rtc and HA camera sources with optional fallback; tag16h5
  detection on a normalised crop; smear sanity check; debounced present/absent
  decision with confirmation checks; MQTT discovery entities (binary sensor,
  status, check button, settings, annotated crop image, diagnostics).
