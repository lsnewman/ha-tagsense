# Events and confirming

## Events

Each scanner's **Code** event entity reports what happened:

| `event_type` | Meaning | Attributes |
|---|---|---|
| `verified` | A valid code. **The only one to act on.** | `label`, `code_type` (`rotating`/`static`), `code_id`, `expires`, `uses_left`, `event_id`, `scanned_at`, `scanner` |
| `invalid` | Looks like a TagSense code but does not check out (forged, mistyped, from a deleted pass or an old key). | `fingerprint` only |
| `not_yet_valid` | A genuine static code before its start time. | as `verified`, plus `valid_from` |
| `expired` | A genuine code after its expiry. | as `verified` |
| `replayed` | A genuine code that was already used (a pass code shown twice, or a static code with no uses left). | as `verified` |
| `revoked` | A genuine static code that was revoked or deleted. | `code_id`, `label` |
| `locked_out` | Too many bad codes: the scanner ignores scans for 15 minutes. | `locked_until` |
| `unrecognised` | A QR code that is not a TagSense code (a parcel label...). | `fingerprint` only |
| `unavailable` | TagSense could not check, so it failed closed. | `reason` |
| `scan_timeout` | No valid code before the scan ended. | frame counts and timing |

Events from [Test my setup](setup.md#test-my-setup) also carry `test: true`.

Bad codes (`invalid`, `expired`, `replayed`, `revoked`) count towards a
**lockout**: more than 3 in one scan, or more than 5 in 10 minutes, locks the
scanner for 15 minutes. The lockout survives restarts and is cleared only in
the panel. Someone could set it off on purpose; that keeps the door shut, it
never opens it. A code's text never appears in events, logs or images: codes
that are not verified are shown only as a short fingerprint.

## Acting on `verified`

An event entity's state is the time of its last event, and it is restored when
TagSense or Home Assistant restarts. Write the automation so a restart cannot
look like a new event: ignore changes from `unavailable`/`unknown`, and check
the event is recent. For example, a notification:

```yaml
automation:
  - alias: "Front door: code verified"
    mode: queued
    triggers:
      - trigger: state
        entity_id: event.tagsense_access_front_door_code
        not_from: ["unavailable", "unknown"]
    conditions:
      - condition: state
        entity_id: event.tagsense_access_front_door_code
        attribute: event_type
        state: verified
      - condition: template
        value_template: >-
          {{ (now() - as_datetime(trigger.to_state.state)).total_seconds() < 30 }}
      - condition: template      # skip the panel's "Test my setup" scans
        value_template: "{{ not (trigger.to_state.attributes.test | default(false)) }}"
    actions:
      - action: notify.notify
        data:
          message: >-
            {{ trigger.to_state.attributes.label }} verified at the front door
            ({{ trigger.to_state.attributes.code_type }} code)
```

Notify on `invalid` and `locked_out` too, so you hear about attempts.

## Confirming events

!!! warning "Before unlocking anything, confirm the event"
    The MQTT broker cannot limit who publishes to TagSense's topics. The Home
    Assistant Mosquitto app treats every logged-in user as a superuser, so a
    Mosquitto ACL file has no effect: this was tested with Home Assistant's own
    login, an HA-user login and a broker-only login, and a fake `verified`
    event got through with each. Anything that can log in to your broker
    (every app given the MQTT service, Zigbee2MQTT, Frigate, devices with an
    MQTT password) can publish one.

- **Simple:** act on `verified` events directly. Fine for a notification or a
  light.
- **Secure:** for a lock or anything else that matters, have the automation ask
  TagSense to confirm the event first. Every `verified` event carries a
  one-time `event_id`. TagSense confirms it **once**, **within 30 s**, and only
  to a request from Home Assistant carrying a secret token. A faked MQTT
  message cannot produce an ID TagSense will confirm.

The Access page's **Confirm events** card has the token and the exact YAML for
your install, for example:

```yaml
# configuration.yaml
rest_command:
  tagsense_confirm:
    url: "http://<tagsense-hostname>:8099/api/confirm/{{ event_id }}"
    method: POST
    headers:
      authorization: !secret tagsense_confirm
    timeout: 5

# secrets.yaml
tagsense_confirm: "Bearer <token from the Access page>"
```

Then, in the automation, before the action that matters:

```yaml
      - action: rest_command.tagsense_confirm
        data:
          event_id: "{{ trigger.to_state.attributes.event_id }}"
        response_variable: confirm
      - condition: template
        value_template: "{{ confirm.status == 200 and confirm.content.confirmed }}"
      - action: lock.unlock
        target:
          entity_id: lock.front_door
```

Or use the [unlock blueprint](blueprint.md), which does all of this.

If TagSense is restarted, unreachable, or the token is wrong, nothing is
confirmed, so the lock stays shut. **Rotate token** on the Access page replaces
the token; update `secrets.yaml` afterwards. Only requests from Home
Assistant's own address reach the confirm step at all, and the token stops
anything else that shares that address (apps on the host network).
