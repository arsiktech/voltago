import { useCallback, useEffect, useState } from 'react'

type Theme = 'light' | 'dark'

function current(): Theme {
  return (document.documentElement.dataset.theme as Theme) || 'light'
}

/** Put the theme on the document. index.html runs the same two steps before first paint. */
export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
  // The browser reads this meta, not data-theme, for the UI it draws itself (form
  // controls, scrollbars, the canvas behind the page). Left at "light dark" it would
  // follow the OS and disagree with a theme the user chose here.
  document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', theme)
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(current)

  useEffect(() => {
    applyTheme(theme)
    try {
      localStorage.setItem('volta-theme', theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  const toggle = useCallback(() => {
    setTheme((t) => (t === 'light' ? 'dark' : 'light'))
  }, [])

  return { theme, toggle }
}
