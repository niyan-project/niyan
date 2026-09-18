<script setup lang="ts">
import MarkdownIt from 'markdown-it'

const props = defineProps<{ content: string }>()
const parser = new MarkdownIt({ html: false, linkify: true, typographer: false })
const rendered = computed(() => parser.render(props.content))
</script>

<template>
  <!-- Raw HTML is disabled above; v-html renders only MarkdownIt's escaped output. -->
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div data-niyan-readme v-html="rendered" />
</template>
