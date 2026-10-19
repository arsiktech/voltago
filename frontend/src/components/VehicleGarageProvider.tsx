import type { ReactNode } from 'react'
import { useGarageState, VehicleContext } from '../hooks/useVehicleGarage'

export function VehicleGarageProvider({ children }: { children: ReactNode }) {
  const garage = useGarageState()
  return <VehicleContext.Provider value={garage}>{children}</VehicleContext.Provider>
}
