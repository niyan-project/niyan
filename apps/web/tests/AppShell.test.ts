import { mountSuspended } from '@nuxt/test-utils/runtime'
import { describe, expect, it } from 'vitest'
import App from '~/app.vue'

describe('App shell', () => {
  it('renders the global page loading indicator in the primary color', async () => {
    const wrapper = await mountSuspended(App, {
      global: {
        stubs: {
          NuxtLayout: { template: '<div><slot /></div>' },
          NuxtPage: true
        }
      }
    })

    const indicator = wrapper.get('.nuxt-loading-indicator')
    expect(indicator.attributes('style')).toContain('height: 2px')
    expect(indicator.attributes('style')).toContain('background: var(--ui-primary)')
  })
})
