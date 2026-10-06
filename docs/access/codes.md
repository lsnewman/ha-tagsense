# Codes

There are two kinds of code, both managed on the Access page by admins.

## Passes (rotating)

For people who come and go, such as family. Each pass belongs to one Home
Assistant user, who opens it from the TagSense sidebar entry (**My pass**) in a
browser or the Home Assistant app. The code changes every 30 s, and each code
works once.

- **Add pass:** a label and the Home Assistant user who will carry it.
- **Disable / Enable:** stops it working, and back.
- **Re-enrol:** gives it a new secret, so every earlier code stops working.
  Use it if a phone is lost.
- **Delete:** removes it.

On **My pass**, the code is drawn by TagSense every period, so the phone's
clock does not matter. *Invert colours* (white on black) can help if the screen
glares on camera. Medium-high brightness works best; tilt the phone away from
lights.

!!! tip "A shortcut to My pass"
    A dashboard button with the tap action *Navigate* to `/<panel>/pass` opens
    the pass directly. The My pass page shows the exact path. See
    [Links to a page](../panel.md#links-to-a-page).

## Static codes

For sending to someone, such as a visitor or tradesperson.

- **Issue code** with a label, an optional start time, and an expiry and/or a
  number of uses (at least one). A code with no expiry still expires after a
  backstop (90 days by default, at most a year), and must stay valid for at
  least 10 minutes.
- The code is shown as a picture with a ready-to-send message. *Download
  image*, then send both.
- **Show** displays it again while it is valid; **Revoke** stops it at once;
  **Revoke all static codes** stops every one.
- **Rotate signing key** makes every static code issued so far stop working.
  Passes are not affected.

!!! note
    Assume a static code can be forwarded. That cannot be prevented, which is
    why they expire, can be limited to one use, and can be revoked.

Passes, static codes and keys are **never exported** by Backup: moving to
another install means re-adding the passes and re-issuing the codes.
