import { mountSuspended } from '@nuxt/test-utils/runtime'
import { axe } from 'vitest-axe'
import { describe, expect, it } from 'vitest'
import LoginPage from '~/pages/login.vue'

describe('LoginPage', () => {
  it('provides an accessible administrator-created-account login', async () => {
    const wrapper = await mountSuspended(LoginPage, { route: '/login' })

    expect(wrapper.get('input[name="username"]').attributes('autocomplete')).toBe('username')
    expect(wrapper.get('input[name="password"]').attributes('autocomplete')).toBe('current-password')
    expect(wrapper.text()).toContain('account created by your installation administrator')
    expect(wrapper.text().toLowerCase()).not.toContain('register')
    const result = await axe(wrapper.element, { rules: { region: { enabled: false } } })
    expect(result.violations).toEqual([])
  })
})
