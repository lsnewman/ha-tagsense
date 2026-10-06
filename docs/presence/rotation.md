# Rotation

Added for bins that come back turned round. While the tag is seen, the
**Rotation** sensor reports how far it is turned from its 0° position,
clockwise as seen by the camera.

## How it is measured

TagSense measures how far the tag is turned **on its own surface** (the lid),
not in the image, so perspective does not distort it: on real frames a 90°
turn measured within 1°, where the raw image angle was nearly 30° out. All
accepted reads in a check are averaged, then rounded to **Rotation steps**.
The value only moves to a new step once it is 5° past the halfway point, so it
does not flicker. It keeps its last value between sightings.

- **Rotation steps** (1–36, default 4): 4 = 0/90/180/270°, 8 = 45° steps,
  1 = always 0 (off).
- **Set current orientation as 0°:** the way the tag is turned now becomes 0°.
  Until it is pressed, 0° comes from the first sighting. *Reset learned
  position* forgets it too.
- The rotation entities are **unavailable** while the object is not present.

On the object's page, the search-area editor draws an arrow from the tag's
centre through its top edge: solid for now, dashed for 0°.

## A "turned round" sensor

There is no built-in "turned" sensor: what counts as turned is up to you. For
example a template binary sensor (**Settings → Devices & services → Helpers →
Template**, or YAML):

```yaml
template:
  - binary_sensor:
      - name: "Bin turned round"
        state: "{{ is_state('sensor.tagsense_bin_rotation', '180') }}"
        availability: "{{ has_value('sensor.tagsense_bin_rotation') }}"
```

Or use the *Back, but turned the wrong way* example in the object's **Use in
automations** card.

!!! note "Keep the tag visible"
    The tag must stay visible in every orientation you want to tell apart, so
    put it on the lid. A tag on the side of a bin disappears when the bin is
    turned 180°; telling those apart would need a second tag with another ID
    (a second object) on the opposite side.
