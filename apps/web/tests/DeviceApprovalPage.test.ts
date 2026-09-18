import { mountSuspended, mockNuxtImport } from '@nuxt/test-utils/runtime'
import { flushPromises } from '@vue/test-utils'
import { axe } from 'vitest-axe'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DeviceApprovalPage from '~/pages/auth/device.vue'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

mockNuxtImport('useApi', () => () => api)

describe('device approval page', () => {
  beforeEach(() => {
    api.get.mockReset()
    api.post.mockReset()
    api.get.mockResolvedValue({
      user_code: 'ABCD-EFGH',
      name: 'HPC login node',
      scopes: ['read_repository'],
      dataset_path: 'lab/images',
      expires_at: '2030-01-01T00:00:00Z'
    })
    api.post.mockResolvedValue(undefined)
  })

  it('makes the requesting device, resource, and scopes explicit before approval', async () => {
    const wrapper = await mountSuspended(DeviceApprovalPage, { route: '/auth/device' })
    await wrapper.get('input').setValue('abcd-efgh')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.get).toHaveBeenCalledWith('/api/v1/auth/device/ABCD-EFGH')
    expect(wrapper.text()).toContain('HPC login node')
    expect(wrapper.text()).toContain('lab/images')
    expect(wrapper.text()).toContain('read_repository')
    expect(wrapper.text()).toContain('Approve only if you started this login')

    const approveButton = wrapper.findAll('button').find(button => button.text().includes('Approve'))
    expect(approveButton).toBeDefined()
    await approveButton!.trigger('click')
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith('/api/v1/auth/device/ABCD-EFGH', { approve: true })
    expect(wrapper.text()).toContain('CLI authorized')
  })

  it('has no detectable baseline accessibility violations', async () => {
    const wrapper = await mountSuspended(DeviceApprovalPage, { route: '/auth/device' })
    const result = await axe(wrapper.element, { rules: { region: { enabled: false } } })

    expect(result.violations).toEqual([])
  })
})
