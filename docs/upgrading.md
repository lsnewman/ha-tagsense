# Upgrading

The [CHANGELOG](https://github.com/lsnewman/ha-tagsense/blob/main/tagsense/CHANGELOG.md)
has every change. Notes for each version that needs one:

## To 0.5.1

- **New panel layout:** a sidebar on a computer, tabs on a phone, with Presence,
  Access, Settings, Health and My pass. Export / import moved to
  **Settings → Backup**.
- **Detection settings moved into the panel** (Settings). On its first start
  0.5.1 copies your values from the Configuration tab, so nothing changes.
  After that the Configuration tab's copies are ignored; they are now optional,
  so you can clear them. A later release removes them.
- **The smear threshold default is now 0.2** (was 0.5). Existing installs keep
  their value: if objects have read *unknown* for long periods with *Discard
  rate* near 100%, set **Smear threshold** to 0.2 in Settings.
- **Re-import the unlock blueprint** to get several locks and the skip of test
  events. Your automation keeps its settings.

## To 0.5.0

- **Nothing changes for your objects** unless you opt in: existing objects stay
  on tag16h5, and Access is off by default.
- **The TagSense sidebar entry shows for every Home Assistant user**, but
  non-admins only see their own pass.
- **Moving from a "TagSense (dev)" install:** export from it and import into
  this one. Objects bring their history, and Access scanners their settings.
  Passes, static codes and the confirm token are never exported: re-add the
  passes, re-issue the codes, and update `secrets.yaml` and the `rest_command`
  URL (this app's hostname) from the *Confirm events* card.

## Older versions

- **To 0.4.3:** new *Rotation*, *Rotation steps* and *Set current orientation as
  0°* entities. 0° is set from the first sighting after the update; press the
  button if the object was not in its normal orientation then.
- **To 0.4.2:** the *Last check* sensor is removed (the time is still the
  `last_check` attribute of *Status*), and *Phantom decodes* is renamed
  *Discarded decodes* with the same entity ID.
- **From 0.3.x:** install 0.4.1 first, so the objects in the old `objects`
  option are imported into the panel, or add them again in the panel.
- **From 0.2.x or earlier:** install 0.3.x first, so its migration runs.
