import type { CurrentUser } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'

export function useAuth() {
  const api = useApi()
  const user = useState<CurrentUser | null>('current-user', () => null)
  const initialized = useState<boolean>('auth-initialized', () => false)

  async function load() {
    if (initialized.value) return user.value
    try {
      user.value = await api.get<CurrentUser>('/api/v1/auth/me')
    } catch (error) {
      if (!(error instanceof NiyanApiError) || error.status !== 401) throw error
      user.value = null
    } finally {
      initialized.value = true
    }
    return user.value
  }

  async function signIn(username: string, password: string) {
    await api.ensureCsrf()
    user.value = await api.post<CurrentUser>('/api/v1/auth/session', { username, password })
    api.resetCsrf()
    await api.ensureCsrf()
    initialized.value = true
    return user.value
  }

  async function signOut() {
    await api.delete('/api/v1/auth/session')
    api.resetCsrf()
    user.value = null
    initialized.value = true
    await navigateTo('/login')
  }

  return { user, initialized, load, signIn, signOut }
}
