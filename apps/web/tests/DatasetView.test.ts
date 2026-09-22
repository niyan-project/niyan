import { mountSuspended, mockNuxtImport } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DatasetView from '~/components/DatasetView.vue'
import type { Dataset } from '~/types/api'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }))
const currentRoute = vi.hoisted(() => ({ query: { tab: 'files' } as Record<string, string>, fullPath: '/lab/images?tab=files' }))

mockNuxtImport('useApi', () => () => api)
mockNuxtImport('useRoute', () => () => currentRoute)

const dataset: Dataset = {
  id: 'dataset-id',
  namespace_id: 'namespace-id',
  namespace_path: 'lab',
  slug: 'images',
  name: 'Images',
  description: 'Microscopy images',
  default_branch: 'main',
  role: 'owner',
  can_write: true,
  can_update: true,
  can_manage_access: true,
  can_delete: true,
  created_at: '2030-01-01T00:00:00Z'
}

function installRepositoryResponses() {
  api.get.mockImplementation((url: string) => {
    if (url.includes('refs?kind=branches')) return Promise.resolve({ items: [{ name: 'main' }] })
    if (url.includes('refs?kind=tags')) return Promise.resolve({ items: [{ name: 'v1' }] })
    if (url.includes('/repository/tree?')) return Promise.resolve({ resolved_commit: 'a'.repeat(40), path: '', items: [{ name: 'notes.txt', path: 'notes.txt', mode: '100644', object_type: 'blob', object_id: 'b'.repeat(40), size: 12 }] })
    if (url.includes('/repository/readme?')) return Promise.reject(new Error('No README'))
    if (url.includes('/repository/blob?')) return Promise.resolve({ resolved_commit: 'a'.repeat(40), path: 'notes.txt', object_id: 'b'.repeat(40), size: 12, is_lfs: false, lfs_object_id: null, lfs_size: null })
    if (url.includes('/repository/download?')) return Promise.resolve({ storage: 'git', method: 'GET', url: '/api/v1/datasets/dataset-id/repository/blob/raw?revision=commit&path=notes.txt', headers: {}, size: 12, resolved_commit: 'a'.repeat(40), path: 'notes.txt', expires_in: null })
    throw new Error(`Unexpected GET ${url}`)
  })
}

describe('dataset repository view', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
    api.put.mockReset()
    api.patch.mockReset()
    api.delete.mockReset()
    currentRoute.query = { tab: 'files' }
    currentRoute.fullPath = '/lab/images?tab=files'
    installRepositoryResponses()
  })

  it('browses repository entries and obtains an authorized action before downloading', async () => {
    const wrapper = await mountSuspended(DatasetView, { props: { dataset }, route: '/lab/images?tab=files' })
    await flushPromises()

    expect(wrapper.text()).toContain('notes.txt')
    const entry = wrapper.findAll('button').find(button => button.text().includes('notes.txt'))
    expect(entry).toBeDefined()
    await entry!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Git · 12 B')

    await wrapper.findAll('button').find(button => button.text().includes('Download'))!.trigger('click')
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/repository/download?'))
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining(`revision=${'a'.repeat(40)}`))
  })

  it('requires the exact dataset path before permanent deletion is enabled', async () => {
    currentRoute.query = { tab: 'settings' }
    currentRoute.fullPath = '/lab/images?tab=settings'
    const wrapper = await mountSuspended(DatasetView, { props: { dataset } })
    await flushPromises()

    const deleteButton = wrapper.findAll('button').find(button => button.text().includes('Delete dataset'))!
    expect(deleteButton.attributes()).toHaveProperty('disabled')
    const confirmation = wrapper.findAll('input').at(-1)!
    await confirmation.setValue('lab/image')
    expect(deleteButton.attributes()).toHaveProperty('disabled')
    await confirmation.setValue('lab/images')
    expect(deleteButton.attributes()).not.toHaveProperty('disabled')
    await deleteButton.trigger('click')
    await flushPromises()

    expect(api.delete).toHaveBeenCalledWith('/api/v1/datasets/dataset-id')
  })

  it('uses semantic buttons for every repository entry so keyboard activation is native', async () => {
    const wrapper = await mountSuspended(DatasetView, { props: { dataset }, route: '/lab/images?tab=files' })
    await flushPromises()

    const entry = wrapper.findAll('button').find(button => button.text().includes('notes.txt'))
    expect(entry?.attributes('type')).toBe('button')
  })

  it('links every dataset breadcrumb and renders its description as safe Markdown', async () => {
    const markdownDataset = { ...dataset, namespace_path: 'research/vision', description: '**Microscopy** [guide](/guide)' }
    const wrapper = await mountSuspended(DatasetView, { props: { dataset: markdownDataset }, route: '/research/vision/images?tab=files' })
    await flushPromises()

    const breadcrumb = wrapper.get('nav[aria-label="Dataset breadcrumb"]')
    expect(breadcrumb.findAll('a').map(link => link.attributes('href'))).toEqual(['/research', '/research/vision', '/research/vision/images'])
    expect(wrapper.get('strong').text()).toBe('Microscopy')
    expect(wrapper.get('a[href="/guide"]').text()).toBe('guide')
  })

  it('creates an explicit browser draft before staging a file deletion', async () => {
    const draft = { id: 'draft-id', dataset_id: dataset.id, target_branch: 'main', base_commit: 'a'.repeat(40), state: 'open', committed_oid: null, expires_at: '2030-01-02T00:00:00Z', changes: [] }
    api.post.mockResolvedValue(draft)
    api.delete.mockResolvedValue({ ...draft, changes: [{ path: 'notes.txt', operation: 'delete', storage: '', size: null, oid: null, ready: true }] })
    const wrapper = await mountSuspended(DatasetView, { props: { dataset }, route: '/lab/images?tab=files' })
    await flushPromises()

    await wrapper.find('button[aria-label="Stage deletion of notes.txt"]').trigger('click')
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith('/api/v1/datasets/dataset-id/drafts', { target_branch: 'main' })
    expect(api.delete).toHaveBeenCalledWith('/api/v1/datasets/dataset-id/drafts/draft-id/files?path=notes.txt')
    expect(wrapper.text()).toContain('Browser commit draft')
  })

  it('uses usernames and group paths as human-facing access principals', async () => {
    currentRoute.query = { tab: 'access' }
    currentRoute.fullPath = '/lab/images?tab=access'
    api.get.mockImplementation((url: string) => {
      if (url.includes('refs?kind=branches')) return Promise.resolve({ items: [{ name: 'main' }] })
      if (url.includes('refs?kind=tags')) return Promise.resolve({ items: [] })
      if (url.endsWith('/grants')) return Promise.resolve({ count: 0, items: [] })
      if (url === '/api/v1/namespaces?limit=100') return Promise.resolve({ items: [{ id: 'group-id', parent_id: null, parent_path: null, path: 'research/vision', slug: 'vision', name: 'Vision', kind: 'group', role: 'owner', can_create_dataset: true, can_create_group: true, can_manage: true, can_delete: true, created_at: '2030-01-01T00:00:00Z', updated_at: '2030-01-01T00:00:00Z' }] })
      if (url.includes('/api/v1/auth/users?')) return Promise.resolve({ items: [{ id: 7, username: 'colleague', display_name: 'Colleague' }] })
      throw new Error(`Unexpected GET ${url}`)
    })
    const wrapper = await mountSuspended(DatasetView, { props: { dataset } })
    await flushPromises()

    const searchInput = wrapper.find('input[placeholder="Search users"]')
    await searchInput.setValue('colleague')
    await wrapper.find('button[aria-label="Search users"]').trigger('click')
    await flushPromises()

    const selects = wrapper.findAllComponents({ name: 'USelect' })
    expect(selects.some(select => JSON.stringify(select.props('items')) === JSON.stringify([{ label: 'Colleague (colleague)', value: 'colleague' }]))).toBe(true)
    const principalTypeSelect = selects.find(select => JSON.stringify(select.props('items')).includes('"value":"user"'))!
    await principalTypeSelect.vm.$emit('update:modelValue', 'group')
    await flushPromises()
    expect(wrapper.findAllComponents({ name: 'USelect' }).some(select => JSON.stringify(select.props('items')) === JSON.stringify([{ label: 'research/vision', value: 'research/vision' }]))).toBe(true)
  })
})
