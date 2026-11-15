# Android app

Volta Go on Google Play is a Trusted Web Activity (TWA): `android/` builds a small app,
`tech.arsik.volta`, that opens https://voltago.app full screen in Chrome. Every feature, label and
data rule comes from the web app, so the Android app shows exactly what production serves. Build
and test steps: [android/README.md](../android/README.md).

## How the pieces connect

- **Digital Asset Links.** Chrome hides its address bar only when
  `https://voltago.app/.well-known/assetlinks.json` lists the package name and the SHA-256 fingerprint
  of the certificate that signed the installed app. The file lives in
  `frontend/public/.well-known/assetlinks.json`; Vite copies it to `frontend/dist/.well-known/`, and
  `server.py` serves it with HTTP 200 and `application/json`
  (`tests/test_android_asset_links.py`). Without a valid file the app still works, but Chrome
  shows its address bar.
- **Location.** "Use my location" in the web app triggers the Android permission prompt for Volta Go
  through location delegation. This works only after Chrome has verified the asset links: Chrome
  links the website to the app during that verification.
- **Theme.** The status and navigation bars use the page's own `theme-color` values (`#f5f6f4`
  light, `#0b0f0d` dark). The splash screen matches the page background in each mode.
- **Notifications.** The web app has no web push, so the app requests no notification permission.

## Signing fingerprints

Play App Signing is used. Two certificates matter, and `assetlinks.json` must list both:

| Certificate | Who holds the key | Where to find the SHA-256 | In `assetlinks.json` |
| --- | --- | --- | --- |
| Upload key | The owner, outside this repository | `keytool -list -v` on the upload keystore | Yes, since 13 Nov 2026 |
| App signing key | Google Play | Play Console > Test and release > App integrity > App signing (`.../keymanagement`) | Yes, since 14 Nov 2026 |

Apps installed from Play are signed with Google's app-signing key, so until that fingerprint is in
the file and deployed, Play installs open with Chrome's address bar and without location delegation.
The upload-key entry covers builds installed directly from the upload-signed bundle.

## First release checklist (Play Console side)

1. Create the app record for `tech.arsik.volta` in Play Console.
2. Upload the release bundle to a closed testing track. Play App Signing is enabled on first upload.
3. Copy the app-signing certificate SHA-256 into `sha256_cert_fingerprints` in
   `frontend/public/.well-known/assetlinks.json`, next to the upload key, then deploy the website.
4. Check `https://voltago.app/.well-known/assetlinks.json` returns 200 with the JSON, without a
   redirect or a Cloudflare challenge, and confirm with Google's tester:
   `https://digitalassetlinks.googleapis.com/v1/statements:list?source.web.site=https://voltago.app&relation=delegate_permission/common.handle_all_urls`.
5. Install from the testing track, confirm the app opens without an address bar, and confirm "Use my
   location" shows the Android permission prompt.
6. Complete the store listing, content rating and data safety form, then promote to production.

For later releases, raise `versionCode` in `android/app/build.gradle.kts` before building.
