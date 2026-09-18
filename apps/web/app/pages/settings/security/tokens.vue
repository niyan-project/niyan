<script setup lang="ts">
import type { AccessTokenCreated, AccessTokenList, AccessTokenMetadata, DatasetList } from '~/types/api'

useHead({ title: 'Access tokens' })
const api = useApi()
const toast = useToast()
const { formatDate } = useFormatting()
const saving = ref(false)
const showCreate = ref(false)
const oneTimeSecret = ref('')
const form = reactive({ name: '', scopes: ['read_api'] as string[], dataset_id: 'user' })
const scopeOptions = [
  { value: 'read_api', label: 'Read API', description: 'View metadata available to your account.' },
  { value: 'api', label: 'Manage API', description: 'Create and update control-plane resources.' },
  { value: 'read_repository', label: 'Read repositories', description: 'Clone, browse, and download dataset content.' },
  { value: 'write_repository', label: 'Write repositories', description: 'Push dataset commits and LFS objects.' }
]
const { data: securityData, refresh } = await useAsyncData('security-access-tokens', async () => {
  const [tokenPage, datasetPage] = await Promise.all([
    api.get<AccessTokenList>('/api/v1/auth/tokens'),
    api.get<DatasetList>('/api/v1/datasets?limit=100')
  ])
  return { tokens: tokenPage.items, datasets: datasetPage.items }
}, { lazy: false, getCachedData: () => undefined })
const tokens = computed(() => securityData.value?.tokens || [])
const datasetOptions = computed(() => [{ label: 'Entire account', value: 'user' }, ...(securityData.value?.datasets || []).map(dataset => ({ label: `${dataset.namespace_path}/${dataset.slug}`, value: dataset.id }))])

function toggleScope(scope: string, checked: boolean) {
  form.scopes = checked ? [...new Set([...form.scopes, scope])] : form.scopes.filter(item => item !== scope)
}

async function createToken() {
  saving.value = true
  try {
    const created = await api.post<AccessTokenCreated>('/api/v1/auth/tokens', { name: form.name, scopes: form.scopes, dataset_id: form.dataset_id === 'user' ? null : form.dataset_id })
    oneTimeSecret.value = created.token
    Object.assign(form, { name: '', scopes: ['read_api'], dataset_id: 'user' })
    showCreate.value = false
    await refresh()
  } catch (error) {
    toast.add({ title: 'Could not create token', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}

async function copySecret() {
  await navigator.clipboard.writeText(oneTimeSecret.value)
  toast.add({ title: 'Token copied', color: 'success' })
}

async function revoke(token: AccessTokenMetadata) {
  try {
    await api.delete(`/api/v1/auth/tokens/${token.id}`)
    toast.add({ title: 'Token revoked', color: 'success' })
    await refresh()
  } catch (error) {
    toast.add({ title: 'Could not revoke token', description: error instanceof Error ? error.message : undefined, color: 'error' })
  }
}
</script>

<template>
  <SettingsShell>
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div><h2 class="text-xl font-medium text-highlighted">Access tokens</h2><p class="mt-1 text-muted">Review credentials issued manually or through the Niyān CLI.</p></div>
      <UButton label="New token" icon="i-lucide-plus" @click="showCreate = !showCreate" />
    </div>

    <UAlert v-if="oneTimeSecret" class="mt-6" color="warning" variant="soft" icon="i-lucide-key-round" title="Copy this token now">
      <template #description>
        <p class="mb-3">Niyān cannot display this secret again.</p>
        <div class="flex flex-col gap-2 sm:flex-row"><UInput :model-value="oneTimeSecret" readonly class="flex-1 font-mono" /><UButton label="Copy" icon="i-lucide-copy" color="neutral" variant="outline" @click="copySecret" /><UButton label="I saved it" color="neutral" variant="ghost" @click="oneTimeSecret = ''" /></div>
      </template>
    </UAlert>

    <UCard v-if="showCreate" class="mt-6">
      <template #header><h3 class="font-medium text-highlighted">Create access token</h3></template>
      <form class="space-y-5" @submit.prevent="createToken">
        <div class="grid gap-4 sm:grid-cols-2">
          <UFormField label="Name" required><UInput v-model="form.name" placeholder="HPC login node" class="w-full" /></UFormField>
          <UFormField label="Resource boundary"><USelect v-model="form.dataset_id" :items="datasetOptions" class="w-full" /></UFormField>
        </div>
        <fieldset><legend class="mb-3 text-sm font-medium text-highlighted">Scopes</legend><div class="grid gap-3 sm:grid-cols-2"><label v-for="scope in scopeOptions" :key="scope.value" class="flex cursor-pointer items-start gap-3 rounded-lg border border-default p-3"><UCheckbox :model-value="form.scopes.includes(scope.value)" class="mt-0.5" @update:model-value="checked => toggleScope(scope.value, Boolean(checked))" /><span><span class="block text-sm font-medium text-highlighted">{{ scope.label }}</span><span class="mt-1 block text-sm text-muted">{{ scope.description }}</span></span></label></div></fieldset>
        <div class="flex justify-end gap-2"><UButton label="Cancel" color="neutral" variant="ghost" @click="showCreate = false" /><UButton type="submit" label="Create token" :loading="saving" :disabled="!form.name || !form.scopes.length" /></div>
      </form>
    </UCard>

    <div class="mt-6 overflow-hidden rounded-lg border border-default bg-default">
      <div v-for="token in tokens" :key="token.id" class="grid gap-4 border-b border-default p-4 last:border-b-0 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] xl:items-center">
        <div><div class="flex flex-wrap items-center gap-2"><p class="font-medium text-highlighted">{{ token.name }}</p><UBadge :color="token.active ? 'success' : 'neutral'" variant="subtle">{{ token.active ? 'Active' : 'Revoked or expired' }}</UBadge><UBadge color="neutral" variant="outline">{{ token.origin }}</UBadge></div><p class="mt-1 font-mono text-sm text-muted">{{ token.fingerprint }}</p></div>
        <div class="text-sm text-muted"><p>{{ token.dataset_path || 'Entire account' }}</p><p class="mt-1">Scopes: {{ token.scopes.join(', ') }}</p><p class="mt-1">Last used: {{ formatDate(token.last_used_at) }} · Expires: {{ formatDate(token.expires_at) }}</p></div>
        <UButton v-if="token.active" label="Revoke" icon="i-lucide-ban" color="error" variant="ghost" @click="revoke(token)" />
      </div>
      <p v-if="!tokens.length" class="p-8 text-center text-muted">No access tokens have been issued.</p>
    </div>
  </SettingsShell>
</template>
