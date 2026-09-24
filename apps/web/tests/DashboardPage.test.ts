import { mountSuspended, mockNuxtImport } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import DashboardPage from '~/pages/index.vue'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
const currentRoute = vi.hoisted(() => ({ path: '/', query: { view: 'groups' } }))
mockNuxtImport('useApi', () => () => api)
mockNuxtImport('useRoute', () => () => currentRoute)

describe('dashboard groups', () => {
  it('lists only root groups on the main Groups view', async () => {
    api.get.mockImplementation((url: string) => {
      if (url.startsWith('/api/v1/datasets')) return Promise.resolve({ items: [] })
      if (url.startsWith('/api/v1/namespaces')) return Promise.resolve({ items: [
        { id: 'root', parent_id: null, parent_path: null, path: 'lab', slug: 'lab', name: 'Lab', kind: 'group', role: null, can_create_dataset: false, can_create_group: false, can_manage: false, can_delete: false, created_at: '', updated_at: '' },
        { id: 'child', parent_id: 'root', parent_path: 'lab', path: 'lab/imaging', slug: 'imaging', name: 'Imaging', kind: 'group', role: 'reader', can_create_dataset: false, can_create_group: false, can_manage: false, can_delete: false, created_at: '', updated_at: '' }
      ] })
      throw new Error(`Unexpected GET ${url}`)
    })

    const wrapper = await mountSuspended(DashboardPage, { route: '/?view=groups' })
    await flushPromises()

    expect(wrapper.text()).toContain('Lab')
    expect(wrapper.text()).not.toContain('Imaging')
  })
})
