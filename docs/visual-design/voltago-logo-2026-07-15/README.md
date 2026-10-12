# Voltago canonical logo

This directory records the source and construction of the canonical Voltago mark introduced on
2026-07-15.

## Direction

The mark keeps the existing map-pin/G and charging-bolt idea, but consolidates the previously
conflicting web V/droplet and native G/pin treatments into one small-size-safe silhouette:

- graphite map pin shaped as a geometric `G`;
- electric-chartreuse charging bolt;
- one restrained coral route accent;
- no wordmark or baked app-icon mask.

`voltago-imagegen2-high-source.png` is the 1254 x 1254 exploratory source returned by the built-in
image-generation route from the explicit GPT Image 2 High target prompt below. Its generated
checkerboard is intentionally not a runtime asset. `voltago-mark.svg` is the clean canonical vector
reconstruction used for web output and for the three Icon Composer layers.

## Image generation prompt

> Create the final canonical VOLTA GO brand mark using GPT Image 2 High. Improve and unify the
> two supplied existing icons into one premium, unmistakable EV-charging navigation symbol.
> Preserve the recognizable idea of a map-location pin shaped like a clean capital G, crossed
> by a centered lightning bolt. Use one consistent silhouette: charcoal graphite G/pin,
> electric chartreuse bolt, and a very small warm coral route accent only at the upper-right end
> of the G. Apple-native, crisp, calm, trustworthy, modern, spatial but not photorealistic. Make
> the geometry bold and optically centered with generous safe margins so it remains readable at
> 20px. Avoid the conflicting V shape and avoid a separate droplet. No text, no letters besides
> the abstract G silhouette, no car, no plug, no gradients that muddy edges, no tiny holes, no
> excessive gloss, no mockup, no device, no rounded-square app-icon border baked into the
> artwork, no shadow outside the mark. Deliver a single 2048x2048 square master on a transparent
> background, with the isolated mark centered and occupying about 72% of the canvas. Clean
> antialiased edges, production-ready high-resolution PNG suitable as the source artwork for
> Apple Icon Composer.

## Construction

- Canvas: 1024 x 1024.
- Safe area: the mark stays inside an approximately 744 x 824 central box.
- Composer background: `layers/00-background.svg`.
- Composer foreground layers: `layers/01-pin.svg`, `layers/02-bolt.svg`, and
  `layers/03-route-accent.svg`.
- Master: `voltago-mark.svg`.
- Flattened fallback source: `voltago-app-icon-flat.svg`.
- Social and hero composition: `social-card.html`.

Do not independently redraw platform variants. Change the canonical vector and regenerate every
consumer together.
