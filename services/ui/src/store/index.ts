import { create } from 'zustand'
import {
  api,
  clearAuthToken,
  getAuthToken,
  getActiveOrgId,
  setActiveOrgId,
  type CurrentUser,
  type Organization,
} from '../lib/api'

export type AuthState = 'loading' | 'setup' | 'login' | 'ready'
export type SetupMode = 'full' | 'admin'

interface AppStore {
  authState: AuthState
  setupMode: SetupMode
  currentUser: CurrentUser | null
  organizations: Organization[]
  activeOrgId: number | null
  setAuthState: (state: AuthState) => void
  setSetupMode: (mode: SetupMode) => void
  setActiveOrg: (orgId: number) => void
  loadIdentity: () => Promise<void>
  completeLogin: () => Promise<void>
  logout: () => Promise<void>
  bootstrap: () => Promise<void>
}

export const useAppStore = create<AppStore>((set, get) => ({
  authState: 'loading',
  setupMode: 'full',
  currentUser: null,
  organizations: [],
  activeOrgId: getActiveOrgId(),

  setAuthState: (authState) => set({ authState }),
  setSetupMode: (setupMode) => set({ setupMode }),

  setActiveOrg: (orgId) => {
    setActiveOrgId(orgId)
    set({ activeOrgId: orgId })
  },

  loadIdentity: async () => {
    const me = await api.auth.me()
    const orgs = me.organizations
    const stored = getActiveOrgId()
    const active =
      stored && orgs.some((o) => o.id === stored) ? stored : orgs[0]?.id ?? null
    setActiveOrgId(active)
    set({ currentUser: me.user, organizations: orgs, activeOrgId: active })
  },

  completeLogin: async () => {
    try {
      await get().loadIdentity()
    } catch {
      // identity is best-effort; the app still works with defaults
    }
    set({ authState: 'ready' })
  },

  logout: async () => {
    try {
      await api.auth.logout()
    } catch {
      // no-op
    } finally {
      clearAuthToken()
      setActiveOrgId(null)
      set({ authState: 'login', currentUser: null, organizations: [], activeOrgId: null })
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
      try {
        await get().loadIdentity()
      } catch {
        // ignore — defaults still allow the app to function
      }
      set({ authState: 'ready' })
    } catch {
      clearAuthToken()
      set({ authState: 'login' })
    }
  },
}))
