# Spec: availability alerts (one page, 2026-10-24)

Status: spec only. No code, Apple capability change or credential until the owner
approves it. Covers Notion VG-19 (watch) and VG-20 (deliver).

## Goal

A driver taps **Watch** on a saved charger. If a compatible connector there turns
free while the watch is active, the iPhone shows one notification. Opening it
re-reads the charger's current facts. The alert never reserves a bay or promises
the connector is still free on arrival.

## Which chargers can be watched

Only rows whose status comes from a feed with connector-level status, a per-connector
change time and a fresh fetch inside the feed's freshness window. Today:

| Feed | Area | Ports | Eligible |
| --- | --- | --- | --- |
| Endolla (CC BY 4.0) | Barcelona | ~1,400 | Yes |
| Spirii AFIR | Spain, Spirii sites | ~100 | Yes |
| Gaia | One Alboraia site | few | Yes |
| MapaREVE | Spain | largest | Yes, but blocked until the refusal lifts |
| Etecnic, Zunder | Spain | — | No: fetch-time counts only, never live |
| Directory, community, cached rows | — | — | No |

**Watch** is hidden or disabled with a reason ("This charger has no live status")
on every other row. Cached or stale rows never start or trigger a watch.

## Transition rule

- A watch names one station and the connector types compatible with the car (or all).
- **Alert** when a compatible connector moves from a fresh non-`AVAILABLE` status to a
  fresh `AVAILABLE` status. Both observations must be valid live-status snapshots.
- **No alert** for `UNKNOWN`, errors, a feed recovering from stale, or a connector
  that was already free when the watch started (the app shows that instead).
- **Flap control:** at most one alert per watch. The watch then ends.
- **Expiry:** 2 hours by default, 6 hours at most; a user can cancel at any time.
- **Outage:** if the feed stays stale for 15 minutes, the watch ends silently and the
  app shows "Watch stopped: live status unavailable" next time it opens.

## Server

- A watch store in the existing SQLite pattern (`community.db` style): watch ID,
  station ID, connector types, created/expires, last seen status per connector,
  APNs device token, delivery state. No location, no account, no vehicle data.
- One watcher loop reads the **existing** live caches on their normal cadence
  (Endolla and Spirii 60 s). It adds no upstream requests per watcher.
- Limits: 3 active watches per device, 500 in total; tokens are deleted when a watch
  ends; rows older than 24 h are purged.
- Endpoints: `POST /api/watches`, `DELETE /api/watches/{id}`, `GET /api/watches?token=…`
  (rate limited like the other write routes).

## Delivery (VG-20)

- Apple Push Notification service (APNs), visible alerts only, `apns-expiration`
  set to the watch expiry, `apns-collapse-id` set to the watch ID. No silent push.
- Payload text: "<Charger name>: a CCS2 connector showed free at 14:05. Not reserved."
  The tap opens the station sheet, which re-fetches current facts.
- **Permission** is requested only after the first **Watch** tap. If it is denied, the
  app stays usable and explains that alerts are off.

## Prerequisites the owner must provide

1. **Push Notifications capability** on the app ID and a provisioning profile with
   `aps-environment` (Apple Developer account).
2. An **APNs auth key** (`.p8`, key ID, team ID) stored on the HP host only, never in git.
3. A **transport decision**: Python's standard library has no HTTP/2, which APNs needs.
   Options: call the host's `curl` (8.5, HTTP/2 supported) from the server, or add one
   pinned dependency.

Until 1–3 exist, the watch logic and transition tests can be built and verified with
a fake push sender. No real delivery can be claimed.

## Tests

One transition matrix: occupied→free (alert), free at start (no alert), unknown→free
(no alert), stale→fresh free (no alert), flapping (one alert), cancel, expiry, outage,
server restart with persisted watches, invalid token (watch ends). Plus one physical
iPhone push once the prerequisites exist.

## Out of scope

Reservations, arrival prediction, background iOS polling, web push, alerts for
price changes, and any App Store submission.
