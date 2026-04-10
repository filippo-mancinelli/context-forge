import { create } from 'zustand'
import { api, clearAuthToken, getAuthToken } from '../lib/api'

export type AuthState = 'loading' | 'setup' | 'login' | 'ready'
export type SetupMode = 'full' | 'admin'

interface AppStore {
  authState: AuthState
  setupMode: SetupMode
  setAuthState: (state: AuthState) => void
  setSetupMode: (mode: SetupMode) => void
  logout: () => Promise<void>
  bootstrap: () => Promise<void>
}

export const useAppStore = create<AppStore>((set) => ({
  authState: 'loading',
  setupMode: 'full',

  setAuthState: (authState) => set({ authState }),
  setSetupMode: (setupMode) => set({ setupMode }),

  logout: async () => {
    try {
      await api.auth.logout()
    } catch {
      // no-op
    } finally {
      clearAuthToken()
      set({ authState: 'login' })
    }
  },

  bootstrap: async () => {
    try {
      const setup = await api.setup.status()
      if (!setup.is_configured) {
        set({
          setupMode: setup.mode === 'admin' ? 'admin' : 'full',
          authState: 'setup',
        })
        return
      }
      if (!getAuthToken()) {
        set({ authState: 'login' })
        return
      }
      await api.auth.session()
      set({ authState: 'ready' })
    } catch {
      clearAuthToken()
      set({ authState: 'login' })
    }
  },
}))
