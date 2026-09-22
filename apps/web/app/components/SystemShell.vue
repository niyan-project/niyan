<script setup lang="ts">
const route = useRoute()
const { user } = useAuth()
const navigation = computed(() => {
  const permissions = user.value?.system_permissions || []
  return {
    niyan: [
      { label: 'Users', to: '/system/users', icon: 'i-lucide-users', visible: permissions.some(permission => permission.startsWith('users.')) },
      { label: 'Datasets', to: '/system/datasets', icon: 'i-lucide-database', visible: permissions.some(permission => permission.startsWith('datasets.')) },
      { label: 'Groups', to: '/system/groups', icon: 'i-lucide-folders', visible: permissions.some(permission => permission.startsWith('groups.')) }
    ].filter(item => item.visible),
    administration: [
      { label: 'Staff', to: '/system/staff', icon: 'i-lucide-user-cog', visible: permissions.some(permission => permission.startsWith('staff.')) },
      { label: 'Permission groups', to: '/system/permission-groups', icon: 'i-lucide-shield-check', visible: permissions.some(permission => permission.startsWith('permission_groups.')) }
    ].filter(item => item.visible)
  }
})
</script>

<template>
  <UContainer class="py-8">
    <div class="grid gap-8 md:grid-cols-[17rem_minmax(0,1fr)]">
      <aside>
        <nav aria-label="System settings">
          <p class="mb-2 px-3 text-sm font-semibold text-muted">Niyān</p>
          <div class="flex gap-1 overflow-x-auto md:flex-col">
            <UButton v-for="item in navigation.niyan" :key="item.to" :to="item.to" :label="item.label" :icon="item.icon" color="neutral" :variant="route.path === item.to ? 'soft' : 'ghost'" class="justify-start" />
          </div>
          <template v-if="navigation.administration.length">
            <p class="mb-2 mt-6 px-3 text-sm font-semibold text-muted">Administration</p>
            <div class="flex gap-1 overflow-x-auto md:flex-col">
              <UButton v-for="item in navigation.administration" :key="item.to" :to="item.to" :label="item.label" :icon="item.icon" color="neutral" :variant="route.path === item.to ? 'soft' : 'ghost'" class="justify-start" />
            </div>
          </template>
        </nav>
      </aside>
      <div class="min-w-0"><slot /></div>
    </div>
  </UContainer>
</template>
