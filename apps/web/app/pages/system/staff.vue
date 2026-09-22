<script setup lang="ts">
import type { PermissionGroupList, SystemUser, SystemUserList } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'

definePageMeta({ middleware: 'system' })
useHead({ title: 'System staff' })
const api = useApi()
const { user } = useAuth()
const permissions = computed(() => user.value?.system_permissions || [])
const canView = computed(() => permissions.value.includes('staff.view'))
const canChange = computed(() => permissions.value.includes('staff.change'))
const canAssignGroups = computed(() => permissions.value.includes('permission_groups.change'))
if (!canView.value) throw createError({ statusCode: 403, statusMessage: 'You cannot administer staff.' })
const { data: page, error } = await useAsyncData('system-staff', () => api.get<SystemUserList>('/api/v1/system/staff?limit=100'), { lazy: false, getCachedData: () => undefined })
const { data: permissionGroupPage } = await useAsyncData('system-staff-permission-groups', async () => canAssignGroups.value ? api.get<PermissionGroupList>('/api/v1/system/permission-groups') : null, { lazy: false, getCachedData: () => undefined })
const staff = computed(() => page.value?.items || [])
const errorMessage = computed(() => error.value instanceof NiyanApiError ? error.value.message : error.value ? 'Staff accounts could not be loaded.' : '')
function replaceUser(updated: SystemUser) {
  if (!page.value) return
  page.value.items = page.value.items.filter(account => account.id !== updated.id)
  if (updated.is_staff) page.value.items.push(updated)
}
</script>

<template>
  <SystemShell>
    <div><h1 class="text-2xl font-medium text-highlighted">Staff</h1><p class="mt-1 text-muted">Manage staff status and delegated permission-group membership.</p></div>
    <UAlert v-if="errorMessage" class="mt-6" color="error" variant="soft" icon="i-lucide-circle-alert" :description="errorMessage" />
    <SystemUserTable class="mt-6" :users="staff" :can-edit="canChange" :can-assign-groups="canAssignGroups" :permission-groups="permissionGroupPage?.items || []" @updated="replaceUser" />
  </SystemShell>
</template>
