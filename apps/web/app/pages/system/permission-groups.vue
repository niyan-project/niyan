<script setup lang="ts">
import type { PermissionGroup, PermissionGroupList, SystemPermission } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'

definePageMeta({ middleware: 'system' })
useHead({ title: 'System permission groups' })
const api = useApi()
const toast = useToast()
const { user } = useAuth()
const permissions = computed(() => user.value?.system_permissions || [])
const canView = computed(() => permissions.value.includes('permission_groups.view'))
const canAdd = computed(() => permissions.value.includes('permission_groups.add'))
const canChange = computed(() => permissions.value.includes('permission_groups.change'))
const canDelete = computed(() => permissions.value.includes('permission_groups.delete'))
if (!canView.value) throw createError({ statusCode: 403, statusMessage: 'You cannot administer permission groups.' })
const { data: page, error, refresh } = await useAsyncData('system-permission-groups', () => api.get<PermissionGroupList>('/api/v1/system/permission-groups'), { lazy: false, getCachedData: () => undefined })
const groups = computed(() => page.value?.items || [])
const errorMessage = computed(() => error.value instanceof NiyanApiError ? error.value.message : error.value ? 'Permission groups could not be loaded.' : '')
const editing = ref<PermissionGroup | null>(null)
const showForm = ref(false)
const saving = ref(false)
const form = reactive({ name: '', permissions: [] as SystemPermission[] })

function openForm(group?: PermissionGroup) {
  editing.value = group || null
  Object.assign(form, { name: group?.name || '', permissions: [...(group?.permissions || [])] })
  showForm.value = true
}
function togglePermission(permission: SystemPermission, checked: boolean) {
  form.permissions = checked ? [...new Set([...form.permissions, permission])] : form.permissions.filter(item => item !== permission)
}
async function saveGroup() {
  saving.value = true
  try {
    if (editing.value) await api.put(`/api/v1/system/permission-groups/${editing.value.id}`, { ...form })
    else await api.post('/api/v1/system/permission-groups', { ...form })
    showForm.value = false
    await refresh()
    toast.add({ title: editing.value ? 'Permission group updated' : 'Permission group created', color: 'success' })
  } catch (saveError) {
    toast.add({ title: 'Could not save permission group', description: saveError instanceof Error ? saveError.message : undefined, color: 'error' })
  } finally { saving.value = false }
}
async function removeGroup(group: PermissionGroup) {
  if (!confirm(`Delete permission group “${group.name}”? Staff users keep their accounts but lose permissions granted by this group.`)) return
  await api.delete(`/api/v1/system/permission-groups/${group.id}`)
  await refresh()
}
</script>

<template>
  <SystemShell>
    <div class="flex items-start justify-between gap-4"><div><h1 class="text-2xl font-medium text-highlighted">Permission groups</h1><p class="mt-1 text-muted">Bundle Django permissions that control delegated access to System.</p></div><UButton v-if="canAdd" label="New group" icon="i-lucide-plus" @click="openForm()" /></div>
    <UAlert v-if="errorMessage" class="mt-6" color="error" variant="soft" icon="i-lucide-circle-alert" :description="errorMessage" />
    <div class="mt-6 overflow-hidden rounded-lg border border-default bg-default"><div v-for="group in groups" :key="group.id" class="flex items-start justify-between gap-4 border-b border-default p-4 last:border-b-0"><div><h2 class="font-medium text-highlighted">{{ group.name }}</h2><div class="mt-2 flex flex-wrap gap-1"><UBadge v-for="permission in group.permissions" :key="permission" color="neutral" variant="subtle">{{ permission }}</UBadge><span v-if="!group.permissions.length" class="text-sm text-muted">No System permissions</span></div></div><div class="flex gap-1"><UButton v-if="canChange" icon="i-lucide-pencil" aria-label="Edit group" color="neutral" variant="ghost" @click="openForm(group)" /><UButton v-if="canDelete" icon="i-lucide-trash-2" aria-label="Delete group" color="error" variant="ghost" @click="removeGroup(group)" /></div></div><div v-if="!groups.length" class="p-8 text-center text-muted">No permission groups found.</div></div>
    <UModal v-model:open="showForm" :title="editing ? 'Edit permission group' : 'Create permission group'" description="Select exactly which System capabilities members receive."><template #body><form class="space-y-4" @submit.prevent="saveGroup"><UFormField label="Name" required><UInput v-model="form.name" class="w-full" /></UFormField><fieldset><legend class="mb-2 text-sm font-medium text-highlighted">Permissions</legend><div class="grid gap-2 sm:grid-cols-2"><label v-for="permission in page?.available_permissions || []" :key="permission" class="flex items-center gap-2 rounded-md border border-default p-3"><UCheckbox :model-value="form.permissions.includes(permission)" @update:model-value="checked => togglePermission(permission, Boolean(checked))" /><span class="font-mono text-sm">{{ permission }}</span></label></div></fieldset><div class="flex justify-end gap-2"><UButton label="Cancel" color="neutral" variant="ghost" @click="showForm = false" /><UButton type="submit" label="Save group" :loading="saving" :disabled="!form.name" /></div></form></template></UModal>
  </SystemShell>
</template>
