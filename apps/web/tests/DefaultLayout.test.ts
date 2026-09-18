import { mountSuspended } from '@nuxt/test-utils/runtime'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import DefaultLayout from '~/layouts/default.vue'

describe('DefaultLayout', () => {
  it('keeps primary navigation focused and moves account actions into the user menu', async () => {
    const wrapper = await mountSuspended(DefaultLayout, {
      slots: { default: '<p>Page content</p>' }
    })

    const primaryNavigation = wrapper.get('nav[aria-label="Primary navigation"]')
    expect(primaryNavigation.text()).toContain('Datasets')
    expect(primaryNavigation.text()).toContain('Groups')
    expect(primaryNavigation.text()).not.toContain('Access tokens')
    const accountMenuButton = wrapper.get('button[aria-label="Open account menu"]')
    await accountMenuButton.trigger('click')
    await nextTick()
    expect(document.body.textContent).toContain('Settings')
    expect(document.body.textContent).toContain('Log out')
    expect(document.body.textContent).not.toContain('Security')
    expect(document.body.textContent).not.toContain('Access tokens')
  })
})
