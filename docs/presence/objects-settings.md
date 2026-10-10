# Objects and settings

## Objects

Objects are managed in the panel under **Presence**: **Add object**, then each
object's page. Changes apply straight away, without restarting the app.

| Field | What it does |
|---|---|
| **Name** | What the tag is on. It names the device ("TagSense Bin") and its main sensor, and can be changed at any time. |
| **ID** | A short, stable identifier (lowercase letters, digits, `_`) used in entity IDs, MQTT topics and the data folder. Made from the name unless you type one, and **cannot be changed later**. Renaming the object keeps it, so its entities and history stay. |
| **Tag family** and **Tag ID** | What is printed on the object. Two objects on the same camera need different tags (same family and ID); the same tag on different cameras is fine. |
| **Mirrored** | The camera shows the tag left-right reversed: a mirror or flip setting on the camera, or a mirrored print. Off by default. Until the tag has been seen once, a check that finds nothing also looks for it the other way round, on one frame only, and says so if it finds it. |
| **Camera** | A **go2rtc stream** (fetched as `<go2rtc_url>/api/frame.jpeg?src=<stream>`; choose the highest-resolution stream that connects directly to the camera) or a **Home Assistant camera**, read through the camera proxy. |
| **Fallback** | Try the other source when the main one fails: a go2rtc object falls back to its Home Assistant camera (often lower resolution), and a camera object to its go2rtc stream. |

Set on the object's page:

| Setting | Default | What it does |
|---|---|---|
| **Search area** | bottom-right of the frame | The part of the frame searched. It must cover **every** spot where the object might be. Tighter is faster and leaves fewer places for phantoms, but too tight and an object moved slightly reads as absent. **Fit to tag** sets it from the learned position. Also the *Crop x1/y1/x2/y2* entities. |
| **Enabled** | on | Off pauses all checks; the main sensor goes unavailable. |
| **Poll interval** | 60 s | How often a check runs on its own. `0` = only when *Check now* is pressed; otherwise at least 10. |
| **Rotation steps** | 4 | See [Rotation](rotation.md). |

Objects on the same stream share one camera worker: each burst is fetched once
and judged by all of them. Different cameras are checked in parallel. The other
objects' tags on a shared camera are recognised, so they are not logged as
ignored reads.

**Backup:** *Settings → Backup* exports all objects, their settings, the
detection settings below, each object's last 24 hours of chart history and its
learned position as text, to keep or to import into another install. Importing
adds new objects and updates ones with the same ID; it never deletes.

## Settings (shared by every object)

Changed in the panel under **Settings**, taking effect straight away. Each
field shows its range, and the default when yours differs. Saving briefly
restarts the checks.

| Setting (option name) | Default | What it does |
|---|---|---|
| Shape gate, max aspect (`max_aspect`) | `2.0` | A read counts only if the tag's longest edge is at most this many times its shortest. A square tag stays fairly square even at a steep angle (1.45–1.52 in the development setup, lying flat); phantom reads in gravel measured 2.2–6. `0` turns it off. Use the *Tag aspect* sensor's history to set it. |
| Shape gate, min size ratio (`min_size_ratio`) | `0.5` | Once 10 sightings are learned, reads smaller than this fraction of the usual size are rejected. Phantoms are usually tiny (15–40% of the real tag), while a moved object rarely shrinks by half. `0` turns it off. |
| Frames per check (`burst_size`) | `5` | 1–20. More frames are harder to fool with one bad frame, but each check takes longer and costs more CPU. |
| Seconds between frames (`burst_interval_s`) | `1.0` | Spacing frames out lets a passing person, car or headlight clear the tag. `0` grabs them back to back. |
| Hits needed for present (`present_min_hits`) | `2` | 1–3, at most the frames per check. `2` means a single stray read cannot flip the state, because a phantom rarely repeats across frames while a real tag shows in all of them. |
| Misses needed for absent (`absent_checks`) | `3` | 1–20 clean misses in a row. Lower reacts faster; higher rides out temporary obstruction. |
| Confirmation delay (`confirm_delay_s`) | `45` | Seconds between the confirmation checks after *Check now* finds no tag. Scheduled polls never start confirmations. |
| Failures before unknown (`unknown_after_failures`) | `2` | 1–5 checks in a row with no usable frame before the state becomes unknown. `1` reports outages immediately. |
| Smear threshold (`sanity_min_ratio`) | `0.2` | Frames scoring below this are treated as smeared and discarded. Most scenes score about 1.0; a tight crop of an empty, streaky scene in low sun about 0.4; smeared frames about 0.0. Lower it if *Discard rate* is high on frames that look fine. Before 0.5.1 the default was 0.5. |
| Dead-feed threshold (`sanity_min_h`) | `0.02` | Below this the crop is uniform (a black or dead feed) and the frame is discarded. Real night frames carry sensor noise well above this. |
| Log level (`log_level`) | `info` | At `info` there is one line per check, plus one per ignored or rejected read. |

!!! note "Upgrading from 0.5.0 or earlier"
    These used to be on the app's Configuration tab. On its first start 0.5.1
    copies your values into Settings; after that the Configuration tab's copies
    are ignored and can be cleared.
