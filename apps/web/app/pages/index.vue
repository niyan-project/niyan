<script setup lang="ts">
import type { Dataset, DatasetList, Namespace, NamespaceList } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'

useHead({ title: 'Dashboard' })
const route = useRoute()
const api = useApi()
const toast = useToast()
const query = ref('')
const showGroupForm = ref(false)
const showDatasetForm = ref(false)
const groupForm = reactive({ name: '', slug: '' })
const saving = ref(false)
const { data: dashboard, error: dashboardError, refresh: refreshDashboard } = await useAsyncData('dashboard-resources', async () => {
  const [datasetPage, namespacePage] = await Promise.all([
    api.get<DatasetList>('/api/v1/datasets?limit=100'),
    api.get<NamespaceList>('/api/v1/namespaces?limit=100')
  ])
  return { datasets: datasetPage.items, namespaces: namespacePage.items }
}, { lazy: false, getCachedData: () => undefined })
const datasets = computed(() => dashboard.value?.datasets || [])
const namespaces = computed(() => dashboard.value?.namespaces || [])
const showGroups = computed(() => route.query.view === 'groups')
const creatableNamespaces = computed(() => namespaces.value.filter(namespace => namespace.can_create_dataset).map(namespace => ({ label: namespace.kind === 'personal' ? `${namespace.name} (personal)` : namespace.path, value: namespace.id })))
const datasetForm = reactive({ namespace_id: creatableNamespaces.value[0]?.value || '', name: '', slug: '', description: '' })
const filteredDatasets = computed(() => datasets.value.filter(dataset => `${dataset.namespace_path}/${dataset.slug} ${dataset.name}`.toLowerCase().includes(query.value.toLowerCase())))
const groups = computed(() => namespaces.value.filter(namespace => namespace.kind === 'group' && namespace.parent_id === null && namespace.path.toLowerCase().includes(query.value.toLowerCase())))
const errorMessage = computed(() => dashboardError.value instanceof NiyanApiError ? dashboardError.value.message : dashboardError.value ? 'The dashboard could not be loaded.' : '')
const { formatRelative } = useFormatting()

async function createGroup() {
  saving.value = true
  try {
    const group = await api.post<Namespace>('/api/v1/namespaces', groupForm)
    toast.add({ title: 'Group created', description: group.path, color: 'success' })
    showGroupForm.value = false
    Object.assign(groupForm, { name: '', slug: '' })
    await refreshDashboard()
    await navigateTo(`/${group.path}`)
  } catch (error) {
    toast.add({ title: 'Could not create group', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally {
    saving.value = false
  }
}

async function createDataset() {
  saving.value = true
  try {
    const dataset = await api.post<Dataset>('/api/v1/datasets', datasetForm)
    toast.add({ title: 'Dataset created', description: `${dataset.namespace_path}/${dataset.slug}`, color: 'success' })
    showDatasetForm.value = false
    Object.assign(datasetForm, { namespace_id: creatableNamespaces.value[0]?.value || '', name: '', slug: '', description: '' })
    await navigateTo(`/${dataset.namespace_path}/${dataset.slug}`)
  } catch (error) {
    toast.add({ title: 'Could not create dataset', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <UContainer class="py-8">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-medium text-highlighted">{{ showGroups ? 'Groups' : 'Datasets' }}</h1>
        <p class="mt-1 text-muted">{{ showGroups ? 'Organize people and datasets in nested groups.' : 'Repositories available to your account.' }}</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <UButton label="New group" icon="i-lucide-users-round" color="neutral" variant="outline" @click="showGroupForm = !showGroupForm; showDatasetForm = false" />
        <UButton v-if="creatableNamespaces.length" label="New dataset" icon="i-lucide-plus" @click="showDatasetForm = !showDatasetForm; showGroupForm = false" />
      </div>
    </div>

    <UAlert v-if="errorMessage" class="mt-6" color="error" variant="soft" icon="i-lucide-circle-alert" :description="errorMessage" />

    <UCard v-if="showGroupForm" class="mt-6">
      <template #header><h2 class="font-medium text-highlighted">Create a root group</h2></template>
      <form class="grid gap-4 sm:grid-cols-2" @submit.prevent="createGroup">
        <UFormField label="Name" required><UInput v-model="groupForm.name" class="w-full" /></UFormField>
        <UFormField label="Slug" hint="Used in paths" required><UInput v-model="groupForm.slug" class="w-full" /></UFormField>
        <div class="flex justify-end gap-2 sm:col-span-2"><UButton label="Cancel" color="neutral" variant="ghost" @click="showGroupForm = false" /><UButton type="submit" label="Create group" :loading="saving" /></div>
      </form>
    </UCard>

    <UCard v-if="showDatasetForm" class="mt-6">
      <template #header><h2 class="font-medium text-highlighted">Create an empty dataset</h2></template>
      <form class="grid gap-4 sm:grid-cols-3" @submit.prevent="createDataset">
        <UFormField label="Group or personal account" required><USelect v-model="datasetForm.namespace_id" :items="creatableNamespaces" class="w-full" /></UFormField>
        <UFormField label="Name" required><UInput v-model="datasetForm.name" class="w-full" /></UFormField>
        <UFormField label="Slug" required><UInput v-model="datasetForm.slug" class="w-full" /></UFormField>
        <UFormField label="Description" hint="Optional" class="sm:col-span-3"><UTextarea v-model="datasetForm.description" :maxlength="500" autoresize class="w-full" /></UFormField>
        <UAlert class="sm:col-span-3" color="neutral" variant="soft" icon="i-lucide-upload" description="The dataset starts empty. Upload and commit files from its Files page, or populate it with the Niyān CLI." />
        <div class="flex justify-end gap-2 sm:col-span-3"><UButton label="Cancel" color="neutral" variant="ghost" @click="showDatasetForm = false" /><UButton type="submit" label="Create dataset" :loading="saving" /></div>
      </form>
    </UCard>

    <div class="mt-6"><UInput v-model="query" :placeholder="showGroups ? 'Filter groups' : 'Filter datasets'" icon="i-lucide-search" class="w-full sm:max-w-sm" /></div>

    <div v-if="showGroups" class="mt-6 grid gap-3 md:grid-cols-2">
      <NuxtLink v-for="group in groups" :key="group.id" :to="`/${group.path}`" class="rounded-lg border border-default bg-default p-4 transition-colors hover:bg-elevated">
        <div class="flex items-start justify-between gap-3"><div><h2 class="font-medium text-highlighted">{{ group.name }}</h2><p class="mt-1 font-mono text-sm text-muted">{{ group.path }}</p></div><UBadge color="neutral" variant="subtle">{{ group.role || 'system' }}</UBadge></div>
      </NuxtLink>
      <UCard v-if="!groups.length"><p class="text-muted">No groups match this view.</p></UCard>
    </div>
    <div v-else class="mt-6 overflow-hidden rounded-lg border border-default bg-default">
      <NuxtLink v-for="dataset in filteredDatasets" :key="dataset.id" :to="`/${dataset.namespace_path}/${dataset.slug}`" class="flex flex-col gap-2 border-b border-default p-4 last:border-b-0 hover:bg-elevated sm:flex-row sm:items-center sm:justify-between">
        <div class="min-w-0 flex-1"><h2 class="font-medium text-highlighted">{{ dataset.name }}</h2><p class="mt-1 truncate font-mono text-sm text-muted">{{ dataset.namespace_path }}/{{ dataset.slug }}</p><DatasetDescriptionSummary :description="dataset.description" /></div>
        <div class="flex shrink-0 items-center gap-3 text-sm text-muted"><UBadge color="neutral" variant="subtle">{{ dataset.role || 'system' }}</UBadge><span>{{ formatRelative(dataset.created_at) }}</span></div>
      </NuxtLink>
      <div v-if="!filteredDatasets.length" class="p-8 text-center text-muted">No datasets match this view.</div>
    </div>
  </UContainer>
</template>
