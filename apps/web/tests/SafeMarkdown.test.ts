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

  it('renders GitHub-style structures and opens external links in a new tab', async () => {
    const wrapper = await mountSuspended(SafeMarkdown, {
      props: { content: '> Note\n\n| File | Count |\n| --- | ---: |\n| scans | 2 |\n\n- [x] Reviewed\n\n[External](https://example.test)' }
    })

    expect(wrapper.get('blockquote').text()).toBe('Note')
    expect(wrapper.get('table').text()).toContain('scans')
    expect(wrapper.get('input[type="checkbox"]').attributes()).toHaveProperty('checked')
    expect(wrapper.get('a').attributes()).toMatchObject({ target: '_blank', rel: 'noopener noreferrer' })
  })

  it('resolves relative dataset links and images like GitHub at the displayed ref', async () => {
    const wrapper = await mountSuspended(SafeMarkdown, {
      props: { content: '[Training](../training.md) ![Scan](images/scan.png)', datasetId: 'dataset-id', datasetPath: 'lab/images', revision: 'experiment', sourcePath: 'docs/README.md' }
    })

    expect(wrapper.get('a').attributes('href')).toBe('/lab/images?tab=files&revision=experiment&path=training.md')
    expect(wrapper.get('img').attributes('src')).toBe('/api/v1/datasets/dataset-id/repository/content?revision=experiment&path=docs%2Fimages%2Fscan.png')
  })

  it('has no detectable accessibility violations', async () => {
    const wrapper = await mountSuspended(SafeMarkdown, {
      props: { content: '# Dataset notes\n\nA useful description.' }
    })

    const result = await axe(wrapper.element, { rules: { region: { enabled: false } } })
    expect(result.violations).toEqual([])
  })
})
