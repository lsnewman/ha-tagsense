# TagSense

[![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flsnewman%2Fha-tagsense)
[![Open TagSense in my Home Assistant](https://my.home-assistant.io/badges/supervisor_addon.svg)](https://my.home-assistant.io/redirect/supervisor_addon/?addon=744e9206_tagsense)

A Home Assistant app (formerly "add-on") that uses cameras you already have
to answer two kinds of question:

- **TagSense Presence: is it in its usual place?** Stick a printed AprilTag
  on something, such as a wheelie bin, a car, a chair or a garage door.
  TagSense looks for that tag in camera frames and reports **present /
  absent / unknown**, plus how far the tag is turned (to tell when a bin comes
  back the wrong way round).
- **TagSense Access: is this a valid code?** Someone holds up a QR code on
  their phone to a camera, for example a doorbell, a gate or garage camera.
  TagSense checks it and reports a **verified** event (or why not). Codes are
  rotating passes for Home Assistant users, or time-limited codes you send to
  someone. Access is **off until you turn it on**, and it **never unlocks
  anything** itself: your automations decide what a verified code does.

Both are managed from a **TagSense panel** in the Home Assistant sidebar, and
everything appears in Home Assistant through MQTT discovery.

![The TagSense panel: an object's page, with its search area drawn on a live frame and the last check beside it](docs/panel-object.png)

> **Status: early.** Presence was developed and tested with a wheelie bin on
> one camera, by day and at night under the camera's IR. Access was tested on
> one camera, with codes shown on a phone. It has had one user. See
> [Limitations](#limitations).

**Contents:**
[Requirements](#requirements) ·
[Installation](#installation) ·
[The panel](#the-panel) ·
[TagSense Presence](#tagsense-presence) ·
[TagSense Access](#tagsense-access) ·
[Health and troubleshooting](#health-and-troubleshooting) ·
[Upgrading](#upgrading) ·
[Limitations](#limitations) ·
[Development](#development) ·
[AI usage disclaimer](#ai-usage-disclaimer)

## Requirements

- Home Assistant OS or Supervised (apps need the Supervisor). Built for
  **amd64** and **aarch64** (Raspberry Pi 4/5).
- The **Mosquitto broker** app with the MQTT integration. TagSense gets the
  broker login from the Supervisor automatically.
- A camera, available as either or both of:
  - a **go2rtc** stream (for example the go2rtc bundled with the Frigate app,
    port 1984). Recommended: it gives full-resolution frames.
  - a **Home Assistant camera entity**, read through the camera proxy. These
    are often lower resolution, which leaves less margin.
- For Presence: a printed AprilTag (the panel prints one for you).
- For Access: a phone to show codes on, and a second MQTT login just for
  Access (see [Turning Access on](#turning-access-on)).

## Installation

1. Add this repository to Home Assistant:

   [![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flsnewman%2Fha-tagsense)

   Or open **Settings → Apps → App store → ⋮ → Repositories** and add
   `https://github.com/lsnewman/ha-tagsense`.
2. Find **TagSense** in the store and click **Install**. The image is built on
   your machine, which takes a few minutes the first time.
3. On the **Configuration** tab, set `go2rtc_url` if you will use go2rtc
   streams (for example `http://<frigate-hostname>:1984`; the Frigate app's
   hostname is on its app page). Then start the app.
4. Open **TagSense** in the sidebar. Everything else is set up there:
   - **Presence:** *Add object*, give it a name, the tag ID and the camera,
     then draw its search area. Each object appears as a **TagSense
     `<name>`** device.
   - **Access:** see [Turning Access on](#turning-access-on).

The Configuration tab only holds what the panel needs before it can work:
`go2rtc_url`, an optional MQTT broker override, and Access's on switch and
login. Everything else is in the panel and takes effect without a restart.

## The panel

On a computer the panel has a sidebar; on a phone the same sections are tabs
along the top.

- **Presence:** every object with its state, latest image and a *Check now*
  button. Each object is listed in the sidebar with a coloured dot for its
  state; its page has the search-area editor, the last check, settings, a
  24-hour chart, recent checks, state-change snapshots, diagnostics, *Use in
  automations* and *Print tag*.
- **Access:** the setup checklist, passes, static codes, *Confirm events* and
  each scanner (camera), with *Scan now*, *Test my setup* and a live frame.
  While Access is off, this page explains what it does and how to turn it on.
- **Settings:** the detection settings shared by every object, and
  **Backup** (export / import).
- **Health:** whether everything is working, with a debug bundle to download.
- **My pass:** your own Access pass, if you have one.

**Who sees what:** every Home Assistant user sees the TagSense sidebar entry.
Admins get the whole panel; everyone else only gets **My pass**. TagSense asks
Home Assistant who is an admin, and if it cannot tell, nobody counts as one
until it can.

**Links to a page.** Each page has its own address under the sidebar entry,
so a dashboard button (tap action *Navigate*) can open one directly, and the
browser's Back button works:
`/<panel>/pass` (anyone), `/<panel>/obj/<object id>`, `/<panel>/access`,
`/<panel>/settings` (admins). `<panel>` is the path you see when you open
TagSense from the sidebar, for example `/744e9206_tagsense`. This needs
TagSense opened as a panel, not embedded in a dashboard card.

---

## TagSense Presence

A cheap, deterministic alternative to an image classifier (for example a
Frigate custom model) for "is the bin out?" questions. Each frame check is a
few tens of milliseconds of CPU on a cropped region, and it either finds your
specific tag or it doesn't.

### How it works

1. **Grab a burst of frames.** A check fetches several frames (default 5, one
   second apart). When several objects share a camera, one burst serves all of
   them, and each object looks for its own tag in its own search area.
2. **Discard broken frames.** Some streams occasionally deliver smeared
   frames full of vertical streaks. TagSense scores each frame and discards
   bad ones, so a corrupt frame is never treated as "absent". A frame where
   the tag is found always counts.
3. **Look for the tag** with OpenCV's AprilTag detector. Reads with an
   implausible shape are rejected: too elongated, or far smaller than usual
   (the *shape gate*). Reads of other tag IDs are ignored, but logged and
   drawn on the image as `ignored: id N`.
4. **Decide, with debounce.**
   - Seeing the tag is strong evidence, so **present** is reported straight
     away.
   - Not seeing it is weak evidence (glare, darkness, a person in the way),
     so **absent** is only reported after several clean misses in a row.
   - Checks that get no usable frame lead to **unknown**, never absent.
   - Reads rejected by the shape gate count as misses. Three such checks in a
     row raise a *Tag rejected* alert, in case the real tag is being rejected.
   - When the object is present and the first frames all hit, the rest of the
     burst is skipped.
5. **Measure the rotation.** While the tag is seen, a *Rotation* sensor reports
   how far it is turned from its 0° position, measured on the tag's own surface
   so perspective does not distort it. See [Rotation](#rotation).

Checks run on a timer, on demand from **Check now**, or both. Pressing Check
now just after the object moved starts a short series of confirmation
checks, so absent is confirmed in about a minute and a half rather than after
several polls.

### The tag

- **Family:** `tag16h5` by default, any ID from 0 to 29. Its big cells decode
  at small sizes and steep angles; the trade-off is that random texture
  occasionally decodes as a valid ID, which the ID filter and shape gate deal
  with. Other families can be chosen per object: see
  [Tag families](#tag-families).
- **Quiet zone:** the black square needs a light border about one cell wide.
  The printed tags include it; leave it off only if the tag sits on a light
  surface, such as a white lid.
- **Finish:** matte if possible. Gloss causes glare.
- **Placement:** face the camera if you can. A tag lying flat (on a lid, say)
  is seen at a steep angle: it still works, with less margin.
- **Size:** bigger is better. Print size and camera resolution are the main
  ways to gain margin. The development setup sees a flat tag at about 50 px
  across in a 1080p frame.

Each object's page has **Print tag**: a **PNG** for paper (print it as large
as the object allows and laminate it outdoors), or an **SVG** sized in
millimetres with separate `black` and `white` shapes, ready for a vinyl
cutter or a two-colour 3D print.

### Tag families

The detector was tuned on real frames for **tag16h5 only**. The others use the
same settings and have **only been tested on synthetic frames**.

| Family | IDs | Data grid | Phantom resistance | Size needed |
|---|---|---|---|---|
| `tag16h5` (default) | 0–29 | 4x4 | lowest | smallest |
| `tag25h9` | 0–34 | 5x5 | much better | a little bigger |
| `tag36h10` | 0–2319 | 6x6 | high | about 1.25x |
| `tag36h11` | 0–586 | 6x6 | highest | about 1.25x |

On 2000 random textures, tag16h5 produced 40 false decodes, tag25h9 1, and the
36-bit families none. Objects with different families can share a camera. If
you switch an object to a new tag, press *Reset learned position* once it is
in place. The measurements are in [SPEC.md](SPEC.md).

### Objects

| Field | What it does |
|---|---|
| Name | What the tag is on. It names the device ("TagSense Bin") and its main sensor, and can be changed at any time. |
| ID | A short, stable identifier used in entity IDs, MQTT topics and the data folder. Made from the name unless you type one, and **cannot be changed later**; renaming keeps it. |
| Tag family and ID | What is printed on the object. Two objects on the same camera need different tags. |
| Camera | A **go2rtc stream** (choose the highest-resolution stream that connects directly to the camera) or a **Home Assistant camera**. Tick **Fallback** to try the other one when the main one fails. |
| Search area | Drawn on a live frame in the object's page: drag the box, drag a corner, or draw a new one. It must cover **every** spot where the object might be. **Fit to tag** sets it to the learned tag position plus 3x the tag size on each side. |
| Poll interval, Enabled, Rotation steps | On the object's page, and also as Home Assistant entities. |

### Settings (shared by every object)

Changed in the panel under **Settings**, taking effect straight away.

| Setting (option name) | Default | What it does |
|---|---|---|
| Shape gate, max aspect (`max_aspect`) | `2.0` | A read counts only if the tag's longest edge is at most this many times its shortest. A square tag stays fairly square even at a steep angle (about 1.5 in the development setup); phantom reads in gravel measured 2.2–6. `0` turns it off. Use the *Tag aspect* sensor's history to set it. |
| Shape gate, min size ratio (`min_size_ratio`) | `0.5` | Once 10 sightings are learned, reads smaller than this fraction of the usual tag size are rejected. Phantoms are usually tiny (15–40% of the real tag). `0` turns it off. |
| Frames per check (`burst_size`) | `5` | More frames are harder to fool with one bad frame, but each check takes longer. |
| Seconds between frames (`burst_interval_s`) | `1.0` | Spacing frames out lets a passing person, car or headlight clear the tag. |
| Hits needed for present (`present_min_hits`) | `2` | `2` means a single stray read cannot flip the state. Fewer hits than this is *inconclusive*: the state is kept. |
| Misses needed for absent (`absent_checks`) | `3` | Clean misses in a row before reporting absent. The count resets when the tag is seen, or if the previous miss is older than three poll intervals. |
| Confirmation delay (`confirm_delay_s`) | `45` | Seconds between confirmation checks after *Check now* finds no tag. |
| Failures before unknown (`unknown_after_failures`) | `2` | Checks in a row with no usable frame before the state becomes unknown. |
| Smear threshold (`sanity_min_ratio`) | `0.2` | Frames scoring below this are treated as smeared and discarded. Real scenes score about 1.0 (a tight crop of an empty scene in low sun about 0.4); smeared frames about 0.0. Lower it if *Discard rate* is high on frames that look fine. |
| Dead-feed threshold (`sanity_min_h`) | `0.02` | Below this the crop is uniform (a black or dead feed) and the frame is discarded. |
| Log level (`log_level`) | `info` | At `info` there is one line per check. |

### Entities

Each object is a device, **TagSense `<name>`**. Entity IDs follow the device,
for example `binary_sensor.tagsense_bin` and `button.tagsense_bin_check_now`.

| Entity | Description |
|---|---|
| **TagSense `<name>`** (`binary_sensor`, occupancy) | `on` = in its usual place, `off` = gone (confirmed). **Unavailable** while unknown, while disabled, or when the app is not running. |
| **Status** (enum sensor) | `present`, `absent` or `unknown`, with a `reason` attribute (`starting`, `restored`, `tag_seen`, `inconclusive`, `pending`, `no_tag`, `fetch_failed`, `all_frames_invalid`, `disabled`) and details of the last check. |
| **Check now** (button) | Checks straight away; for automations, for example when Frigate sees someone leave the area. |
| **Rotation** (sensor, °) | How far the tag is turned from 0°, clockwise as seen by the camera, in *Rotation steps*. Unavailable while the object is not present. |
| **Rotation steps** (number), **Set current orientation as 0°** (button) | 4 = 0/90/180/270°, 8 = 45° steps, 1 = off. 0° comes from the first sighting until you press the button. |
| **Tag rejected** (problem sensor) | On after 3 checks in a row where the tag was read but rejected by the shape gate, with a Home Assistant notification explaining why. Both clear by themselves. |
| **Reset learned position** (button) | Forgets the learned usual size, position and 0°, for example after moving the object. |
| **Enabled** (switch), **Poll interval** (number), **Crop x1/y1/x2/y2** (numbers) | The same settings as the panel. Poll interval `0` = only when *Check now* is pressed. |
| **Last crop** (image) | The latest search area, annotated: green for the tag, orange for ignored and rejected reads. |
| Diagnostics | Last source, resolution, last error, warning, fetch and detect time, fetch failures, discard rate, unique frames, discarded decodes, tag size and aspect, sanity ratio, miss streak, crop brightness and contrast. |

TagSense also **learns** where your tag usually appears, and how big, from
accepted hits. After 10 hits the *Warning* diagnostic flags a hit far from the
usual size or position (often a phantom of your ID). It only warns, and it
follows a permanent move within about 20 hits.

### Using it in automations

Each object's page has a **Use in automations** card: its entity IDs (read
from Home Assistant, so renamed entities show their real IDs) with copy
buttons, and ready-made automations to paste into a new automation's YAML
editor:

- not in place at a set time;
- gone for a long time;
- back in place;
- back, but turned the wrong way;
- check straight away when something else happens (a gate, a garage door).

### Rotation

TagSense measures how far the tag is turned on its own surface (the lid), not
in the image, so perspective does not distort it: on real frames a 90° turn
measured within 1°, where the raw image angle was nearly 30° out. The value
only moves to a new step once it is 5° past the halfway point, so it does not
flicker. For a "turned round" sensor, use a template:

```yaml
template:
  - binary_sensor:
      - name: "Bin turned round"
        state: "{{ is_state('sensor.tagsense_bin_rotation', '180') }}"
        availability: "{{ has_value('sensor.tagsense_bin_rotation') }}"
```

**The tag must stay visible in every orientation** you want to tell apart, so
it belongs on the lid. A tag on the side disappears when the object is turned
180°.

![24-hour chart: hit rate, crop contrast and tag aspect, over the reported state](docs/panel-chart.png)

---

## TagSense Access

Someone holds up a QR code on their phone to a camera, and TagSense reports
whether it is valid. What starts the scan is up to you: a doorbell press is
the obvious one, but it could be a button, a motion sensor or anything else
an automation can see. What happens next is up to you too: a notification,
a light, or, if you decide to, unlocking a door or opening a gate.

![The Access page: setup checklist, and Test my setup with a verified code](docs/panel-access.png)

**TagSense never unlocks anything.** It holds no lock credentials and has no
path to a lock. It verifies codes and reports events; your Home Assistant
automations act on them. For anything that matters, the automation asks
TagSense to **confirm** each event first, so a faked message cannot open
anything (see [Confirming events](#confirming-events)). A
[blueprint](#the-unlock-blueprint) does all of this for one or more locks.

### Codes

- **Passes** (rotating) for people who come and go, such as family. Each pass
  belongs to one Home Assistant user, who opens it from the TagSense sidebar
  entry (**My pass**) in the browser or the Home Assistant app. The code
  changes every 30 s, and each code works once.
- **Static codes** to send to someone, such as a visitor or tradesperson.
  Give it an optional start time, plus an expiry and/or a number of uses. A
  code with no expiry still expires after a backstop (90 days by default, at
  most a year). Assume a static code can be forwarded: that is why they expire
  and can be revoked.

### Turning Access on

Access needs **its own MQTT login**, separate from the one every app shares,
so its traffic is clearly its own.

1. In the **Mosquitto broker** app (not the MQTT integration), add a login
   under *Logins*, for example `tagsense_access` with a long random password.
   **Use it for TagSense Access only**, never for anything else.
2. On TagSense's **Configuration** tab, turn on `access_enabled` and set
   `access_mqtt_username` / `access_mqtt_password` to that login. Restart
   TagSense.
3. Open **Access** in the panel and follow the **Setup** checklist.

If the login is missing, or is the same as the shared app login, Access stays
off and the Access page says why. Presence is unaffected either way.

### Scanners

A **scanner** is a camera that reads codes: a go2rtc stream, a Home Assistant
camera, or both. Add it on the Access page, draw the area where people will
hold up their phone, and set how long a scan lasts (default 20 s). Each
scanner is a device, **TagSense Access `<name>`**:

- **Scan** (button): starts a scan. Scanning only happens in these windows,
  never continuously. A scan ends at the first verified code, or when the time
  is up.
- **Code** (event): what happened (see [Events](#events)).
- **Scanning** (binary sensor): on while a scan is running.
- **Last scan** (image): the scan area, with every code blacked out.

An automation starts the scan, for example on a doorbell press:

```yaml
automation:
  - alias: "Front door: scan for an access code"
    triggers:
      - trigger: state
        entity_id: binary_sensor.front_door_doorbell   # whatever should start a scan
        to: "on"
    actions:
      - action: button.press
        target:
          entity_id: button.tagsense_access_front_door_scan
```

**Reading distance:** a code needs to be about 100 px across in the frame.
TagSense's codes are deliberately small, so a low-resolution stream (for
example 896x672) still reads a code held close. With both a go2rtc stream and
a Home Assistant camera, a scan starts with snapshots straight away and
switches to the video stream once it is running. People should use
medium-high screen brightness and tilt the phone away from lights.

### Setup checklist and Test my setup

The Access page starts with a **Setup** checklist, ticked from what TagSense
has actually seen: the Access login is connected; a scanner exists; there is
a pass or a code; *Test my setup* has read a code; Home Assistant has started
a scan; Home Assistant has confirmed an event. It folds away once everything
is ticked.

**Test my setup** on a scanner runs a test scan: show your pass, or issue a
1-use test code (valid for 15 minutes, revoked if unused), and hold it up to
the camera. The result says what happened and what to try if it failed. Test
events are marked `test: true` and can never be confirmed, so they cannot
unlock anything.

### Events

| `event_type` | Meaning |
|---|---|
| `verified` | A valid code: the only one to act on. Attributes: `label`, `code_type` (`rotating`/`static`), `code_id`, `expires`, `uses_left`, `event_id`, `scanner`. |
| `invalid` | Looks like a TagSense code but does not check out (forged, from a deleted pass or an old key). |
| `not_yet_valid`, `expired`, `replayed`, `revoked` | A genuine code used too early, too late, a second time, or after being revoked. |
| `locked_out` | Too many bad codes: the scanner ignores scans for 15 minutes. |
| `unrecognised` | A QR code that is not a TagSense code (a parcel label...). |
| `unavailable` | TagSense could not check, so it failed closed. |
| `scan_timeout` | No valid code before the scan ended. |

Bad codes count towards a **lockout** (more than 3 in one scan, or 5 in 10
minutes), which survives restarts and is cleared in the panel. A code's text
never appears in events, logs or images.

### Confirming events

Anything that can log in to your MQTT broker can publish a fake `verified`
event, and the Home Assistant Mosquitto app cannot restrict who publishes
where (tested). That is fine for a notification. For a lock, have the
automation ask TagSense to **confirm** the event first: every `verified` event
carries a one-time `event_id`, which TagSense confirms **once**, **within
30 s**, and only to Home Assistant with a secret token.

The Access page's **Confirm events** card shows the token and the exact
`rest_command` for your install, to paste into `configuration.yaml` and
`secrets.yaml`.

### The unlock blueprint

For one lock or several (for example a deadbolt and a handle lock on the same
door): it confirms the event, unlocks them all, waits the auto-lock time
(fixed, or read from a lock's own setting) and locks again every one still
unlocked. One slow or offline lock cannot stop the others locking again. It
needs the `rest_command` from the *Confirm events* card.

[![Import the TagSense unlock blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Flsnewman%2Fha-tagsense%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ftagsense%2Funlock_on_confirmed_code.yaml)

To get a newer version, import it again from the same link; automations made
from it keep their settings. If your lock does not lock again by itself,
also add a plain "lock again after N seconds unlocked" automation as a safety
net for every unlock.

### What Access does and does not protect against

- **Forged or altered codes:** every code is signed; a changed character fails.
- **Replayed codes** (a photo or a recording of a code): each pass code works
  once and only for about a minute; static codes have limited uses and expire.
- **Fake MQTT events:** stopped by confirming events before unlocking.
- **Forwarded static codes:** cannot be prevented, which is why they expire,
  can be limited to one use, and can be revoked.
- **Someone holding your unlocked phone:** a code cannot tell.

The Documentation tab in Home Assistant has the full details.

---

## Health and troubleshooting

The panel's **Health** page lists anything that needs a look (MQTT down, a
camera with no recent frame, an object stuck at unknown, many discarded
frames, a locked-out scanner), with each camera's and object's last 24 hours,
the Access scanners and the recent log.

**Download debug bundle** gives a zip to attach to a bug report: the log,
settings, each object's status and checks, and the scanners' settings and
recent events. Passwords, tokens and keys are hidden, and nothing from the
Access code store (keys, passes, codes) is included.

## Upgrading

- **To 0.5.1:**
  - The panel has a new layout (sidebar on a computer, tabs on a phone).
  - **Detection settings moved into the panel** (Settings). On its first
    start 0.5.1 copies your values from the Configuration tab, so nothing
    changes. After that the Configuration tab's copies are ignored and can be
    cleared.
  - **Re-import the unlock blueprint** to get several locks and the skip of
    test events. Your automation keeps its settings.
- **To 0.5.0:** nothing changes for your objects unless you opt in. The
  sidebar entry shows for every Home Assistant user, but non-admins only see
  their own pass. Moving between installs: export and import (Settings →
  Backup). Passes, codes and the confirm token are never exported.
- **To 0.4.3:** new *Rotation* entities. 0° is set from the first sighting.
- **From 0.3.x or earlier:** install 0.4.1 first, so the old options are
  imported into the panel.

The [CHANGELOG](tagsense/CHANGELOG.md) has every change.

## Limitations

- **Tested on one setup.** Presence: a wheelie bin with a tag lying flat on
  its lid, on one camera through Frigate's go2rtc, by day and at night under
  IR. Access: one camera, at full and low resolution. Other cameras, objects
  and placements should work, but have not been tested.
- **Night, rain and glare.** IR night frames work on the development camera
  (lower contrast, 3–5 of 5 frames hit). Rain, fog and headlight glare have
  not been specifically tested.
- **Only tag16h5 is tested on a real camera.** The other families have only
  been tested on synthetic frames.
- **Thin margin for small tags.** A small tag at a steep angle is near the
  limit of what the detector can read. A larger print helps more than any
  setting.
- **Phantoms.** tag16h5 trades error-resistance for small size. The ID filter
  and shape gate protect the decision, but a false read of your exact ID with
  a plausible shape is still possible.
- **Rotation is only tested on simulated turns** (the tag turned digitally
  on real frames), not yet on a bin actually turned round.
- **Run Presence in shadow mode first.** If it replaces an existing detector,
  run both side by side for a few weeks before relying on it.
- **Access has had no independent security review.** Read
  [What Access does and does not protect against](#what-access-does-and-does-not-protect-against),
  confirm events before unlocking, and keep a way in that does not depend on
  it.

## Development

```sh
cd tagsense
pip install -r requirements.txt pytest
python -m pytest tests
python -m app.check ../test-frames --save-annotated /tmp/annotated
python -m app.sweep ../test-frames --ablation   # per-family margins and phantoms
```

`app/check.py` runs the detector and sanity check over a folder of frames
(`present/`, `absent/`, `smear/`) and flags misses, phantoms and corrupt
frames. `app/sweep.py` compares the tag families and detector parameters; the
results are in [SPEC.md](SPEC.md). Tests that use real frames read
`../test-frames` or `$TAGSENSE_TESTDATA` and are skipped without them; real
frames are not committed.

Releasing: bump `version` in `tagsense/config.yaml`, or Home Assistant will
not offer the update.

## AI usage disclaimer

This project was built with substantial help from AI, and you should weigh
that when deciding whether to rely on it.

- **Design and early tuning:** the approach, the tag family choice and the
  first detector settings were worked out in conversation with an AI
  assistant. The maintainer ran test scripts on real camera frames and fed the
  results back. Several of the AI's assumptions were later shown to be wrong
  by measurement (for example, contrast enhancement was expected to help and
  in fact hurt). The settings that shipped are the ones that held up.
- **Code, tests and documentation:** the source code, test suite and
  documentation, including this README, were written by an AI coding agent
  (Anthropic's Claude, through Claude Code). The maintainer directed the work.
- **What the maintainer did:** set requirements and constraints, made or
  approved the design decisions and trade-offs, reviewed the plans, supplied
  the real camera frames used for calibration, and installed and tested each
  release on their own Home Assistant system.
- **How the numbers were checked:** thresholds were set from measurements on
  real frames (day and IR night, from one camera), not from the AI's
  reasoning alone. The test suite runs against synthetic and real frames.
- **What has not been done:** no independent human code review or security
  review. Testing covers one camera for each part.

Treat it as experimental software. Read the code before you rely on it for
anything that matters, and especially before letting it near a lock.

## Licence

MIT. See [LICENSE](LICENSE).
