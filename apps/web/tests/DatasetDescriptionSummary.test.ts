import { mountSuspended } from '@nuxt/test-utils/runtime'
import { describe, expect, it } from 'vitest'
import DatasetDescriptionSummary from '~/components/DatasetDescriptionSummary.vue'

describe('DatasetDescriptionSummary', () => {
  it('presents Markdown descriptions as clamped plain text', async () => {
    const wrapper = await mountSuspended(DatasetDescriptionSummary, {
      props: { description: '# Images\n\n**Annotated** [training set](/guide) with ![scan](scan.png).' }
    })

    expect(wrapper.text()).toBe('Images Annotated training set with scan.')
    expect(wrapper.get('p').classes()).toContain('line-clamp-2')
    expect(wrapper.get('p').classes()).toContain('break-words')
    expect(wrapper.get('p').classes()).toContain('max-w-full')
    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.find('img').exists()).toBe(false)
  })

  it('does not render an empty description', async () => {
    const wrapper = await mountSuspended(DatasetDescriptionSummary, { props: { description: '' } })

    expect(wrapper.find('p').exists()).toBe(false)
  })
})
