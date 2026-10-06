# Limitations

- **Tested on one setup.** Presence: a wheelie bin with a tag lying flat on its
  lid, on one camera through Frigate's go2rtc, by day and at night under IR.
  Access: one camera, at full and low resolution, with codes shown on a phone.
  Other cameras, objects and placements should work, but have not been tested.
- **Night, rain and glare.** IR night frames work on the development camera:
  they have about a fifth of the daytime contrast, and 3–5 of 5 frames hit
  instead of 5 of 5, which is still enough. Rain, fog and headlight glare have
  not been specifically tested.
- **Only tag16h5 is tested on a real camera.** The other families have only been
  tested on synthetic frames.
- **Thin margin for small tags.** A small tag seen at a steep angle is near the
  limit of what the detector can read. A larger print helps more than any
  setting.
- **Phantoms.** tag16h5 trades error resistance for small size. The ID filter
  and shape gate protect the decision, but a false read of your exact ID with a
  plausible shape is still possible. Phantoms are logged and drawn so you can
  see them.
- **Rotation is only tested on simulated turns:** the angle was checked on real
  frames with the tag turned digitally in place (within 1° at every 45° step),
  not yet on a bin actually turned round.
- **Run Presence in shadow mode first.** If it replaces an existing detector,
  run both side by side for a few weeks, covering the situations you care
  about, before relying on it.
- **Access has had no independent security review.** Read
  [Security](access/security.md), confirm events before unlocking, and keep a
  way in that does not depend on it.
