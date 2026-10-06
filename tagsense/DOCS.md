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

### Detection settings (panel → Settings)

These apply to every object and are changed in the panel under **Settings**,
taking effect straight away. On the app's **Configuration** tab you only need
`go2rtc_url` (and the MQTT override or access login, if used). The other
options still listed there only seed **Settings** on the first start of
0.5.1; after that, changing them on the Configuration tab does nothing.

| Option | Default | Notes |
|---|---|---|
| `go2rtc_url` | *(empty)* | **Configuration tab.** e.g. `http://<frigate-hostname>:1984`. The Frigate app's hostname is on its app page, and it changes if Frigate is reinstalled under another slug. Required if any object uses go2rtc. |
| `max_aspect` | `2.0` | Shape gate: a read of the object's tag whose longest/shortest edge ratio is above this is rejected (gravel phantoms decode as slivers, measured 2.2-6; the real tag about 1.5 by day). 0 disables it. Use the *Tag aspect* sensor's history to set it. |
| `min_size_ratio` | `0.5` | Shape gate: once 10 sightings are learned, a read of the object's tag smaller than this fraction of its usual size is rejected. 0 disables it. |
| `burst_size` | `5` | Frames per check |
| `burst_interval_s` | `1.0` | Seconds between frames in a burst |
| `present_min_hits` | `2` | Frames in one burst that must decode the tag to report present. 2 stops a single stray read flipping the state. |
| `absent_checks` | `3` | Consecutive clean-miss checks before reporting absent |
| `confirm_delay_s` | `45` | Delay between confirmation checks after a *Check now* miss |
| `unknown_after_failures` | `2` | Consecutive checks with no usable frame before reporting unknown |
| `sanity_min_ratio` | `0.2` | Smear threshold (see below). Lower it if *Discard rate* is high on good frames. |
| `sanity_min_h` | `0.02` | Below this the crop is uniform (dead feed) and the frame is discarded |
| `log_level` | `info` | One line per check at `info`. |
| `mqtt_*` | | **Configuration tab.** Optional overrides. Leave unset to use the Supervisor's broker. |

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

## Access codes at the door (off by default)

A visitor rings the doorbell and holds up a code on their phone. TagSense
checks it and reports a **verified** event (or why not) to Home Assistant.
**TagSense never unlocks anything.** It holds no lock credentials and has no
path to a lock. Your automations decide what a verified event does.

There are two kinds of code:

- **Passes** (rotating): for people who come and go, such as family. Each
  pass belongs to one Home Assistant user, who opens it from the TagSense
  entry in the HA sidebar or app. The code changes every 30 s, and each code
  works once.
- **Static codes**: for sending to someone, such as a tradesperson, by text.
  Give it an optional start time, plus an expiry and/or a number of uses (at
  least one). A code with no expiry still expires after a backstop (90 days by
  default, at most a year). Assume a static code can be forwarded.

### Turning it on

Access needs **its own MQTT login**, kept separate from the login shared by
every app, so its traffic is clearly its own. A login cannot stop *other*
clients on the broker from publishing a fake event, though: see *Confirming
events* below for that.

1. In the **Mosquitto broker** app (not the MQTT integration), add a login
   under *Logins*, for example `tagsense_access` with a long random password.
   **Use this login for TagSense access only.** Never give it to another app,
   integration or device (ESPHome, zigbee2mqtt, Node-RED, a phone app...), and
   never reuse its password. Anything that can log in as this user can fake an
   access event.
2. On TagSense's **Configuration** tab, set `access_enabled` to on, and set
   `access_mqtt_username` and `access_mqtt_password` to the new login. Then
   restart TagSense.

If the login is missing, or is the same as the shared app login, access stays
off and the panel's **Access** page says why. The bin sensor is unaffected
either way.

### Who sees what in the panel

Every Home Assistant user sees the TagSense entry in the sidebar.
**Admins** get the whole panel. **Everyone else gets only "My pass"**: their
own pass, if an admin has given them one. TagSense asks Home Assistant who is
an admin. If it cannot tell, for example because Home Assistant is
restarting, nobody counts as an admin until it can.

### Scanners

Open **Access** in the panel and add a **scanner**: the doorbell camera (a
go2rtc stream, an HA camera, or both), the area of the frame where a visitor
holds up their phone, and how long a scan lasts (default 20 s). After adding
it, its page has the same scan-area editor as an object: drag a box on a live
frame (any readable code in the frame is blacked out there). Each scanner is a
device, **TagSense Access `<name>`**, with these entities:

- **Scan** (button): starts a scan window. Scanning only happens in these
  windows, never continuously.
- **Code** (event): what happened, see the table below. Events are never
  retained.
- **Scanning** (binary sensor): on while a window is open.
- **Last scan** (image): the scan area, with every code blacked out.

A doorbell press starts the scan:

```yaml
automation:
  - alias: "Doorbell: scan for an access code"
    triggers:
      - trigger: state
        entity_id: binary_sensor.doorbell_doorbell   # your doorbell's press sensor
        to: "on"
    actions:
      - action: button.press
        target:
          entity_id: button.tagsense_access_front_door_scan
```

A window ends at the first verified code, or after the window time.

### Events

| `event_type` | Meaning | Attributes |
|---|---|---|
| `verified` | A valid code. The only one to act on. | `label`, `code_type` (`rotating`/`static`), `code_id`, `expires`, `uses_left`, `scanned_at`, `scanner` |
| `invalid` | Looks like a TagSense code but does not check out (forged, mistyped, from a deleted pass or an old key). | `fingerprint` only |
| `not_yet_valid` | A genuine static code before its start time. | as `verified`, plus `valid_from` |
| `expired` | A genuine code after its expiry. | as `verified` |
| `replayed` | A genuine code that was already used (a pass code shown twice, or a static code with no uses left). | as `verified` |
| `revoked` | A genuine static code that was revoked or deleted. | `code_id`, `label` |
| `locked_out` | Too many bad codes: the scanner ignores scans for 15 minutes. | `locked_until` |
| `unrecognised` | A QR code that is not a TagSense code (a parcel label...). | `fingerprint` only |
| `unavailable` | TagSense could not check (it fails closed). | `reason` |
| `scan_timeout` | No valid code before the window ended. | frame counts and timing |

Bad codes (`invalid`, `expired`, `replayed`, `revoked`) count towards a
**lockout**: more than 3 in one window, or more than 5 in 10 minutes, locks
the scanner for 15 minutes. The lockout survives restarts and is cleared only
in the panel. Someone could set it off on purpose; that keeps the door shut,
it never opens it. A code's text never appears in events, logs or images:
codes that are not verified are shown only as a short fingerprint.

**Acting on `verified`:** an event entity's state is the time of its last
event, and it is restored when TagSense or Home Assistant restarts. Write the
automation so a restart cannot look like a new event: ignore changes from
`unavailable`/`unknown`, and check the event is recent. For example, a
notification:

```yaml
automation:
  - alias: "Front door: code verified"
    mode: queued
    triggers:
      - trigger: state
        entity_id: event.tagsense_access_front_door_code
        not_from: ["unavailable", "unknown"]
    conditions:
      - condition: state
        entity_id: event.tagsense_access_front_door_code
        attribute: event_type
        state: verified
      - condition: template
        value_template: >-
          {{ (now() - as_datetime(trigger.to_state.state)).total_seconds() < 30 }}
    actions:
      - action: notify.notify
        data:
          message: >-
            {{ trigger.to_state.attributes.label }} verified at the front door
            ({{ trigger.to_state.attributes.code_type }} code)
```

What you do on `verified` (a notification, turning on a light, or, if you
decide to, unlocking) is up to you. **Before unlocking, confirm the event**
(see *Confirming events* below): anything on your MQTT broker could publish a
fake one. Notify on `invalid` and `locked_out` too,
so you hear about attempts.

### Passes and static codes (panel, admins)

- **Passes:** add a pass with a label and the Home Assistant user who will
  carry it. That user opens TagSense in the sidebar (or the HA app) to show
  it. *Disable* stops it working; *Re-enrol* gives it a new secret, so every
  earlier code stops working (use it if a phone is lost); *Delete* removes it.
- **Static codes:** *Issue code* with a label, an optional start, and an
  expiry and/or a number of uses. The code is shown as a picture with a
  ready-to-send message; *Download image*, then send both. *Show* displays it
  again while it is valid; *Revoke* stops it at once; *Revoke all static
  codes* stops every one; *Rotate signing key* invalidates every static code
  issued so far (passes are not affected).

### Frame capture and reading distance

- **Auto** (the default when a scanner has both a go2rtc stream and an HA
  camera): snapshots from the HA camera straight away, then the go2rtc video
  stream once it is running. On the development camera the HA camera gave a
  first full-resolution frame in 0.8 s; the stream took about 6 s to start,
  then gave several frames a second.
- **Video stream** (go2rtc): holds the stream open for the window. Requesting
  single JPEGs from go2rtc instead can take several seconds each.
- **Snapshots:** single images, the only choice for an HA camera on its own.

A scan falls back to snapshots if the stream fails, and the scanner card
shows the timing of the last scan. A code has to be about 100 px across in the
frame (4-5 px per QR module). TagSense's codes are deliberately small (QR
version 2), and the ZXing decoder copes with an over-bright phone screen. Ask
visitors to use medium-high screen brightness (maximum can make it worse) and
to tilt the phone away from lights. Use the highest-resolution stream the
camera offers; a low-resolution stream (for example 896x672) still reads a
code held close. *Keep raw scan frames* (per scanner, off by default) saves up
to 50 frames for 24 h for checking; TagSense codes in them are blacked out.

### What this does and does not protect against

- **Forged or altered codes:** every code is signed (HMAC-SHA256, 80-bit
  tag); a changed character fails.
- **Replayed codes** (a code filmed or photographed, or a doorbell recording):
  each pass code works once and only for about a minute; static codes have
  limited uses and an expiry.
- **Forwarded static codes:** cannot be prevented, which is why they expire
  and can be limited to one use and revoked.
- **Someone at the door with your phone unlocked:** not something a code can
  tell apart.
- **Fake events:** anything that can log in to your MQTT broker (every app
  given the MQTT service, Zigbee2MQTT, Frigate, devices with an MQTT
  password) can publish a fake `verified` event. Fine for notifications; for
  a lock, confirm each event first (below).

### Confirming events (recommended before unlocking anything)

The MQTT broker cannot limit who publishes to TagSense's topics. The Home
Assistant Mosquitto app treats every logged-in user as a superuser, so a
Mosquitto ACL file has no effect. This was tested on 2026-10-05 with Home
Assistant's own login, an HA-user login and a broker-only login: a fake
`verified` event got through with each. So:

- **Simple:** act on `verified` events directly. Fine for a notification or
  a light.
- **Secure:** for a lock or anything else that matters, have the automation
  ask TagSense to confirm the event first. Every `verified` event carries a
  one-time `event_id`. TagSense confirms it **once**, **within 30 s**, and
  only to a request from Home Assistant carrying a secret token. A faked
  MQTT message cannot produce an ID TagSense will confirm.

The **Access** page has a *Confirm events* card with the token and the exact
YAML for your install, for example:

```yaml
# configuration.yaml
rest_command:
  tagsense_confirm:
    url: "http://<tagsense-hostname>:8099/api/confirm/{{ event_id }}"
    method: POST
    headers:
      authorization: !secret tagsense_confirm
    timeout: 5

# secrets.yaml
tagsense_confirm: "Bearer <token from the Access page>"
```

Then, in the automation, before the action that matters:

```yaml
      - action: rest_command.tagsense_confirm
        data:
          event_id: "{{ trigger.to_state.attributes.event_id }}"
        response_variable: confirm
      - condition: template
        value_template: "{{ confirm.status == 200 and confirm.content.confirmed }}"
      - action: lock.unlock
        target:
          entity_id: lock.front_door
```

**Or use the blueprint**, which does all of this for a lock: it confirms
the event, unlocks, waits until the lock reports unlocked, waits the auto-lock
time (fixed, or read from the lock's own auto-lock setting), and locks again
if the lock is still unlocked. It still needs the `rest_command` above.

[![Import the TagSense unlock blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Flsnewman%2Fha-tagsense%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ftagsense%2Funlock_on_confirmed_code.yaml)

If TagSense is restarted, unreachable, or the token is wrong, nothing is
confirmed, so the lock stays shut. *Rotate token* on the Access page replaces
the token; update `secrets.yaml` afterwards. Only requests from Home
Assistant's own address reach the confirm step at all, and the token stops
anything else that shares that address (apps on the host network).

## Tag and print notes

- Use the object's family (tag16h5 by default), with a white quiet zone
  about as wide as the black border. A matte finish is best.
- A larger print is the main way to gain decode margin at oblique angles.
