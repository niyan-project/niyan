<script setup lang="ts">
import MarkdownIt from 'markdown-it'

const props = defineProps<{ content: string, datasetId?: string, datasetPath?: string, revision?: string, sourcePath?: string }>()
const parser = new MarkdownIt({ html: false, linkify: true, typographer: false })

function isExternal(value: string) {
  return /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(value)
}

function resolveRepositoryPath(value: string) {
  if (!props.datasetId || !props.revision || value.startsWith('#') || isExternal(value)) return null
  const directory = props.sourcePath?.includes('/') ? props.sourcePath.slice(0, props.sourcePath.lastIndexOf('/') + 1) : ''
  try {
    const resolved = new URL(value, `https://repository.invalid/${directory}`)
    if (resolved.origin !== 'https://repository.invalid') return null
    return { path: decodeURIComponent(resolved.pathname.slice(1)), hash: resolved.hash }
  } catch {
    return null
  }
}

parser.renderer.rules.link_open = (tokens, index, options, environment, renderer) => {
  const token = tokens[index]!
  const href = token.attrGet('href') || ''
  const repositoryPath = resolveRepositoryPath(href)
  if (repositoryPath && props.datasetPath) {
    const query = new URLSearchParams({ tab: 'files', revision: props.revision!, path: repositoryPath.path })
    token.attrSet('href', `/${props.datasetPath}?${query}${repositoryPath.hash}`)
  } else if (isExternal(href)) {
    token.attrSet('target', '_blank')
    token.attrSet('rel', 'noopener noreferrer')
  }
  return renderer.renderToken(tokens, index, options)
}

parser.renderer.rules.image = (tokens, index, options, environment, renderer) => {
  const token = tokens[index]!
  const repositoryPath = resolveRepositoryPath(token.attrGet('src') || '')
  if (repositoryPath) {
    const query = new URLSearchParams({ revision: props.revision!, path: repositoryPath.path })
    token.attrSet('src', `/api/v1/datasets/${props.datasetId}/repository/content?${query}`)
  }
  return renderer.renderToken(tokens, index, options)
}

const rendered = computed(() => parser.render(props.content)
  .replace(/<li>\s*\[ \]\s+/g, '<li class="task-list-item"><input type="checkbox" disabled> ')
  .replace(/<li>\s*\[[xX]\]\s+/g, '<li class="task-list-item"><input type="checkbox" checked disabled> '))
</script>

<template>
  <!-- Raw HTML is disabled above; v-html renders only MarkdownIt's escaped output. -->
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="min-w-0 max-w-full overflow-hidden" data-niyan-readme v-html="rendered" />
</template>
