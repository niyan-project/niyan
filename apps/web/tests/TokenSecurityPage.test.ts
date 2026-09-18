import { mountSuspended, mockNuxtImport } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TokenSecurityPage from '~/pages/settings/security/tokens.vue'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }))

mockNuxtImport('useApi', () => () => api)

describe('access token security page', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
    api.delete.mockReset()
    api.get.mockImplementation((url: string) => {
      if (url === '/api/v1/auth/tokens') return Promise.resolve({ count: 1, items: [{
        id: 'token-id',
        name: 'Workstation',
        origin: 'manual',
        fingerprint: 'niyan_1234…abcd',
        resource_boundary: 'user',
        dataset_id: null,
        dataset_path: null,
        scopes: ['read_api'],
        created_at: '2030-01-01T00:00:00Z',
        last_used_at: null,
        expires_at: '2030-02-01T00:00:00Z',
        revoked_at: null,
        active: true
      }] })
      if (url === '/api/v1/datasets?limit=100') return Promise.resolve({ count: 0, limit: 100, offset: 0, items: [] })
      throw new Error(`Unexpected GET ${url}`)
    })
  })

  it('shows stored token metadata without pretending the secret can be recovered', async () => {
    const wrapper = await mountSuspended(TokenSecurityPage, { route: '/settings/security/tokens' })
    await flushPromises()

    expect(wrapper.text()).toContain('Workstation')
    expect(wrapper.text()).toContain('niyan_1234…abcd')
    expect(wrapper.text()).not.toContain('niyan_secret-value')
  })

  it('displays a newly issued secret once and lets the user dismiss it', async () => {
    api.post.mockResolvedValue({ token: 'niyan_secret-value' })
    const wrapper = await mountSuspended(TokenSecurityPage, { route: '/settings/security/tokens' })
    await flushPromises()

    await wrapper.findAll('button').find(button => button.text().includes('New token'))!.trigger('click')
    await wrapper.get('input[placeholder="HPC login node"]').setValue('HPC login node')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith('/api/v1/auth/tokens', { name: 'HPC login node', scopes: ['read_api'], dataset_id: null })
    expect(wrapper.text()).toContain('Niyān cannot display this secret again')
    expect(wrapper.get('input[readonly]').element.getAttribute('value')).toBe('niyan_secret-value')

    await wrapper.findAll('button').find(button => button.text().includes('I saved it'))!.trigger('click')
    expect(wrapper.text()).not.toContain('Niyān cannot display this secret again')
  })
})
