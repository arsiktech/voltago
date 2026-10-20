import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import './vehicles.css'
import App from './App'
import { VehicleGarageProvider } from './components/VehicleGarageProvider'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
})

if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // The app remains fully usable online when registration is unavailable. Offline
      // behavior is an enhancement and must never block the route-first decision surface.
    })
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <VehicleGarageProvider><App /></VehicleGarageProvider>
    </QueryClientProvider>
  </StrictMode>,
)
