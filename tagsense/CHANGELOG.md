# Changelog

Bump `version` in `config.yaml` with every release, or Home Assistant will not offer the update.

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
