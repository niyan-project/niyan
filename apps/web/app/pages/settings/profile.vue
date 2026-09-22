<script setup lang="ts">
import type { CurrentUser } from '~/types/api'

useHead({ title: 'Profile' })
const api = useApi()
const toast = useToast()
const { user } = useAuth()
const saving = ref(false)
const form = reactive({ first_name: user.value?.first_name || '', last_name: user.value?.last_name || '' })

async function updateProfile() {
  saving.value = true
  try {
    const updated = await api.patch<CurrentUser>('/api/v1/auth/me/profile', form)
    user.value = updated
    toast.add({ title: 'Profile updated', color: 'success' })
  } catch (error) {
    toast.add({ title: 'Could not update profile', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}
</script>

<template>
  <SettingsShell>
    <UCard>
      <template #header><div><h2 class="font-medium text-highlighted">Profile</h2><p class="mt-1 text-sm text-muted">Choose the personal name shown to other members of this Niyān installation.</p></div></template>
      <form class="grid gap-4 sm:grid-cols-2" @submit.prevent="updateProfile">
        <UFormField label="First name"><UInput v-model="form.first_name" autocomplete="given-name" class="w-full" /></UFormField>
        <UFormField label="Last name"><UInput v-model="form.last_name" autocomplete="family-name" class="w-full" /></UFormField>
        <div class="flex justify-end sm:col-span-2"><UButton type="submit" label="Save profile" :loading="saving" /></div>
      </form>
    </UCard>
  </SettingsShell>
</template>
