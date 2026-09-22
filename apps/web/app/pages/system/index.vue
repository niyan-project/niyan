<script setup lang="ts">
definePageMeta({ middleware: 'system' })
const { user } = useAuth()
const permissions = user.value?.system_permissions || []
const destination = permissions.some(permission => permission.startsWith('users.'))
  ? '/system/users'
  : permissions.some(permission => permission.startsWith('groups.'))
    ? '/system/groups'
    : permissions.some(permission => permission.startsWith('datasets.'))
      ? '/system/datasets'
      : permissions.some(permission => permission.startsWith('staff.'))
        ? '/system/staff'
        : '/system/permission-groups'
await navigateTo(destination, { replace: true })
</script>
