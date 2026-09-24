import { mountSuspended, mockNuxtImport } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import UserProfilePage from '~/pages/users/[username].vue'

const api = vi.hoisted(() => ({ get: vi.fn() }))
const currentRoute = vi.hoisted(() => ({ params: { username: 'ada' } }))
mockNuxtImport('useApi', () => () => api)
mockNuxtImport('useRoute', () => () => currentRoute)

describe('user profile page', () => {
  it('shows the small authenticated identity surface', async () => {
    api.get.mockImplementation((url: string) => Promise.resolve(url === '/api/v1/auth/me' ? { id: 1, username: 'researcher', display_name: 'Researcher', email: '', is_staff: false, is_superuser: false, system_permissions: [], authentication_method: 'session', access_token: null } : { id: 7, username: 'ada', display_name: 'Ada Lovelace', first_name: 'Ada', last_name: 'Lovelace', email: 'ada@example.test' }))

    const wrapper = await mountSuspended(UserProfilePage, { route: '/users/ada' })
    await flushPromises()

    expect(api.get).toHaveBeenCalledWith('/api/v1/auth/users/ada')
    expect(wrapper.get('h1').text()).toBe('Ada Lovelace')
    expect(wrapper.get('a[href="mailto:ada@example.test"]').text()).toContain('ada@example.test')
  })
})
