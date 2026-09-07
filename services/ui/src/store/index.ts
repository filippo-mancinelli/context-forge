import { create } from 'zustand'
import {
  api,
  clearAuthToken,
  getAuthToken,
  getActiveOrgId,
  setActiveOrgId,
  getActiveProjectId,
  setActiveProjectId,
  type CurrentUser,
  type Organization,
  type Project,
} from '../lib/api'
import { resolveActiveProjectId } from '../lib/tenancy'

export type AuthState = 'loading' | 'setup' | 'login' | 'ready'
export type SetupMode = 'full' | 'admin'

interface AppStore {
  authState: AuthState
  setupMode: SetupMode
  currentUser: CurrentUser | null
  organizations: Organization[]
  activeOrgId: number | null
  projects: Project[]
  activeProjectId: number | null
  setAuthState: (state: AuthState) => void
  setSetupMode: (mode: SetupMode) => void
  setActiveOrg: (orgId: number) => void
  setActiveProject: (projectId: number) => void
  loadProjects: () => Promise<void>
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
  projects: [],
  activeProjectId: getActiveProjectId(),

  setAuthState: (authState) => set({ authState }),
  setSetupMode: (setupMode) => set({ setupMode }),

  setActiveOrg: (orgId) => {
    setActiveOrgId(orgId)
    set({ activeOrgId: orgId })
  },

  setActiveProject: (projectId) => {
    setActiveProjectId(projectId)
    set({ activeProjectId: projectId })
  },

  loadProjects: async () => {
    try {
      const res = await api.projects.list()
      const active = resolveActiveProjectId(res.projects, getActiveProjectId())
      setActiveProjectId(active)
      set({ projects: res.projects, activeProjectId: active })
    } catch {
      // best-effort: on failure, clear the (possibly stale) project so the API
      // falls back to the org's default project instead of sending a foreign id
      setActiveProjectId(null)
      set({ projects: [], activeProjectId: null })
    }
  },

  loadIdentity: async () => {
    const me = await api.auth.me()
    const orgs = me.organizations
    const stored = getActiveOrgId()
    const active =
      stored && orgs.some((o) => o.id === stored) ? stored : orgs[0]?.id ?? null
    setActiveOrgId(active)
    set({ currentUser: me.user, organizations: orgs, activeOrgId: active })
    await get().loadProjects()
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
    let logoutUrl: string | undefined
    try {
      const res = await api.auth.logout()
      logoutUrl = res.logout_url
    } catch {
      // no-op
    } finally {
      clearAuthToken()
      setActiveOrgId(null)
      setActiveProjectId(null)
      set({ authState: 'login', currentUser: null, organizations: [], activeOrgId: null, projects: [], activeProjectId: null })
      if (logoutUrl) window.location.assign(logoutUrl)
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
