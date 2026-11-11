import { expect, it } from 'vitest'
import searchBar from '../components/SearchBar.tsx?raw'
import sourceMatrix from '../components/SourceMatrix.tsx?raw'

it('both geocoder inputs wait for typing to pause through the one shared hook', () => {
  for (const source of [searchBar, sourceMatrix]) {
    expect(source).toContain("import { useSettledQuery } from '../hooks/useSettledQuery'")
    expect(source).not.toContain('setTimeout')
    expect(source).not.toMatch(/useGeocode\((?!useSettledQuery|settledQuery)/)
  }
})
