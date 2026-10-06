# TagSense

A Home Assistant app (formerly "add-on") that uses cameras you already have to
answer two kinds of question.

<div class="grid cards" markdown>

-   :material-tag-search: **[TagSense Presence](presence/index.md)**

    ---

    **Is it in its usual place?** Stick a printed AprilTag on something (a
    wheelie bin, a car, a chair, a garage door). TagSense looks for that tag in
    camera frames and reports **present / absent / unknown**, and how far the
    tag is turned.

-   :material-qrcode-scan: **[TagSense Access](access/index.md)**

    ---

    **Is this a valid code?** Someone holds up a QR code on their phone to a
    camera (a doorbell, a gate, a garage). TagSense checks it and reports a
    **verified** event, or why not. Off until you turn it on, and it **never
    unlocks anything** itself.

</div>

Both are managed from a **TagSense panel** in the Home Assistant sidebar, and
everything appears in Home Assistant through MQTT discovery.

![The TagSense panel: an object's page, with its search area drawn on a live frame and the last check beside it](images/panel-object.png)

!!! warning "Status: early"
    Presence was developed and tested with a wheelie bin on one camera, by day
    and at night under the camera's IR. Access was tested on one camera, with
    codes shown on a phone. It has had one user. See
    [Limitations](limitations.md).

## Where to start

- **New here:** [Getting started](getting-started.md) installs the app and
  adds your first object.
- **Want codes at a door or gate:** [Setting up Access](access/setup.md).
- **Something not working:** [Health and troubleshooting](health.md).
- **Coming from an older version:** [Upgrading](upgrading.md).

The app's own **Documentation** tab in Home Assistant has a short guide that
links back here.
