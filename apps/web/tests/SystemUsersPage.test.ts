import { mountSuspended, mockNuxtImport } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SystemUsersPage from '~/pages/system/users.vue'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

mockNuxtImport('useApi', () => () => api)

describe('system users page', () => {
  beforeEach(() => {
    useState('current-user').value = {
      id: 1,
      username: 'operator',
      display_name: 'Operator',
      email: '',
      is_staff: true,
      is_superuser: false,
      system_permissions: ['users.view', 'users.add', 'groups.view', 'datasets.view'],
      authentication_method: 'session',
      access_token: null
    }
    api.get.mockReset()
    api.post.mockReset()
    api.get.mockResolvedValue({
      count: 1,
      limit: 100,
      offset: 0,
      items: [{ id: 1, username: 'operator', display_name: 'Operator', email: '', is_active: true, is_staff: true, is_superuser: false, date_joined: '2030-01-01T00:00:00Z', last_login: null }]
    })
  })

  it('uses a sectioned system sidebar and lists installation users', async () => {
    const wrapper = await mountSuspended(SystemUsersPage, { route: '/system/users' })
    await flushPromises()

    const navigation = wrapper.get('nav[aria-label="System settings"]')
    expect(navigation.text()).toContain('Administration')
    expect(navigation.text()).toContain('Users')
    expect(navigation.text()).toContain('Groups')
    expect(navigation.text()).toContain('Datasets')
    expect(wrapper.text()).toContain('Operator')
    expect(wrapper.text()).toContain('Staff')
  })

  it('provisions a user from the system interface', async () => {
    api.post.mockResolvedValue({ id: 2, username: 'new-user', display_name: 'New User' })
    const wrapper = await mountSuspended(SystemUsersPage, { route: '/system/users' })
    await flushPromises()

    await wrapper.findAll('button').find(button => button.text().includes('New user'))!.trigger('click')
    const inputs = wrapper.findAll('input')
    await inputs[0]!.setValue('new-user')
    await inputs[1]!.setValue('Niyan-Lab-7Hawk-Mosaic!')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith('/api/v1/system/users', {
      username: 'new-user',
      password: 'Niyan-Lab-7Hawk-Mosaic!',
      email: '',
      first_name: '',
      last_name: ''
    })
  })

  it('generates a strong editable password in the browser', async () => {
    const wrapper = await mountSuspended(SystemUsersPage, { route: '/system/users' })
    await flushPromises()

    await wrapper.findAll('button').find(button => button.text().includes('New user'))!.trigger('click')
    await wrapper.findAll('button').find(button => button.text().includes('Generate random password'))!.trigger('click')

    const password = wrapper.findAll('input')[1]!.element.value
    expect(password).toHaveLength(24)
    expect(password).toMatch(/[A-Z]/)
    expect(password).toMatch(/[a-z]/)
    expect(password).toMatch(/[0-9]/)
    expect(password).toMatch(/[!@#$%^&*()\-_=+]/)
    expect(document.body.textContent).toContain('Copy the generated password')
    expect(document.body.textContent).toContain('I copied it')

    await wrapper.findAll('input')[1]!.setValue(`${password}x`)
    expect(wrapper.findAll('input')[1]!.element.value).toBe(`${password}x`)
  })
})
