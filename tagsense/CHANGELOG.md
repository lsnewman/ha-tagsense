# Changelog

Bump `version` in `config.yaml` with every release, or Home Assistant will not offer the update.

## 0.5.0 (in development)

- **Tag family per object.** The object form in the panel has a new *Tag
  family* choice: `tag16h5` (the default; existing objects stay on it),
  `tag25h9`, `tag36h10` or `tag36h11`. The tag ID list follows the family
  (0-29, 0-34, 0-2319, 0-586, as read from OpenCV), and *Print tag* and
  export/import follow it too. Objects with different families can share a
  camera. There is no new app option: everything is set in the panel.
- Only tag16h5 is tuned and tested on real frames; the other families are
  tested on synthetic frames only. Trade-off: bigger grids resist phantoms
  far better (on 2000 random textures: tag16h5 40 false decodes, tag25h9 1,
  the 36-bit families none), but need about 1.25x the printed size.
- **Access codes, preview (off by default).** A new *Access* page in the
  panel manages QR **scanners**, for example the doorbell camera, each with
  its own source, scan area and scan window. A *Scan* button (pressed by a
  doorbell automation) scans for a few seconds. In this build a scan only
  reports *that* a code was read: a `qr_seen` event with the code's length
  and a fingerprint, never its content. Images have the code blacked out.
  Verification of signed codes comes next. Access is turned on with
  `access_enabled` and needs its own MQTT login (`access_mqtt_username` /
  `access_mqtt_password`) plus a broker ACL (see the docs). The bin sensor
  does not load or touch any of it while it is off.
- **Faster scanning from go2rtc.** A scan holds the camera's video stream
  open and checks the newest frame continuously, instead of requesting one
  JPEG at a time (about 6 s per frame on a 5 MP Reolink; now about 6 s to
  start, then about 7 frames per second). The scanner card and the log show
  each scan's timing. A per-scanner *Frame capture* setting can switch to
  single snapshots, and a failed stream falls back to snapshots.
- **QR decoding uses ZXing** (new dependency `zxing-cpp`). On real camera
  frames of a phone screen, OpenCV's QR detector read nothing (it did not
  even detect an easily readable code whose bright screen had bled into the
  dark modules); ZXing read it, and 13 of 50 frames of a scan, in about 1 ms
  per frame. It also reads Micro QR. AprilTag detection is unchanged.
- `go2rtc_url` without `http://` now works (it is added).
- New `app/sweep.py` (development): per-family decode margin on synthetic
  frames and on real frames with the tag swapped in, phantom counts, and a
  one-at-a-time ablation of the tuned detector parameters. The results and
  the design notes are in the new `SPEC.md`.

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
