<script setup lang="ts">
useHead({ title: 'Change password' })
const api = useApi()
const toast = useToast()
const saving = ref(false)
const form = reactive({ current_password: '', new_password: '', confirmation: '' })
const passwordsMatch = computed(() => Boolean(form.new_password) && form.new_password === form.confirmation)

async function changePassword() {
  if (!passwordsMatch.value) return
  saving.value = true
  try {
    await api.patch('/api/v1/auth/me/password', { current_password: form.current_password, new_password: form.new_password })
    Object.assign(form, { current_password: '', new_password: '', confirmation: '' })
    toast.add({ title: 'Password changed', color: 'success' })
  } catch (error) {
    toast.add({ title: 'Could not change password', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}
</script>

<template>
  <SettingsShell>
    <UCard>
      <template #header><div><h2 class="font-medium text-highlighted">Change password</h2><p class="mt-1 text-sm text-muted">Confirm your current password before choosing a new one.</p></div></template>
      <form class="space-y-4" @submit.prevent="changePassword">
        <UFormField label="Current password" required><UInput v-model="form.current_password" type="password" autocomplete="current-password" class="w-full" /></UFormField>
        <UFormField label="New password" required><UInput v-model="form.new_password" type="password" autocomplete="new-password" class="w-full" /></UFormField>
        <UFormField label="Confirm new password" :error="form.confirmation && !passwordsMatch ? 'The passwords do not match.' : undefined" required><UInput v-model="form.confirmation" type="password" autocomplete="new-password" class="w-full" /></UFormField>
        <div class="flex justify-end"><UButton type="submit" label="Change password" :loading="saving" :disabled="!form.current_password || !passwordsMatch" /></div>
      </form>
    </UCard>
  </SettingsShell>
</template>
