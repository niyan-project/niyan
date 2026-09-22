<script setup lang="ts">
import type { PermissionGroup, SystemUser } from '~/types/api'

const props = defineProps<{ users: SystemUser[], canEdit?: boolean, permissionGroups?: PermissionGroup[], canAssignGroups?: boolean }>()
const emit = defineEmits<{ updated: [user: SystemUser] }>()
const api = useApi()
const toast = useToast()
const { formatRelative } = useFormatting()
const editing = ref<SystemUser | null>(null)
const saving = ref(false)
const form = reactive({ email: '', first_name: '', last_name: '', is_active: true, is_staff: false, permission_group_ids: [] as number[] })

function openEditor(account: SystemUser) {
  editing.value = account
  Object.assign(form, { email: account.email, first_name: account.first_name, last_name: account.last_name, is_active: account.is_active, is_staff: account.is_staff, permission_group_ids: [...account.permission_group_ids] })
}

function toggleGroup(groupId: number, checked: boolean) {
  form.permission_group_ids = checked ? [...new Set([...form.permission_group_ids, groupId])] : form.permission_group_ids.filter(id => id !== groupId)
}

async function saveUser() {
  if (!editing.value) return
  saving.value = true
  try {
    const payload = { ...form, permission_group_ids: props.canAssignGroups ? form.permission_group_ids : undefined }
    const updated = await api.patch<SystemUser>(`/api/v1/system/users/${editing.value.id}`, payload)
    emit('updated', updated)
    editing.value = null
    toast.add({ title: 'User updated', color: 'success' })
  } catch (error) {
    toast.add({ title: 'Could not update user', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <div class="overflow-hidden rounded-lg border border-default bg-default">
      <div v-for="account in props.users" :key="account.id" class="flex flex-col gap-3 border-b border-default p-4 last:border-b-0 sm:flex-row sm:items-center sm:justify-between">
        <div><div class="flex flex-wrap items-center gap-2"><h2 class="font-medium text-highlighted">{{ account.display_name }}</h2><UBadge v-if="account.is_superuser" color="primary" variant="subtle">Superuser</UBadge><UBadge v-else-if="account.is_staff" color="neutral" variant="subtle">Staff</UBadge><UBadge v-if="!account.is_active" color="error" variant="subtle">Inactive</UBadge></div><p class="mt-1 font-mono text-sm text-muted">{{ account.username }}</p><p v-if="account.email" class="mt-1 text-sm text-muted">{{ account.email }}</p></div>
        <div class="flex items-center gap-3"><span class="text-sm text-muted">Joined {{ formatRelative(account.date_joined) }}</span><UButton v-if="props.canEdit" label="Edit" icon="i-lucide-pencil" color="neutral" variant="outline" size="sm" @click="openEditor(account)" /></div>
      </div>
      <div v-if="!props.users.length" class="p-8 text-center text-muted">No users found.</div>
    </div>

    <UModal :open="Boolean(editing)" title="Edit user" description="Update profile and installation access." @update:open="value => { if (!value) editing = null }">
      <template #body>
        <form class="grid gap-4 sm:grid-cols-2" @submit.prevent="saveUser">
          <UFormField label="First name"><UInput v-model="form.first_name" class="w-full" /></UFormField>
          <UFormField label="Last name"><UInput v-model="form.last_name" class="w-full" /></UFormField>
          <UFormField label="Email" class="sm:col-span-2"><UInput v-model="form.email" type="email" class="w-full" /></UFormField>
          <UFormField label="Active"><USwitch v-model="form.is_active" /></UFormField>
          <UFormField label="Staff access"><USwitch v-model="form.is_staff" /></UFormField>
          <fieldset v-if="props.canAssignGroups" class="sm:col-span-2"><legend class="mb-2 text-sm font-medium text-highlighted">Permission groups</legend><div class="grid gap-2 sm:grid-cols-2"><label v-for="group in props.permissionGroups || []" :key="group.id" class="flex items-center gap-2 rounded-md border border-default p-3"><UCheckbox :model-value="form.permission_group_ids.includes(group.id)" @update:model-value="checked => toggleGroup(group.id, Boolean(checked))" /><span class="text-sm">{{ group.name }}</span></label><p v-if="!props.permissionGroups?.length" class="text-sm text-muted">No permission groups have been created.</p></div></fieldset>
          <div class="flex justify-end gap-2 sm:col-span-2"><UButton label="Cancel" color="neutral" variant="ghost" @click="editing = null" /><UButton type="submit" label="Save changes" :loading="saving" /></div>
        </form>
      </template>
    </UModal>
  </div>
</template>
