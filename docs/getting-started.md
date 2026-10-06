# Getting started

## Requirements

- **Home Assistant OS or Supervised** (apps need the Supervisor). Built for
  **amd64** and **aarch64** (Raspberry Pi 4/5).
- The **Mosquitto broker** app with the MQTT integration. TagSense gets the
  broker login from the Supervisor automatically.
- A **camera**, available as either or both of:
    - a **go2rtc** stream, for example the go2rtc bundled with the Frigate app
      (port 1984). Recommended: it gives full-resolution frames.
    - a **Home Assistant camera entity**, read through the camera proxy. These
      are often lower resolution (for example Frigate's detect stream), which
      leaves less margin.
- For Presence: a printed AprilTag. The panel prints one for you.
- For Access: a phone to show codes on, and a second MQTT login just for
  Access (see [Setting up Access](access/setup.md)).

## Installation

1. Add this repository to Home Assistant:

    [![Add repository to my Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flsnewman%2Fha-tagsense)

    Or open **Settings → Apps → App store → ⋮ → Repositories** and add
    `https://github.com/lsnewman/ha-tagsense`.

2. Find **TagSense** in the store and click **Install**. The image is built
   on your machine, which takes a few minutes the first time.
3. On the **Configuration** tab, set `go2rtc_url` if you will use go2rtc
   streams, for example `http://<frigate-hostname>:1984`. The Frigate app's
   hostname is on its app page; it changes if Frigate is reinstalled from
   another repository. Then start the app.
4. Open **TagSense** in the sidebar.

The Configuration tab only holds what the panel needs before it can work:
`go2rtc_url`, an optional MQTT broker override (`mqtt_host`, `mqtt_port`,
`mqtt_username`, `mqtt_password`), and Access's on switch and login.
Everything else is set in the panel and takes effect without a restart.

## Your first object

1. In the panel, open **Presence** and click **Add object**.
2. Give it a **name** (what the tag is on), pick the **tag family and ID**
   printed on it (tag16h5 and ID 5 are fine to start with), and the **camera**
   that sees it.
3. On the object's page, use **Print tag** to print the tag, and stick it on
   the object facing the camera if you can. See
   [Tags and printing](presence/tags.md).
4. Draw the **search area** on the live frame: drag the box, drag a corner, or
   draw a new one. It must cover every spot where the object might be.
5. Press **Check now**. The object appears in Home Assistant as a **TagSense
   `<name>`** device, with `binary_sensor.tagsense_<name>` on while it is in
   place.

Then look at [Entities and automations](presence/entities-automations.md) to
use it, and run it alongside whatever it replaces for a while before relying on
it.
