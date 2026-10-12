import type { ExpressionSpecification, LayerSpecification, TransformStyleFunction } from 'maplibre-gl'

const ROAD_SHIELDS = new Set(['highway-shield-non-us', 'highway-shield-us-interstate', 'road_shield_us'])

// The current OpenFreeMap styles compare ref_length before checking whether it
// exists. Guard that exact upstream expression before MapLibre sends it to workers.
// Unknown lengths still fail closed; valid road labels keep the original filter.
export const guardBasemapShieldFilters: TransformStyleFunction = (_previous, next) => ({
  ...next,
  layers: next.layers.map((layer): LayerSpecification => {
    if (!ROAD_SHIELDS.has(layer.id) || !('filter' in layer) || !Array.isArray(layer.filter)
      || layer.filter[0] !== 'all'
      || JSON.stringify(layer.filter[1]) !== '["<=",["get","ref_length"],6]') return layer
    return {
      ...layer,
      filter: ['all', ['==', ['typeof', ['get', 'ref_length']], 'number'], layer.filter as ExpressionSpecification],
    }
  }),
})
