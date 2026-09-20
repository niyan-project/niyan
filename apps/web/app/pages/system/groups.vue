<script setup lang="ts">
import type { NamespaceList } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'

definePageMeta({ middleware: 'system' })
useHead({ title: 'System groups' })
const api = useApi()
const { user } = useAuth()
if (!(user.value?.system_permissions || []).some(permission => permission.startsWith('groups.'))) throw createError({ statusCode: 403, statusMessage: 'You cannot administer groups.' })
const { data: page, error } = await useAsyncData('system-groups', () => api.get<NamespaceList>('/api/v1/namespaces?limit=100'), { lazy: false, getCachedData: () => undefined })
const groups = computed(() => (page.value?.items || []).filter(namespace => namespace.kind === 'group'))
const errorMessage = computed(() => error.value instanceof NiyanApiError ? error.value.message : error.value ? 'Groups could not be loaded.' : '')
</script>

<template>
  <SystemShell>
    <div><h1 class="text-2xl font-medium text-highlighted">Groups</h1><p class="mt-1 text-muted">Review and open groups across this Niyān installation.</p></div>
    <UAlert v-if="errorMessage" class="mt-6" color="error" variant="soft" icon="i-lucide-circle-alert" :description="errorMessage" />
    <div class="mt-6 grid gap-3 md:grid-cols-2">
      <NuxtLink v-for="group in groups" :key="group.id" :to="`/${group.path}`" class="rounded-lg border border-default bg-default p-4 transition-colors hover:bg-elevated">
        <h2 class="font-medium text-highlighted">{{ group.name }}</h2><p class="mt-1 font-mono text-sm text-muted">{{ group.path }}</p>
      </NuxtLink>
      <UCard v-if="!groups.length && !errorMessage"><p class="text-muted">No groups found.</p></UCard>
    </div>
  </SystemShell>
</template>
