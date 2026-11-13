# Volta Go for Android

A Trusted Web Activity (TWA): a small Android app that opens https://voltago.app full screen in
the user's Chrome. The web app does all the work. This project adds the launcher icon, the splash
screen, the Android location permission and the Play package. Background and the release checklist:
[docs/android.md](../docs/android.md).

| | |
| --- | --- |
| Application ID | `tech.arsik.volta` |
| Start URL | `https://voltago.app/?source=android-twa` (the web app ignores the query) |
| Target / min SDK | 36 (Play requirement for new apps since 31 Aug 2026) / 26 |
| Library | [android-browser-helper](https://github.com/GoogleChrome/android-browser-helper) with location delegation |
| Permissions | `ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION` (for "Use my location") |

## Files

- `app/build.gradle.kts`: the website host, start URL, version and signing. Change the host here
  and in `frontend/public/.well-known/assetlinks.json` together.
- `app/src/main/AndroidManifest.xml`: the launcher activity and its Chrome settings (bar colours,
  splash, orientation), the location delegation service and the site-settings activity.
- `app/src/main/java/tech/arsik/volta/DelegationService.java`: hands Chrome's location requests to
  the Android permission prompt.
- `app/src/main/res/`: icon (the web app's maskable icon plus a monochrome bolt), the animated
  Android 12+ launch splash (`drawable-v31/splash_animated.xml`), Chrome's static splash
  (`drawable/splash_mark.xml`), and colours that match the web app's light and dark themes.

## 1. Install the tools

You need JDK 17 or newer and the Android SDK (platform 36, build-tools 36). No Android Studio needed.

```bash
brew install openjdk@17 android-commandlinetools
sdkmanager "platforms;android-36" "build-tools;36.0.0" "platform-tools"
```

Point the build at them, either with environment variables:

```bash
export JAVA_HOME="$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home"
export ANDROID_HOME="$(brew --prefix)/share/android-commandlinetools"
```

or with `sdk.dir=<path>` in `android/local.properties` (git-ignored).

## 2. Build a debug APK

```bash
cd android
./gradlew assembleDebug
```

Result: `app/build/outputs/apk/debug/app-debug.apk`. It needs no signing setup.

## 3. Sign and build the release bundle

The upload keystore and its passwords never enter the repository. Keep a properties file outside
it, readable only by you (`chmod 600`):

```properties
storeFile=/absolute/path/to/upload.jks
storePassword=...
keyAlias=upload
keyPassword=...
```

Then build with the file's path in `VOLTA_ANDROID_KEYSTORE_PROPERTIES` (or put
`volta.keystoreProperties=<path>` in `android/local.properties`):

```bash
cd android
VOLTA_ANDROID_KEYSTORE_PROPERTIES=/absolute/path/to/keystore.properties ./gradlew bundleRelease
```

Result: `app/build/outputs/bundle/release/app-release.aab`, signed with the upload key. Without the
properties file, release tasks stop with a message instead of producing an unsigned bundle.

To create a new upload key (once per app; Play App Signing holds the real signing key):

```bash
keytool -genkeypair -keystore upload.jks -storetype PKCS12 -alias upload \
  -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Volta Go upload, O=arsiktech"
```

Read its public fingerprint with `keytool -list -v -keystore upload.jks -alias upload`.

## 4. Check the bundle

```bash
bundletool validate --bundle=app/build/outputs/bundle/release/app-release.aab
bundletool dump manifest --bundle=app/build/outputs/bundle/release/app-release.aab
keytool -printcert -jarfile app/build/outputs/bundle/release/app-release.aab
```

Expect package `tech.arsik.volta`, the version you set, `targetSdkVersion` 36, only the two location
permissions (plus AndroidX's internal `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`), and the upload
certificate's SHA-256.

## 5. Test on an emulator or phone

Install the debug APK with `adb install app/build/outputs/apk/debug/app-debug.apk`, or the release
bundle with `bundletool build-apks --connected-device ... && bundletool install-apks ...`.

Chrome only hides its address bar after it verifies
`https://voltago.app/.well-known/assetlinks.json` against the app's signing certificate. Before that
file is live (or for a build signed with a key it does not list), tell Chrome to skip the check:

```bash
adb shell 'echo "_ --disable-digital-asset-link-verification-for-url=\"https://voltago.app\"" > /data/local/tmp/chrome-command-line'
adb shell am set-debug-app --persistent com.android.chrome
adb shell am force-stop com.android.chrome
```

With the check skipped, Chrome does not link the site to the app, so "Use my location" falls back
to a denial (`No service exists for scope` in logcat). Location delegation can only be tested with
the real asset links file in place. Undo the switch with `adb shell am clear-debug-app` and
`adb shell rm /data/local/tmp/chrome-command-line`.

To see why verification fails on a real build: `adb logcat | grep -e OriginVerifier -e digital_asset_links`.

## 6. Release

1. Raise `versionCode` (and `versionName`) in `app/build.gradle.kts`. Play rejects a reused code.
2. Build and check the bundle (steps 3 and 4).
3. Upload it in Play Console. The first time, add Play's app-signing certificate fingerprint to
   `assetlinks.json` and deploy the website (see [docs/android.md](../docs/android.md)).
