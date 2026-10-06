# Tags and printing

## The tag

- **Family:** `tag16h5` by default, any ID from 0 to 29. Its big cells decode at
  small sizes and steep angles. The trade-off is that random texture (gravel,
  foliage) occasionally decodes as a valid ID, which the ID filter and shape
  gate deal with. Other families can be chosen per object: see below.
- **Quiet zone:** the black square needs a light border around it, about one
  cell wide (a cell is a sixth of the black square for tag16h5). Without it the
  tag will not decode. The printed tags include a white margin; leave it off
  only if the tag sits on a light surface, such as a white lid.
- **Finish:** matte if possible. Gloss causes glare.
- **Placement:** face the camera if you can. A tag lying flat (on a bin lid,
  say, which has to survive the collection truck) is seen at a steep angle:
  detection still works, with less margin. For [rotation](rotation.md), the tag
  must stay visible in every orientation, so it belongs on the lid.
- **Size:** bigger is better. Print size and camera resolution are the main
  ways to gain margin. The development setup sees a flat tag at about 50 px
  across in a 1080p frame.

## Printing

Each object's page has **Print tag**, in the object's family:

- **PNG** for paper. Print it as large as the object allows, and laminate it for
  outdoor use.
- **SVG** in millimetres: set the black square's size. It contains two separate,
  non-overlapping shapes, `black` (the tag) and `white` (the inner white cells,
  plus the margin if enabled). Import it into a slicer and give each shape its
  own filament for a two-colour 3D print, or use just the black shape with a
  vinyl cutter.

The white margin can be turned off for both.

## Tag families

Each object's form has a **Tag family** next to the tag ID. The default is
tag16h5. The detector was tuned on real frames for **tag16h5 only**; the others
use the same settings and have **only been tested on synthetic frames**.

| Family | IDs | Data grid | Phantom resistance | Size needed |
|---|---|---|---|---|
| `tag16h5` (default) | 0–29 | 4x4 | lowest | smallest |
| `tag25h9` | 0–34 | 5x5 | much better | a little bigger |
| `tag36h10` | 0–2319 | 6x6 | high | about 1.25x |
| `tag36h11` | 0–586 | 6x6 | highest | about 1.25x |

- **A smaller grid** means bigger cells, so the tag decodes when it is smaller,
  further away or blurred. It also means a lower Hamming distance, so random
  texture decodes as a valid tag more often. On 2000 random textures, tag16h5
  produced 40 false decodes, tag25h9 1, and the 36-bit families none.
- **A larger grid** needs more pixels per cell. In the synthetic tests the 6x6
  families needed about 1.25x the tag size of tag16h5 to decode as reliably
  when blurred. tag36h11 is the usual choice in robotics; tag36h10 has more IDs
  but less error resistance.

The tag ID list in the form follows the family. Objects with different families
can share a camera, even with the same ID. If you switch an object to a new tag,
press **Reset learned position** once it is in place.

The measurements and how they were made are in
[SPEC.md](https://github.com/lsnewman/ha-tagsense/blob/main/SPEC.md).
