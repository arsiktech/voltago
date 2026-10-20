# Volta Go bolt mark (20 October 2026)

The app icon and web mark are a single rounded lightning bolt, in two owner-approved treatments:

| Appearance | Background | Bolt |
| --- | --- | --- |
| Light | White to cool mint (#FAFDF9 to #D8EDEB) | Glass lime to green (#E4FB7A, #C8F03C, #5DBB4A) |
| Dark | Charcoal green (#1A2519 to #0C120B) | Solid electric lime #C8F03C with a soft glow |
| Tinted / clear | Derived by iOS from the same layers | |

## Source of truth

- `bolt.svg` is the canonical geometry (1024 canvas). It was traced from the owner's draft, fitted to six straight edges, made exactly parallel per stroke pair and rebuilt with true corner arcs.
- `ios/Volta/Volta/AppIcon.icon` is the Icon Composer document used on iOS 26 and later. Its only layer is `bolt.svg`; iOS renders glass, lighting, dark, tinted and clear appearances. Light and dark use separate layer treatments (glass in light, flat with a glow in dark).
- `ios/Volta/Volta/Assets.xcassets/AppIcon.appiconset` holds opaque 1024 fallbacks (light, dark, tinted) for iOS 18 to 25.
- `app-icon-*.svg`, `volta-icon-*.svg` and `social-card.svg` generate the web favicon, header marks, PNG icons and the 1200 x 630 link preview. Web PNGs live in `frontend/public/brand/`.
- `renders/ios26-*.png` are Apple `ictool` renders of the Icon Composer document in each appearance.

`tests/test_ios_release_contract.py` checks that the native layer, the fallbacks and the web marks all use this exact path.

## Small sizes

The light favicon and header mark use a deeper green gradient and a fine edge so the bolt stays legible on white at 16 to 36 px. The favicon switches with the browser's light or dark preference; the header mark follows the site theme.
