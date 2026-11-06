import { describe, expect, it } from 'vitest'
import { chosenPlaceText } from './chosenPlaceText'

describe('chosen place text', () => {
  it('keeps the selected city name in the search box', () => {
    expect(chosenPlaceText('Valencia', 'vale')).toBe('Valencia')
    expect(chosenPlaceText('Home Base', 'Carrer de casa')).toBe('Home Base')
  })

  it('keeps the typed text when the selected place has no name', () => {
    expect(chosenPlaceText('   ', 'Madrid')).toBe('Madrid')
  })
})
