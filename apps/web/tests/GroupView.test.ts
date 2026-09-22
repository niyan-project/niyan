import { mountSuspended, mockNuxtImport } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import GroupView from '~/components/GroupView.vue'
import type { Namespace } from '~/types/api'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() }))
const currentRoute = vi.hoisted(() => ({ query: { tab: 'members' } as Record<string, string> }))

mockNuxtImport('useApi', () => () => api)
mockNuxtImport('useRoute', () => () => currentRoute)

const group: Namespace = {
  id: 'group-id',
  parent_id: null,
  parent_path: null,
  path: 'lab',
  slug: 'lab',
  name: 'Laboratory',
  kind: 'group',
  role: 'owner',
  can_create_dataset: true,
  can_create_group: true,
  can_manage: true,
  can_delete: true,
  created_at: '2030-01-01T00:00:00Z',
  updated_at: '2030-01-01T00:00:00Z'
}

describe('group collaboration view', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
    api.patch.mockReset()
    api.delete.mockReset()
    api.get.mockImplementation((url: string) => {
      if (url === '/api/v1/namespaces?limit=100') return Promise.resolve({ items: [] })
      if (url.includes('/api/v1/datasets?')) return Promise.resolve({ items: [] })
      if (url.endsWith('/memberships')) return Promise.resolve({ items: [{ id: 1, username: 'owner', display_name: 'Owner', role: 'owner' }] })
      if (url.includes('/api/v1/auth/users?')) return Promise.resolve({ items: [{ id: 2, username: 'colleague', display_name: 'Colleague' }] })
      throw new Error(`Unexpected GET ${url}`)
    })
  })

  it('shows direct memberships and searches existing users by human identity', async () => {
    const wrapper = await mountSuspended(GroupView, { props: { group } })
    await flushPromises()

    expect(wrapper.text()).toContain('Owner')
    const searchInput = wrapper.find('input[placeholder="Search username or name"]')
    await searchInput.setValue('colleague')
    await wrapper.findAll('button').find(button => button.text() === 'Search')!.trigger('click')
    await flushPromises()

    expect(api.get).toHaveBeenCalledWith('/api/v1/auth/users?query=colleague')
    const userSelect = wrapper.findAllComponents({ name: 'USelect' })[0]
    expect(userSelect.props('items')).toEqual([{ label: 'Colleague (colleague)', value: 'colleague' }])
  })

  it('links every level of a nested group breadcrumb', async () => {
    const nested = { ...group, parent_id: 'parent-id', parent_path: 'research', path: 'research/vision', slug: 'vision' }
    const wrapper = await mountSuspended(GroupView, { props: { group: nested } })
    await flushPromises()

    const breadcrumb = wrapper.get('nav[aria-label="Group breadcrumb"]')
    expect(breadcrumb.findAll('a').map(link => link.attributes('href'))).toEqual(['/research', '/research/vision'])
  })
})
