# TagSense Presence

**Is it in its usual place?** A printed AprilTag on an object is looked for in
camera frames, and the object is reported **present / absent / unknown**,
plus how far the tag is turned.

It is meant as a cheap, deterministic alternative to an image classifier (for
example a Frigate custom model) for "is the bin out?" questions. Each frame
check is a few tens of milliseconds of CPU on a cropped region, and it either
finds your specific tag or it doesn't. One app can watch several objects on one
or more cameras; each is its own device in Home Assistant.

!!! tip "Before you rely on it"
    - **Run it in shadow mode first.** Keep your existing detector (for example
      a Frigate classifier) running alongside TagSense for a few weeks,
      covering the situations you care about (night, rain, the times the
      object moves), before basing automations on it.
    - **Compare the CPU cost.** Use the *Fetch time* and *Detect time*
      diagnostics and the app's CPU graph.
    - **Night/IR is tested on one camera.** Watch *Crop brightness* and *Crop
      contrast* next to any misses at night.

## How a check works

1. **Grab a burst of frames.** A check fetches several frames (default 5, one
   second apart) from go2rtc or a Home Assistant camera. When several objects
   share a camera, one burst serves all of them, and each object looks for its
   own tag in its own search area. If the object is already present and the
   first frames all hit, the rest of the burst is skipped.
2. **Discard broken frames.** Each frame is scored for smearing: the ratio of
   vertical to horizontal pixel differences in the search area. Corrupt
   "vertical streak" frames score about 0, real scenes (dark ones included)
   about 1. Smeared, flat and undecodable frames are discarded. **A frame
   where the tag is found always counts**, whatever its score.
3. **Look for the tag** with OpenCV's AprilTag detector. Reads with an
   implausible shape are rejected by the **shape gate**: too elongated, or far
   smaller than usual. Reads of other tag IDs are ignored, but logged and drawn
   on the image as `ignored: id N`.
4. **Decide, with debounce.**
    - If no frame is usable, the check *failed*. The state is kept until
      several failed checks in a row, then it becomes **unknown**. **A missing
      or bad frame never produces "absent".**
    - Seeing the tag is strong evidence, so **present** is reported straight
      away (with the default of 2 hits). Fewer hits than needed is
      *inconclusive*: the state is kept.
    - Not seeing it is weak evidence (glare, darkness, a person in the way), so
      **absent** is only reported after several clean misses in a row. The
      count resets if the last miss is older than 3 poll intervals (at least 5
      minutes), so misses separated by an outage cannot add up.
    - A read rejected by the shape gate is not the tag, so it counts as a miss.
      Three such checks in a row raise a *Tag rejected* alert, in case the gate
      is rejecting the real tag.
5. **Measure the rotation** while the tag is seen. See [Rotation](rotation.md).

Checks run on a timer, on demand from **Check now**, or both. Pressing Check now
just after the object moved starts confirmation checks every 45 s, so absent is
confirmed in about a minute and a half rather than after several polls.

State and settings are stored in `/data` and survive restarts.

## The learned reference

TagSense learns where your tag usually appears, and how big, from a running
average of accepted hits. This needs no setup. After 10 hits:

- the *Warning* diagnostic flags a hit more than 40% off the usual size or more
  than 0.08 of the frame from the usual position (often a phantom of your ID);
  it only warns;
- the size half of the shape gate starts rejecting reads far smaller than usual.

If the object's spot changes permanently, the average follows within about 20
hits; **Reset learned position** starts again straight away.
