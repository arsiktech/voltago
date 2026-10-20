# Owner-authorized vehicle connection

Volta Go can read an owner's authorized EV telemetry through Smartcar. The Python
backend uses the standard library and fixed Smartcar HTTPS endpoints. This consent
integration is separate from public station collection; it does not change
`scripts/source_transport.py` or its station-source exceptions.

The feature stays unavailable until the server has a valid Smartcar application
configuration. It never substitutes example readings for a connected vehicle.
Manual vehicle details and any locally chosen car photo are independent of this
connection. Smartcar vehicle identity is make, model, year and provider vehicle ID;
the backend does not manufacture a photo or request the owner's VIN or location.

## Configure a deployment

Set these variables through the deployment's secret/configuration mechanism. Do
not commit them or put them in a browser bundle:

| Variable | Value |
| --- | --- |
| `VOLTA_VEHICLE_APP_ORIGIN` | Exact public app origin, for example `https://voltago.app` |
| `VOLTA_SMARTCAR_CLIENT_ID` | Smartcar application client ID |
| `VOLTA_SMARTCAR_CLIENT_SECRET` | Smartcar application secret, server only |
| `VOLTA_SMARTCAR_MODE` | `live` (default), `test`, or `simulated` |

Register the exact callback `<app origin>/api/vehicles/callback` in the Smartcar
dashboard. Requested permissions are only `read_vehicle_info`, `read_battery`, and
`read_charge`. Configure the same read-only permissions in the provider's dashboard
if using its Vehicle Access setup. No charging, lock, payment or vehicle-control
endpoint is implemented. Availability varies by country, manufacturer, model,
subscription and the permissions the owner grants; Smartcar account setup and
commercial service prerequisites remain the deployment owner's responsibility.

The configured origin must be HTTPS, with no path, query, user information or
fragment. Literal loopback HTTP origins (`localhost`, `127.0.0.1`, or `[::1]`, with
the actual port) are accepted for local development. For Vite development, use the
browser's Vite origin and proxy `/api` to the Python backend. Register that same
development callback with Smartcar. The server never infers a callback from
`Host`/forwarded headers and never accepts a caller-supplied return URL.

Server egress needs `auth.smartcar.com:443` for OAuth token exchange and
`api.smartcar.com:443` for telemetry. The owner's browser also needs
`connect.smartcar.com` and the manufacturer's login hosts required by Smartcar.
Preserve inherited `HTTP_PROXY`/`HTTPS_PROXY` and CA trust; the transport uses the
standard urllib proxy and verified TLS handlers. Restricted managed environments
need the supported network-configuration workflow to approve those destinations.
The current development environment has no Smartcar credentials or Smartcar host
approvals, so no real account connection has been verified here.

## API and native handoff

All JSON telemetry responses use `Cache-Control: private, no-store`. Vehicle
responses never inherit the public station API's wildcard CORS. Every POST requires
`Content-Type: application/json` and `Origin` equal to the configured app origin;
unknown fields are rejected.

| Request | Result |
| --- | --- |
| `GET /api/vehicles` | Configuration/connection state and cached vehicle readings; no provider calls |
| `GET /api/vehicles/status` | Alias of the status request |
| `POST /api/vehicles/connect` with `{}` | `authorization_url` plus a browser session cookie; navigate the browser to that URL |
| `GET /api/vehicles/callback` | Verifies single-use OAuth state and exchanges the code, then redirects to the fixed app landing page |
| `POST /api/vehicles/refresh` with `{}` or `{"vehicle_id":"..."}` | Refresh authorized vehicles, at most once per session per 60 seconds |
| `POST /api/vehicles/disconnect` with `{}` | Erases this connection's credentials and cached readings, and clears its cookie |
| `GET /api/vehicles/native/start?challenge=...` | Starts an isolated native OAuth session with an S256 verifier challenge and redirects the system browser to Smartcar |
| `POST /api/vehicles/native/exchange` with `{"code":"...","verifier":"..."}` | Redeems the one-use native ticket and returns status plus a new session cookie |

For native iPhone authentication, create a random RFC 7636 verifier (43–128
unreserved characters) and pass its SHA-256 base64url digest, without padding, to
`native/start`. Open that URL with an ephemeral `ASWebAuthenticationSession`.
After successful consent, the server redirects only to
`voltagovehicle://connection/callback?code=<opaque ticket>`. The fixed scheme/host/path
must be registered by the app. On cancellation/failure, it returns that fixed
callback with `error=error` instead.

The ticket expires after 60 seconds, is bound to that session's S256 proof, and
works once. It contains no vehicle data, session cookie or OAuth access/refresh
token. The callback clears the temporary browser cookie; the native exchange
rotates the session and extends its lifetime. Store only the designated
`volta_vehicle_session` cookie in native secure storage and send it only to the
exact backend origin. Clear it on disconnect or expiry. No generic deep-link or
browser-to-native return URL is accepted.

## Data and freshness contract

Status returns `configured`, `connected`, `connectionStatus`, `reason`, and
`vehicles`. Connection states are `unavailable`, `disconnected`, `connected`, or
`reconnect`. `mode` is `live` or `simulation`; provider `test` and `simulated` modes
always produce `simulation` in both status and each vehicle's `source`.

Each vehicle includes `batteryPercent` (0–100), `rangeKm`, `chargingState`, and
`batteryCapacityKwh` where the provider actually supplies them. Smartcar's metric
unit header is requested; a contradictory response unit prevents a kilometre
reading. Actual usable battery capacity comes only from `/battery/capacity`, never
from a nominal trim lookup. Unsupported or unknown metrics remain `null`.
`chargePowerKw`, `maxAcKw`, `maxDcKw` and `connectors` currently remain `null`
because this integration has no supported source for them.

`usableEnergyKwh` estimates capacity × state of charge and is accompanied by
`usableEnergyEstimated: true`. It is returned only if both actual capacity and
charge observations are present, fresh, and within five minutes of one another.
Otherwise it remains `null`. It is an estimate, not a claim that all stored energy
is available for driving or that charging time can be predicted exactly.

`fieldObservedAt`, `fieldFetchedAt` and `fieldFreshUntil` distinguish the provider's
observation (`sc-data-age`) from when Volta retrieved it. `observedAt` and
`freshUntil` conservatively summarize available dynamic readings. Missing, invalid,
future or old observation timestamps cannot be replaced with the HTTP request
time to make data look fresh. Five minutes is the freshness limit. On provider
failure, previous readings retain their original observation timestamps and are
marked stale. A successful HTTP request does not prove that the car is currently
online, nor does polling faster force the manufacturer's data to update.

## Storage, bounds and verification

Vehicle OAuth access and refresh tokens stay in server memory. Session cookie
values and native handoff tickets are indexed only by their digests. Cookies are
`HttpOnly; SameSite=Lax; Path=/`, and `Secure` for HTTPS. OAuth state is random,
single-use, bound to its browser session and valid for ten minutes; successful
consent rotates the cookie. Sessions expire after 12 hours and are removed on
subsequent vehicle activity. Disconnect drops the session immediately. OAuth
callback query strings are removed from app request logs; exception bodies,
credentials and tokens are never included in telemetry responses or errors.

State is process-local and bounded to 512 sessions and eight vehicles per owner
connection, with at most four simultaneous provider syncs. Each request has a
five-second socket timeout, a shared twenty-second sync budget, and a 256 KiB
response cap. Upstream redirects are refused, and every host/path/method is checked
before a socket is opened. Repeat refreshes within 60 seconds return the previous
snapshot without contacting the provider.

Restarting the server forgets all vehicle connections. Multiple backend workers
would need sticky routing or a separate encrypted session store before enabling
this feature across them. Local disconnect does not revoke the grant retained by
Smartcar/the manufacturer (`providerRevoked` is `false`); the owner can revoke that
grant from their provider account. No durable token store or provider revocation
endpoint is implemented.

Run the deterministic consent, native-proof, isolation, freshness, transport and
HTTP checks with:

```bash
python3 -m unittest discover -s tests -p 'test_vehicles.py'
```

These tests do not use live credentials or demonstrate OEM compatibility. Before
production use, perform an actual owner-consent journey for each target brand in
the configured region and verify both web and iPhone callback delivery, real data
timestamps, refresh, expiry and disconnect behavior.

Protocol references: [Smartcar's official Python SDK transport and OAuth source](https://github.com/smartcar/python-sdk),
[making API requests](https://smartcar.com/docs/getting-started/how-to/making-api-requests).
