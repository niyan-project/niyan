<script setup lang="ts">
import MarkdownIt from 'markdown-it'

const props = defineProps<{ description: string }>()
const parser = new MarkdownIt({ html: false, linkify: true, typographer: false })
type MarkdownToken = ReturnType<typeof parser.parse>[number]

function tokenText(token: MarkdownToken): string {
  if (token.children) return token.children.map(tokenText).join('')
  if (token.type === 'softbreak' || token.type === 'hardbreak' || token.type.endsWith('_close')) return ' '
  if (token.type === 'image' || token.type === 'text' || token.type === 'code_inline' || token.type === 'code_block' || token.type === 'fence') return token.content
  return ''
}

const plainText = computed(() => parser.parse(props.description, {}).map(tokenText).join('').replace(/\s+/g, ' ').trim())
</script>

<template>
  <p v-if="plainText" class="mt-2 min-w-0 max-w-full break-words text-sm text-muted line-clamp-2">{{ plainText }}</p>
</template>
