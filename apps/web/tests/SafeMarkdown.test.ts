import { mountSuspended } from '@nuxt/test-utils/runtime'
import { axe } from 'vitest-axe'
import { describe, expect, it } from 'vitest'
import SafeMarkdown from '~/components/SafeMarkdown.vue'

describe('SafeMarkdown', () => {
  it('renders useful Markdown without accepting raw HTML', async () => {
    const wrapper = await mountSuspended(SafeMarkdown, {
      props: { content: '# Dataset notes\n\n<script>alert("no")</script>\n\n[Files](/data)' }
    })

    expect(wrapper.get('h1').text()).toBe('Dataset notes')
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.html()).toContain('&lt;script&gt;')
    expect(wrapper.get('a').attributes('href')).toBe('/data')
  })

  it('has no detectable accessibility violations', async () => {
    const wrapper = await mountSuspended(SafeMarkdown, {
      props: { content: '# Dataset notes\n\nA useful description.' }
    })

    const result = await axe(wrapper.element, { rules: { region: { enabled: false } } })
    expect(result.violations).toEqual([])
  })
})
