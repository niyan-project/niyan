<script setup lang="ts">
import type { Dataset, Namespace } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'

const route = useRoute()
const api = useApi()
const path = computed(() => Array.isArray(route.params.path) ? route.params.path.join('/') : String(route.params.path || ''))

definePageMeta({ key: route => route.fullPath })

const { data: resolvedResource } = await useAsyncData(`resource:${route.fullPath}`, async () => {
  try {
    try {
      const resolved = await api.get<{ id: string }>(`/api/v1/datasets/resolve?path=${encodeURIComponent(path.value)}`)
      const resource = await api.get<Dataset>(`/api/v1/datasets/${resolved.id}`)
      return { kind: 'dataset' as const, resource }
    } catch (error) {
      if (!(error instanceof NiyanApiError) || error.status !== 404) throw error
      const resolved = await api.get<{ id: string }>(`/api/v1/namespaces/resolve?path=${encodeURIComponent(path.value)}`)
      const resource = await api.get<Namespace>(`/api/v1/namespaces/${resolved.id}`)
      return { kind: 'namespace' as const, resource }
    }
  } catch (error) {
    if (error instanceof NiyanApiError && error.status === 404) throw createError({ statusCode: 404, statusMessage: 'This dataset or group was not found.' })
    throw error
  }
}, { lazy: false, getCachedData: () => undefined })
const resource = computed(() => resolvedResource.value?.resource || null)
const kind = computed(() => resolvedResource.value?.kind || null)
useHead({ title: computed(() => resource.value?.name || 'Loading') })
</script>

<template>
  <DatasetView v-if="kind === 'dataset' && resource" :dataset="resource as Dataset" />
  <GroupView v-else-if="kind === 'namespace' && resource" :group="resource as Namespace" />
</template>
