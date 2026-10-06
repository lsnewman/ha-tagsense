# Health and troubleshooting

## The Health page

The panel's **Health** page answers "is everything working?". A badge on its
tab counts anything that needs a look.

- **Things to look at:** MQTT down, go2rtc not set, a camera with no recent
  frame, an object stuck at unknown, a tag being rejected, many discarded
  frames, Access not started, a locked-out scanner.
- **Connections:** MQTT for Presence and Access, go2rtc, and whether TagSense
  can read Home Assistant's users and entity IDs.
- **Cameras:** each camera's objects, last frame, fetch failures and last error.
- **Objects, last 24 hours:** state, checks, failed checks, frames discarded,
  state changes, fetch time and resolution.
- **Access scanners:** last scan, last event, lockout, last error.
- **Recent log.**

**Download debug bundle** gives a zip to attach to a bug report: the log,
settings, each object's status and last 24 hours of checks, and the scanners'
settings and recent events. **Passwords, tokens and keys are hidden, and nothing
from the Access code store (keys, passes, codes) is included.**

## Common problems

??? question "An object stays *unknown*"
    No usable frame is arriving. Check the Health page's *Cameras* section for
    the last error, and that `go2rtc_url` is set for go2rtc streams. If frames
    arrive but are all discarded (*Frames discarded* near 100%), see the next
    question.

??? question "Many frames are discarded, though they look fine"
    The smear check is rejecting good frames, which can happen with a tight
    search area of an empty scene in low sun. Lower **Smear threshold** in
    Settings (0.2 is the default; real smeared frames score about 0.01).

??? question "The object is there but reads as absent"
    Look at the object's **Last crop** and **Recent checks**. Is the tag inside
    the search area? Is it big and sharp enough (*Tag size*)? At night, compare
    *Crop brightness* and *Crop contrast* with daytime. A larger print helps
    more than any setting.

??? question "*Tag rejected* is on"
    The tag was read but the shape gate rejected it in 3 checks in a row. The
    notification says whether it was too skewed (raise **Shape gate, max
    aspect** a little, for example to 2.5) or too small (if the object moved
    further from the camera, press **Reset learned position**).

??? question "Phantom reads (*ignored: id N*) on gravel or foliage"
    These are expected with tag16h5 and are ignored unless they match your
    object's ID with a plausible shape. Tighten the search area, or move to a
    family with more phantom resistance (see [Tag families](presence/tags.md#tag-families)).

??? question "Access: the code is not read"
    Use **Test my setup** on the scanner; its result says what to try. Usually
    the code is outside the scan area, too small in the frame (it needs about
    100 px across), or the screen is glaring: use medium-high brightness and
    tilt the phone.

??? question "Access: verified, but the lock does not open"
    Check the automation's trace. The usual cause is the confirm step:
    `rest_command.tagsense_confirm` missing, a wrong or rotated token in
    `secrets.yaml`, or the event being more than 30 s old. The setup checklist
    ticks *Home Assistant confirms events* once a confirm request has worked.

??? question "A new version is not offered in Home Assistant"
    Home Assistant's app store can keep an old copy of the repository. Press
    **Check for updates** in the app store once and wait a minute. If it still
    does not appear, restarting the Supervisor (`ha supervisor restart` in the
    Terminal & SSH app) reloads it.
