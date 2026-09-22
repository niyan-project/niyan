<script setup lang="ts">
import type { SystemUser, SystemUserList } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'
import { generateRandomPassword } from '~/utils/password'

definePageMeta({ middleware: 'system' })
useHead({ title: 'System users' })
const api = useApi()
const toast = useToast()
const { user } = useAuth()
const permissions = computed(() => user.value?.system_permissions || [])
const canView = computed(() => permissions.value.includes('users.view'))
const canAdd = computed(() => permissions.value.includes('users.add'))
if (!canView.value && !canAdd.value) throw createError({ statusCode: 403, statusMessage: 'You cannot administer users.' })

const showCreateForm = ref(false)
const showGeneratedPassword = ref(false)
const saving = ref(false)
const form = reactive({ username: '', password: '', email: '', first_name: '', last_name: '' })
const { data: page, error, refresh } = await useAsyncData('system-users', async () => canView.value ? api.get<SystemUserList>('/api/v1/system/users?limit=100') : { count: 0, limit: 100, offset: 0, items: [] }, { lazy: false, getCachedData: () => undefined })
const users = computed(() => page.value?.items || [])
const errorMessage = computed(() => error.value instanceof NiyanApiError ? error.value.message : error.value ? 'Users could not be loaded.' : '')
const { formatRelative } = useFormatting()

function fillRandomPassword() {
  try {
    form.password = generateRandomPassword()
    showGeneratedPassword.value = true
  } catch (generationError) {
    toast.add({ title: 'Could not generate password', description: generationError instanceof Error ? generationError.message : undefined, color: 'error' })
  }
}

async function copyGeneratedPassword() {
  await navigator.clipboard.writeText(form.password)
  toast.add({ title: 'Password copied', color: 'success' })
}

async function createUser() {
  saving.value = true
  try {
    const created = await api.post<SystemUser>('/api/v1/system/users', { ...form })
    toast.add({ title: 'User created', description: `${created.username} can now sign in.`, color: 'success' })
    Object.assign(form, { username: '', password: '', email: '', first_name: '', last_name: '' })
    showCreateForm.value = false
    if (canView.value) await refresh()
  } catch (createError) {
    toast.add({ title: 'Could not create user', description: createError instanceof Error ? createError.message : undefined, color: 'error' })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <SystemShell>
    <div class="flex items-start justify-between gap-4">
      <div><h1 class="text-2xl font-medium text-highlighted">Users</h1><p class="mt-1 text-muted">Provision and review accounts for this Niyān installation.</p></div>
      <UButton v-if="canAdd" label="New user" icon="i-lucide-user-plus" @click="showCreateForm = !showCreateForm" />
    </div>

    <UCard v-if="showCreateForm" class="mt-6">
      <template #header><div><h2 class="font-medium text-highlighted">Create user</h2><p class="mt-1 text-sm text-muted">The username also becomes the account's personal namespace path.</p></div></template>
      <form class="grid gap-4 sm:grid-cols-2" @submit.prevent="createUser">
        <UFormField label="Username" required><UInput v-model="form.username" autocomplete="off" class="w-full" /></UFormField>
        <UFormField label="Initial password" required>
          <div class="grid gap-2">
            <UInput v-model="form.password" type="password" autocomplete="new-password" class="w-full" />
            <UButton type="button" label="Generate random password" icon="i-lucide-dices" color="neutral" variant="soft" class="justify-center" @click="fillRandomPassword" />
          </div>
        </UFormField>
        <UFormField label="First name"><UInput v-model="form.first_name" autocomplete="off" class="w-full" /></UFormField>
        <UFormField label="Last name"><UInput v-model="form.last_name" autocomplete="off" class="w-full" /></UFormField>
        <UFormField label="Email" class="sm:col-span-2"><UInput v-model="form.email" type="email" autocomplete="off" class="w-full" /></UFormField>
        <div class="flex justify-end gap-2 sm:col-span-2"><UButton label="Cancel" color="neutral" variant="ghost" @click="showCreateForm = false" /><UButton type="submit" label="Create user" :loading="saving" :disabled="!form.username || !form.password" /></div>
      </form>
    </UCard>

    <UModal v-model:open="showGeneratedPassword" :dismissible="false" title="Copy the generated password" description="This password will not be shown after the user is created. Send it to the user through an appropriate private channel.">
      <template #body>
        <div class="flex flex-col gap-3 sm:flex-row">
          <UInput :model-value="form.password" readonly class="min-w-0 flex-1 font-mono" />
          <UButton label="Copy" icon="i-lucide-copy" color="neutral" variant="outline" @click="copyGeneratedPassword" />
        </div>
      </template>
      <template #footer><div class="flex w-full justify-end"><UButton label="I copied it" @click="showGeneratedPassword = false" /></div></template>
    </UModal>

    <UAlert v-if="errorMessage" class="mt-6" color="error" variant="soft" icon="i-lucide-circle-alert" :description="errorMessage" />
    <div v-if="canView" class="mt-6 overflow-hidden rounded-lg border border-default bg-default">
      <div v-for="account in users" :key="account.id" class="flex flex-col gap-2 border-b border-default p-4 last:border-b-0 sm:flex-row sm:items-center sm:justify-between">
        <div><div class="flex flex-wrap items-center gap-2"><h2 class="font-medium text-highlighted">{{ account.display_name }}</h2><UBadge v-if="account.is_superuser" color="primary" variant="subtle">Superuser</UBadge><UBadge v-else-if="account.is_staff" color="neutral" variant="subtle">Staff</UBadge><UBadge v-if="!account.is_active" color="error" variant="subtle">Inactive</UBadge></div><p class="mt-1 font-mono text-sm text-muted">{{ account.username }}</p><p v-if="account.email" class="mt-1 text-sm text-muted">{{ account.email }}</p></div>
        <div class="text-sm text-muted">Joined {{ formatRelative(account.date_joined) }}</div>
      </div>
      <div v-if="!users.length" class="p-8 text-center text-muted">No users found.</div>
    </div>
  </SystemShell>
</template>
