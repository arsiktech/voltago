/** The chosen place stays in the box. An unnamed result keeps whatever was typed. */
export function chosenPlaceText(name: string, typed: string): string {
  const place = name.trim()
  return place || typed
}
