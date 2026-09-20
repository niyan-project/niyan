<script setup lang="ts">
import type { DatasetList } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'

definePageMeta({ middleware: 'system' })
useHead({ title: 'System datasets' })
const api = useApi()
const { user } = useAuth()
if (!(user.value?.system_permissions || []).some(permission => permission.startsWith('datasets.'))) throw createError({ statusCode: 403, statusMessage: 'You cannot administer datasets.' })
const { data: page, error } = await useAsyncData('system-datasets', () => api.get<DatasetList>('/api/v1/datasets?limit=100'), { lazy: false, getCachedData: () => undefined })
const datasets = computed(() => page.value?.items || [])
const errorMessage = computed(() => error.value instanceof NiyanApiError ? error.value.message : error.value ? 'Datasets could not be loaded.' : '')
const { formatRelative } = useFormatting()
</script>

<template>
  <SystemShell>
    <div><h1 class="text-2xl font-medium text-highlighted">Datasets</h1><p class="mt-1 text-muted">Review and open datasets across this Niyān installation.</p></div>
    <UAlert v-if="errorMessage" class="mt-6" color="error" variant="soft" icon="i-lucide-circle-alert" :description="errorMessage" />
    <div class="mt-6 overflow-hidden rounded-lg border border-default bg-default">
      <NuxtLink v-for="dataset in datasets" :key="dataset.id" :to="`/${dataset.namespace_path}/${dataset.slug}`" class="flex flex-col gap-2 border-b border-default p-4 last:border-b-0 hover:bg-elevated sm:flex-row sm:items-center sm:justify-between">
        <div><h2 class="font-medium text-highlighted">{{ dataset.name }}</h2><p class="mt-1 font-mono text-sm text-muted">{{ dataset.namespace_path }}/{{ dataset.slug }}</p><p v-if="dataset.description" class="mt-2 text-sm text-muted">{{ dataset.description }}</p></div>
        <div class="text-sm text-muted">Created {{ formatRelative(dataset.created_at) }}</div>
      </NuxtLink>
      <div v-if="!datasets.length && !errorMessage" class="p-8 text-center text-muted">No datasets found.</div>
    </div>
  </SystemShell>
</template>
