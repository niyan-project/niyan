import { mountSuspended, mockNuxtImport } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProfileSettingsPage from '~/pages/settings/profile.vue'

const api = vi.hoisted(() => ({ patch: vi.fn() }))

mockNuxtImport('useApi', () => () => api)

describe('profile settings page', () => {
  beforeEach(() => {
    api.patch.mockReset()
    useState('current-user').value = {
      id: 1,
      username: 'researcher',
      display_name: 'Ada Lovelace',
      first_name: 'Ada',
      last_name: 'Lovelace',
      email: '',
      is_staff: false,
      is_superuser: false,
      system_permissions: [],
      authentication_method: 'session',
      access_token: null
    }
  })

  it('updates the signed-in user names and shared account state', async () => {
    api.patch.mockResolvedValue({ ...useState('current-user').value, display_name: 'Grace Hopper', first_name: 'Grace', last_name: 'Hopper' })
    const wrapper = await mountSuspended(ProfileSettingsPage, { route: '/settings/profile' })

    const inputs = wrapper.findAll('input')
    expect(inputs[0]!.element.value).toBe('Ada')
    expect(inputs[1]!.element.value).toBe('Lovelace')
    await inputs[0]!.setValue('Grace')
    await inputs[1]!.setValue('Hopper')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.patch).toHaveBeenCalledWith('/api/v1/auth/me/profile', { first_name: 'Grace', last_name: 'Hopper' })
    expect(useState('current-user').value.display_name).toBe('Grace Hopper')
  })
})
