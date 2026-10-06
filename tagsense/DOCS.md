# TagSense

**Full documentation: <https://lsnewman.github.io/ha-tagsense/>**

TagSense uses cameras you already have for two things, both managed from the
**TagSense panel** in the sidebar:

- **TagSense Presence:** is a tagged object (a wheelie bin, a car, a chair,
  ...) in its usual place? A printed AprilTag on it is looked for in camera
  frames, and the object is reported **present / absent / unknown**, with how
  far the tag is turned.
- **TagSense Access** (off until you turn it on): is the QR code someone holds
  up to a camera valid? TagSense verifies rotating passes and time-limited
  codes, and reports events. **It never unlocks anything**; your automations
  decide what a verified code does.

## This Configuration tab

Only what the panel needs before it can work is set here; a change needs a
restart. Everything else is in the panel and takes effect straight away.

| Option | What it is |
|---|---|
| `go2rtc_url` | Base URL of go2rtc, for example `http://<frigate-hostname>:1984`. Needed if any object or scanner uses a go2rtc stream. |
| `access_enabled`, `access_mqtt_username`, `access_mqtt_password` | Turn Access on, with its own MQTT login (see below). |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | Only to use a broker other than the Mosquitto app. |

The detection options also listed here (`max_aspect`, `burst_size`,
`sanity_min_ratio` and the rest) moved to the panel's **Settings** in 0.5.1.
They were copied there on the first start of 0.5.1 and are ignored now, so you
can clear them.

## Presence in five steps

1. Open **TagSense** in the sidebar → **Presence** → **Add object**: a name, the
   tag family and ID, and the camera.
2. On the object's page, **Print tag** and stick it on the object, facing the
   camera if you can (matte, with its white border).
3. Draw the **search area** on the live frame. It must cover every spot where
   the object might be.
4. Press **Check now**. The object appears as a **TagSense `<name>`** device;
   `binary_sensor.tagsense_<name>` is on while it is in place.
5. Use the object's **Use in automations** card for its entity IDs and
   ready-made automations.

Run it alongside whatever it replaces for a few weeks before relying on it.

## Access in five steps

1. In the **Mosquitto broker** app, add a login just for this (for example
   `tagsense_access`, with a long random password). Never use it for anything
   else.
2. Here on the Configuration tab, turn on `access_enabled` and set
   `access_mqtt_username` / `access_mqtt_password`. Restart TagSense.
3. In the panel, open **Access** and follow the **Setup** checklist: add a
   scanner (a camera), add a pass for yourself, and run **Test my setup**.
4. Add an automation that presses the scanner's **Scan** button when someone
   should show a code (for example on a doorbell press).
5. **Before unlocking anything, confirm each event** with TagSense: copy the
   `rest_command` from the *Confirm events* card, and use the unlock blueprint
   (it handles one or more locks and locks them again).

Anything on your MQTT broker could publish a fake event, which is why the
confirm step matters for locks. Details:
<https://lsnewman.github.io/ha-tagsense/access/events/>.

## Who sees what

Every Home Assistant user sees the TagSense sidebar entry. Admins get the whole
panel; everyone else only gets **My pass** (their own pass, if they have one).
A dashboard button can open a page directly: `/<panel>/pass` opens My pass,
where `<panel>` is the path you see when you open TagSense from the sidebar.

## When something is wrong

Open the panel's **Health** page: it lists anything that needs a look, and
**Download debug bundle** gives a zip for a bug report (with no passwords,
tokens, keys, passes or codes). See
<https://lsnewman.github.io/ha-tagsense/health/>.
