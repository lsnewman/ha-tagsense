# The unlock blueprint

For one lock or several (for example a deadbolt and a handle lock on the same
door), when a scanner reports a verified code:

1. **Confirms** the event with TagSense (once, within 30 s). Without a
   confirmation nothing happens.
2. **Unlocks** all the chosen locks together.
3. Waits until one reports unlocked: at most a minute, so one slow or offline
   lock cannot hold up the rest.
4. Waits the **auto-lock time**, fixed or read from a lock's own auto-lock
   setting (a number entity), then **locks again** every one that is still
   unlocked.
5. Runs any **extra actions** you add (a notification, say) alongside the
   relock, so a failing extra action cannot stop the locks locking again.
   Templates there can use `confirm.content.label` (who) and
   `confirm.content.code_type`.

Events from *Test my setup* are skipped.

[![Import the TagSense unlock blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Flsnewman%2Fha-tagsense%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ftagsense%2Funlock_on_confirmed_code.yaml)

It needs the `rest_command.tagsense_confirm` from the Access page's *Confirm
events* card first (see [Confirming events](events.md#confirming-events));
without it the confirm step fails and nothing unlocks.

| Input | What to pick |
|---|---|
| **TagSense scanner** | The scanner's *Code* event entity (`event.tagsense_access_..._code`). |
| **Locks** | One or more locks to open. |
| **Auto-lock time from the lock** (optional) | A number entity with the lock's own auto-lock time in seconds. |
| **Auto-lock after** | Used when no entity is set or it has no value. `0` = never lock again (leave it to the lock). |
| **Also do this when a code is confirmed** (optional) | Extra actions. |

**Updating it:** import it again from the same link and allow the overwrite.
Automations made from it keep their settings.

!!! tip "A safety net"
    If your lock does not lock again by itself after being unlocked from Home
    Assistant, also add a plain "lock again after N seconds unlocked"
    automation, so every unlock is covered, not just these.
