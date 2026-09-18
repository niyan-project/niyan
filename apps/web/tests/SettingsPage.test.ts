import { mountSuspended } from '@nuxt/test-utils/runtime'
import { describe, expect, it } from 'vitest'
import PasswordSettingsPage from '~/pages/settings/security/password.vue'

describe('Settings pages', () => {
  it('uses one sectioned sidebar without a redundant account-review page', async () => {
    const wrapper = await mountSuspended(PasswordSettingsPage, { route: '/settings/security/password' })
    const navigation = wrapper.get('nav[aria-label="Settings"]')

    expect(navigation.text()).toContain('Security')
    expect(navigation.text()).toContain('Email')
    expect(navigation.text()).toContain('Password and authentication')
    expect(navigation.text()).toContain('Access tokens')
    expect(wrapper.text()).not.toContain('Review your Niyān account')
  })
})
