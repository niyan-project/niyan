<script setup lang="ts">
import type { DeviceAuthorization } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'

useHead({ title: 'Authorize CLI' })
const route = useRoute()
const api = useApi()
const code = ref(typeof route.query.user_code === 'string' ? route.query.user_code.toUpperCase() : '')
const authorization = ref<DeviceAuthorization | null>(null)
const loading = ref(false)
const decided = ref<'approved' | 'denied' | null>(null)
const errorMessage = ref('')
const { formatDate } = useFormatting()

async function inspect() {
  if (!code.value) return
  loading.value = true
  errorMessage.value = ''
  authorization.value = null
  try {
    authorization.value = await api.get<DeviceAuthorization>(`/api/v1/auth/device/${encodeURIComponent(code.value.trim().toUpperCase())}`)
  } catch (error) {
    errorMessage.value = error instanceof NiyanApiError ? error.message : 'The request could not be loaded.'
  } finally {
    loading.value = false
  }
}

async function decide(approve: boolean) {
  if (!authorization.value) return
  loading.value = true
  try {
    await api.post(`/api/v1/auth/device/${encodeURIComponent(authorization.value.user_code)}`, { approve })
    decided.value = approve ? 'approved' : 'denied'
  } catch (error) {
    errorMessage.value = error instanceof NiyanApiError ? error.message : 'The decision could not be saved.'
  } finally {
    loading.value = false
  }
}

onMounted(() => { if (code.value) inspect() })
</script>

<template>
  <UContainer class="max-w-2xl py-10">
    <UPageHeader title="Authorize Niyān CLI" description="Review the requesting device and exactly what it will be allowed to do." />

    <UCard class="mt-6">
      <form v-if="!authorization && !decided" class="flex flex-col gap-3 sm:flex-row" @submit.prevent="inspect">
        <UFormField label="Device code" class="flex-1">
          <UInput v-model="code" placeholder="ABCD-EFGH" autocomplete="one-time-code" class="w-full font-mono uppercase" />
        </UFormField>
        <UButton type="submit" label="Continue" class="self-end" :loading="loading" :disabled="!code" />
      </form>

      <UAlert v-if="errorMessage" class="mt-4" color="error" variant="soft" icon="i-lucide-circle-alert" :description="errorMessage" />

      <div v-if="authorization && !decided" class="space-y-6">
        <div class="grid gap-4 sm:grid-cols-2">
          <div><p class="text-sm text-muted">Device</p><p class="mt-1 font-medium text-highlighted">{{ authorization.name }}</p></div>
          <div><p class="text-sm text-muted">Expires</p><p class="mt-1 text-highlighted">{{ formatDate(authorization.expires_at) }}</p></div>
          <div><p class="text-sm text-muted">Resource</p><p class="mt-1 text-highlighted">{{ authorization.dataset_path || 'Your account' }}</p></div>
          <div><p class="text-sm text-muted">Code</p><p class="mt-1 font-mono text-highlighted">{{ authorization.user_code }}</p></div>
        </div>
        <div>
          <p class="mb-2 text-sm text-muted">Requested permissions</p>
          <div class="flex flex-wrap gap-2"><UBadge v-for="scope in authorization.scopes" :key="scope" color="neutral" variant="subtle">{{ scope }}</UBadge></div>
        </div>
        <UAlert color="warning" variant="soft" icon="i-lucide-shield-alert" description="Approve only if you started this login on a device you recognize." />
        <div class="flex justify-end gap-2">
          <UButton label="Deny" color="neutral" variant="outline" :loading="loading" @click="decide(false)" />
          <UButton label="Approve" icon="i-lucide-check" :loading="loading" @click="decide(true)" />
        </div>
      </div>

      <UAlert v-if="decided" :color="decided === 'approved' ? 'success' : 'neutral'" variant="soft" :icon="decided === 'approved' ? 'i-lucide-circle-check' : 'i-lucide-circle-x'" :title="decided === 'approved' ? 'CLI authorized' : 'Request denied'" description="You may close this page and return to your terminal." />
    </UCard>
  </UContainer>
</template>
