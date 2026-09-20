<script setup lang="ts">
definePageMeta({ middleware: 'system' })
const { user } = useAuth()
const permissions = user.value?.system_permissions || []
const destination = permissions.some(permission => permission.startsWith('users.'))
  ? '/system/users'
  : permissions.some(permission => permission.startsWith('groups.'))
    ? '/system/groups'
    : '/system/datasets'
await navigateTo(destination, { replace: true })
</script>
