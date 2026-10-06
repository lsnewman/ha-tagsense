# Entities and automations

## Entities

Each object is a device, **TagSense `<name>`**. Entity IDs follow the device,
for example `binary_sensor.tagsense_bin`, `sensor.tagsense_bin_status` and
`button.tagsense_bin_check_now`.

| Entity | Description |
|---|---|
| **TagSense `<name>`** (`binary_sensor`, occupancy) | `on` = in its usual place, `off` = gone (confirmed). **Unavailable** while the state is unknown, while disabled, or when the app is not running. Attributes: `last_seen`, `size_px`, `centre`, `area_px`, `aspect`, `source`. |
| **Status** (enum sensor) | `present`, `absent` or `unknown`, with a `reason` attribute and the details of the last check (frames, valid, hits, trigger). |
| **Check now** (button) | Checks straight away. If the tag is not found, confirmation checks follow until absent is confirmed or the tag is seen. |
| **Rotation** (sensor, °) | How far the tag is turned from 0°, clockwise as seen by the camera, in *Rotation steps*. Attributes `angle` (exact) and `steps`. Unavailable while the object is not present. |
| **Rotation steps** (number), **Set current orientation as 0°** (button) | See [Rotation](rotation.md). |
| **Tag rejected** (problem sensor) | On after 3 checks in a row in which the tag was read but every read was rejected by the shape gate. A Home Assistant notification explains why (too small or too skewed) and what to change if the object really is there. Both clear by themselves after a check without rejected reads. |
| **Reset learned position** (button) | Forgets the learned usual size, position and 0°, for example after moving the object further from the camera. |
| **Enabled** (switch), **Poll interval** (number), **Crop x1/y1/x2/y2** (numbers) | The same settings as the object's page. |
| **Last crop** (image) | The latest search area, annotated: the tag in green with its ID, size and a red dot on corner 0; ignored and rejected reads in orange. |
| Diagnostics | Last source, frame resolution, last error, warning, fetch time, detect time, fetch failures, discard rate, unique frames, discarded decodes, tag size, tag aspect, sanity ratio, miss streak, crop brightness, crop contrast. |

The `reason` attribute of **Status** is one of:

| Reason | Meaning |
|---|---|
| `starting` | No state yet. |
| `restored` | Carried over from before a restart, until the first good check. |
| `tag_seen` | Present: the tag was found. |
| `inconclusive` | Fewer hits than needed: the state is kept. |
| `pending` | Misses seen, but not yet enough for absent. |
| `no_tag` | Absent, confirmed. |
| `fetch_failed`, `all_frames_invalid` | No usable frame. |
| `disabled` | Checking is off. |

Some diagnostics explained:

- **Warning:** the tag was found at an unusual size or position compared with
  what has been learned, a read was rejected, or the search area is invalid.
  The learned values are its attributes.
- **Unique frames** lower than the frames fetched means the source served the
  same frame more than once.
- **Discarded decodes:** ignored reads of other IDs and rejected reads of the
  object's own tag in the latest check; the attributes show where.
- **Tag aspect:** the worst edge ratio of the accepted reads. Its history (day
  and night) shows how close the real tag comes to the shape gate.

## Use in automations

Each object's page has a **Use in automations** card: its entity IDs (read from
Home Assistant, so renamed entities show their real IDs) with copy buttons, and
ready-made automations to paste into a new automation's YAML editor
(**Settings → Automations → Create automation → ⋮ → Edit in YAML**):

- not in place at a set time;
- gone for a long time;
- back in place;
- back, but turned the wrong way (when rotation is on);
- check straight away when something else happens.

For example, a reminder:

```yaml
alias: "Bin: not in place at 21:00"
triggers:
  - trigger: time
    at: "21:00:00"
conditions:
  - condition: state
    entity_id: binary_sensor.tagsense_bin
    state: "off"
actions:
  - action: notify.notify          # or your phone's notify action
    data:
      message: "Bin is not in its usual place."
mode: single
```

And checking straight away when something happens, for example when Frigate
sees a person leave the area or a gate closes:

```yaml
alias: "Bin: check when the gate closes"
triggers:
  - trigger: state
    entity_id: binary_sensor.side_gate      # whatever tells you it may have moved
    to: "off"
actions:
  - action: button.press
    target:
      entity_id: button.tagsense_bin_check_now
mode: single
```
