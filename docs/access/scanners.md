# Scanners

A **scanner** is a camera that reads codes: a go2rtc stream, a Home Assistant
camera, or both. Add one on the Access page with **Add scanner**: the camera,
the area of the frame where people will hold up their phone, and how long a
scan lasts (default 20 s). After adding it, its page has the same scan-area
editor as an object: drag a box on a live frame. Any readable code in the frame
is blacked out there.

Each scanner is a device, **TagSense Access `<name>`**:

| Entity | Description |
|---|---|
| **Scan** (button) | Starts a scan. Scanning only happens in these windows, never continuously. A scan ends at the first verified code, or when the time is up. |
| **Code** (event) | What happened: see [Events](events.md). Events are never retained. |
| **Scanning** (binary sensor) | On while a scan is running. |
| **Last scan** (image) | The scan area, with every code blacked out. |

## Starting a scan

An automation presses **Scan**. A doorbell press is the obvious trigger, but it
can be anything:

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

The setup checklist shows the exact button entity for your scanner.

## Frame capture

- **Auto** (the default when a scanner has both a go2rtc stream and a Home
  Assistant camera): snapshots from the Home Assistant camera straight away,
  then the go2rtc video stream once it is running. On the development camera
  the Home Assistant camera gave a first full-resolution frame in 0.8 s; the
  stream took about 6 s to start, then gave several frames a second.
- **Video stream** (go2rtc): holds the stream open for the scan. Requesting
  single JPEGs from go2rtc instead can take several seconds each.
- **Snapshots:** single images, the only choice for a Home Assistant camera on
  its own.

A scan falls back to snapshots if the stream fails, and the scanner card shows
the timing of the last scan.

## Reading distance

A code has to be about **100 px across** in the frame (4–5 px per QR module).
TagSense's codes are deliberately small (QR version 2), and the decoder copes
with an over-bright phone screen. Use the highest-resolution stream the camera
offers; a low-resolution stream (for example 896x672) still reads a code held
close. Ask people to use medium-high screen brightness (maximum can make it
worse) and to tilt the phone away from lights.

**Keep raw scan frames** (per scanner, off by default) saves up to 50 frames
for 24 hours for checking; TagSense codes in them are blacked out. The scanner
card offers them as a download.
