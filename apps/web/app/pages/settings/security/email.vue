<script setup lang="ts">
import type { CurrentUser } from '~/types/api'

useHead({ title: 'Change email' })
const api = useApi()
const toast = useToast()
const { user } = useAuth()
const saving = ref(false)
const form = reactive({ email: user.value?.email || '', current_password: '' })

async function changeEmail() {
  saving.value = true
  try {
    const updated = await api.patch<CurrentUser>('/api/v1/auth/me/email', form)
    user.value = updated
    form.current_password = ''
    toast.add({ title: 'Email changed', color: 'success' })
  } catch (error) {
    toast.add({ title: 'Could not change email', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}
</script>

<template>
  <SettingsShell>
    <UCard>
      <template #header><div><h2 class="font-medium text-highlighted">Change email</h2><p class="mt-1 text-sm text-muted">Your administrator can use this address for account-related communication.</p></div></template>
      <form class="space-y-4" @submit.prevent="changeEmail">
        <UFormField label="Email address" required><UInput v-model="form.email" type="email" autocomplete="email" class="w-full" /></UFormField>
        <UFormField label="Current password" required><UInput v-model="form.current_password" type="password" autocomplete="current-password" class="w-full" /></UFormField>
        <div class="flex justify-end"><UButton type="submit" label="Change email" :loading="saving" :disabled="!form.email || !form.current_password" /></div>
      </form>
    </UCard>
  </SettingsShell>
</template>
