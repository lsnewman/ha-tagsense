# Setting up Access

## Turning it on

Access needs **its own MQTT login**, kept separate from the login every app
shares, so its traffic is clearly its own. (A login cannot stop *other*
clients on the broker from publishing a fake event: that is what
[confirming events](events.md#confirming-events) is for.)

1. In the **Mosquitto broker** app (not the MQTT integration), add a login under
   *Logins*, for example `tagsense_access` with a long random password.

    !!! warning
        **Use this login for TagSense Access only.** Never give it to another
        app, integration or device (ESPHome, Zigbee2MQTT, Node-RED, a phone
        app...), and never reuse its password. Anything that can log in as
        this user can fake an access event.

2. On TagSense's **Configuration** tab, turn on `access_enabled`, and set
   `access_mqtt_username` and `access_mqtt_password` to the new login. Restart
   TagSense.
3. Open **Access** in the panel.

If the login is missing, or is the same as the shared app login, Access stays
off and the Access page says why. Presence is unaffected either way.

## The setup checklist

The Access page starts with a **Setup** checklist. It is ticked from what
TagSense has actually seen, not from a form, and each open step says how to do
it:

1. The Access MQTT login is connected.
2. A camera is added as a [scanner](scanners.md).
3. There is a [pass or a static code](codes.md).
4. **Test my setup** has read a code (below).
5. An automation has started a scan with the scanner's *Scan* button.
6. Home Assistant has asked TagSense to confirm an event (the `rest_command`
   and the [unlock blueprint](blueprint.md) are set up).

The times of the last three are kept across restarts. The checklist folds away
once everything is ticked.

## Test my setup

Each scanner has **Test my setup**, which runs a scan whose events are marked
as tests:

1. Have a code ready on a phone: open your pass (**My pass**), or press **Issue
   a 1-use test code** (valid for 15 minutes, and revoked when you close the
   test if it was not used) and open the page on the phone to show it.
2. Press **Start test scan** and hold the code up to the camera.

The result says in plain words what happened ("Verified", "nothing readable in
the window: check the scan area, brightness, distance", "not a TagSense code",
...) and what to try.

Test events carry `test: true` and never get a confirmable `event_id`, so they
**cannot unlock anything**, even with an automation that does not check the
flag. A real scan started during a test is a real scan.
