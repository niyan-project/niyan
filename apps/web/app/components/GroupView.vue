<script setup lang="ts">
import type { Dataset, DatasetList, Membership, MembershipList, Namespace, NamespaceList, Role, UserSearchItem } from '~/types/api'

const props = defineProps<{ group: Namespace }>()
const api = useApi()
const toast = useToast()
const route = useRoute()
const saving = ref(false)
const tab = computed(() => typeof route.query.tab === 'string' ? route.query.tab : 'datasets')
const showChildForm = ref(false)
const showDatasetForm = ref(false)
const childForm = reactive({ parent_id: props.group.id, name: '', slug: '' })
const datasetForm = reactive({ namespace_id: props.group.id, name: '', slug: '', description: '' })
const editForm = reactive({ name: props.group.name, slug: props.group.slug })
const deleteConfirmation = ref('')
const userQuery = ref('')
const userResults = ref<UserSearchItem[]>([])
const membershipForm = reactive<{ username: string, role: Role }>({ username: '', role: 'reader' })
const { data: groupData, refresh: refreshGroup } = await useAsyncData(`group:${props.group.id}`, async () => {
  const [namespacePage, datasetPage] = await Promise.all([
    api.get<NamespaceList>('/api/v1/namespaces?limit=100'),
    api.get<DatasetList>(`/api/v1/datasets?namespace_id=${props.group.id}&limit=100`)
  ])
  const membershipPage = props.group.can_manage ? await api.get<MembershipList>(`/api/v1/namespaces/${props.group.id}/memberships`) : null

  return {
    namespaces: namespacePage.items,
    datasets: datasetPage.items,
    memberships: membershipPage?.items || []
  }
}, {
  lazy: false,
  getCachedData: () => undefined
})
const namespaces = computed(() => groupData.value?.namespaces || [])
const datasets = computed(() => groupData.value?.datasets || [])
const memberships = computed(() => groupData.value?.memberships || [])
const childGroups = computed(() => namespaces.value.filter(namespace => namespace.parent_id === props.group.id))
const groupBreadcrumbs = computed(() => props.group.path.split('/').map((segment, index, parts) => ({ label: segment, path: parts.slice(0, index + 1).join('/') })))

async function createChild() {
  saving.value = true
  try {
    const child = await api.post<Namespace>('/api/v1/namespaces', childForm)
    toast.add({ title: 'Group created', description: child.path, color: 'success' })
    await navigateTo(`/${child.path}`)
  } catch (error) {
    toast.add({ title: 'Could not create group', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}

async function createDataset() {
  saving.value = true
  try {
    const dataset = await api.post<Dataset>('/api/v1/datasets', datasetForm)
    toast.add({ title: 'Dataset created', description: `${dataset.namespace_path}/${dataset.slug}`, color: 'success' })
    await navigateTo(`/${dataset.namespace_path}/${dataset.slug}`)
  } catch (error) {
    toast.add({ title: 'Could not create dataset', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}

async function searchUsers() {
  if (userQuery.value.trim().length < 2) return
  const response = await api.get<{ items: UserSearchItem[] }>(`/api/v1/auth/users?query=${encodeURIComponent(userQuery.value.trim())}`)
  userResults.value = response.items
}

async function addMember() {
  if (!membershipForm.username) return
  saving.value = true
  try {
    await api.post(`/api/v1/namespaces/${props.group.id}/memberships`, membershipForm)
    Object.assign(membershipForm, { username: '', role: 'reader' })
    userResults.value = []
    userQuery.value = ''
    await refreshGroup()
  } catch (error) {
    toast.add({ title: 'Could not add member', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}

async function changeMember(member: Membership, role: Role) {
  try {
    await api.patch(`/api/v1/namespaces/${props.group.id}/memberships/${member.id}`, { role })
    await refreshGroup()
  } catch (error) {
    toast.add({ title: 'Could not change membership', description: error instanceof Error ? error.message : undefined, color: 'error' })
  }
}

async function removeMember(member: Membership) {
  try {
    await api.delete(`/api/v1/namespaces/${props.group.id}/memberships/${member.id}`)
    await refreshGroup()
  } catch (error) {
    toast.add({ title: 'Could not remove member', description: error instanceof Error ? error.message : undefined, color: 'error' })
  }
}

async function updateGroup() {
  saving.value = true
  try {
    const updated = await api.patch<Namespace>(`/api/v1/namespaces/${props.group.id}`, editForm)
    toast.add({ title: 'Group updated', color: 'success' })
    await navigateTo(`/${updated.path}?tab=settings`)
  } catch (error) {
    toast.add({ title: 'Could not update group', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}

async function deleteGroup() {
  if (deleteConfirmation.value !== props.group.path) return
  saving.value = true
  try {
    await api.delete(`/api/v1/namespaces/${props.group.id}`)
    toast.add({ title: 'Group deleted', color: 'success' })
    await navigateTo('/')
  } catch (error) {
    toast.add({ title: 'Could not delete group', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}
</script>

<template>
  <UContainer class="py-8">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <nav class="flex flex-wrap items-center gap-1 font-mono text-sm text-muted" aria-label="Group breadcrumb">
          <template v-for="(crumb, index) in groupBreadcrumbs" :key="crumb.path"><span v-if="index" aria-hidden="true">/</span><NuxtLink :to="`/${crumb.path}`" class="rounded px-1 py-0.5 hover:text-primary">{{ crumb.label }}</NuxtLink></template>
        </nav>
        <h1 class="mt-1 text-2xl font-medium text-highlighted">{{ group.name }}</h1>
        <p class="mt-2 text-muted">{{ group.kind === 'personal' ? 'Your personal datasets.' : 'A Niyān group for people and datasets.' }}</p>
      </div>
      <div v-if="group.can_create_group || group.can_create_dataset" class="flex flex-wrap gap-2">
        <UButton v-if="group.can_create_group" label="New subgroup" icon="i-lucide-users-round" color="neutral" variant="outline" @click="showChildForm = !showChildForm" />
        <UButton v-if="group.can_create_dataset" label="New dataset" icon="i-lucide-plus" @click="showDatasetForm = !showDatasetForm" />
      </div>
    </div>

    <nav class="mt-6 flex gap-1 border-b border-default" aria-label="Group sections">
      <UButton to="?tab=datasets" label="Datasets" icon="i-lucide-database" color="neutral" :variant="tab === 'datasets' ? 'soft' : 'ghost'" />
      <UButton v-if="group.kind === 'group' && group.can_manage" to="?tab=members" label="Members" icon="i-lucide-user-round-cog" color="neutral" :variant="tab === 'members' ? 'soft' : 'ghost'" />
      <UButton v-if="group.kind === 'group' && (group.can_manage || group.can_delete)" to="?tab=settings" label="Settings" icon="i-lucide-settings" color="neutral" :variant="tab === 'settings' ? 'soft' : 'ghost'" />
    </nav>

    <UCard v-if="showChildForm" class="mt-6">
      <template #header><h2 class="font-medium text-highlighted">Create subgroup in {{ group.path }}</h2></template>
      <form class="grid gap-4 sm:grid-cols-2" @submit.prevent="createChild">
        <UFormField label="Name" required><UInput v-model="childForm.name" class="w-full" /></UFormField>
        <UFormField label="Slug" required><UInput v-model="childForm.slug" class="w-full" /></UFormField>
        <div class="flex justify-end sm:col-span-2"><UButton type="submit" label="Create subgroup" :loading="saving" /></div>
      </form>
    </UCard>

    <UCard v-if="showDatasetForm" class="mt-6">
      <template #header><h2 class="font-medium text-highlighted">Create empty dataset in {{ group.path }}</h2></template>
      <form class="grid gap-4 sm:grid-cols-2" @submit.prevent="createDataset">
        <UFormField label="Name" required><UInput v-model="datasetForm.name" class="w-full" /></UFormField>
        <UFormField label="Slug" required><UInput v-model="datasetForm.slug" class="w-full" /></UFormField>
        <UFormField label="Description" hint="Optional" class="sm:col-span-2"><UTextarea v-model="datasetForm.description" :maxlength="500" autoresize class="w-full" /></UFormField>
        <UAlert class="sm:col-span-2" color="neutral" variant="soft" icon="i-lucide-upload" description="Upload and commit files from the dataset's Files page, or populate it with the Niyān CLI." />
        <div class="flex justify-end sm:col-span-2"><UButton type="submit" label="Create dataset" :loading="saving" /></div>
      </form>
    </UCard>

    <section v-if="tab === 'datasets'" class="mt-6 space-y-8">
      <div v-if="childGroups.length">
        <h2 class="mb-3 font-medium text-highlighted">Subgroups</h2>
        <div class="grid gap-3 md:grid-cols-2"><NuxtLink v-for="child in childGroups" :key="child.id" :to="`/${child.path}`" class="rounded-lg border border-default p-4 hover:bg-elevated"><p class="font-medium text-highlighted">{{ child.name }}</p><p class="mt-1 font-mono text-sm text-muted">{{ child.path }}</p></NuxtLink></div>
      </div>
      <div>
        <h2 class="mb-3 font-medium text-highlighted">Datasets</h2>
        <div class="overflow-hidden rounded-lg border border-default">
          <NuxtLink v-for="dataset in datasets" :key="dataset.id" :to="`/${dataset.namespace_path}/${dataset.slug}`" class="flex items-center justify-between gap-4 border-b border-default p-4 last:border-b-0 hover:bg-elevated"><div class="min-w-0 flex-1"><p class="font-medium text-highlighted">{{ dataset.name }}</p><p class="mt-1 truncate font-mono text-sm text-muted">{{ dataset.slug }}</p><DatasetDescriptionSummary :description="dataset.description" /></div><UBadge class="shrink-0" color="neutral" variant="subtle">{{ dataset.role || 'system' }}</UBadge></NuxtLink>
          <p v-if="!datasets.length" class="p-8 text-center text-muted">This {{ group.kind === 'group' ? 'group' : 'account' }} has no visible datasets.</p>
        </div>
      </div>
    </section>

    <section v-else-if="tab === 'members' && group.can_manage" class="mt-6 space-y-6">
      <UCard>
        <template #header><h2 class="font-medium text-highlighted">Add member</h2></template>
        <div class="grid gap-3 sm:grid-cols-[1fr_auto]">
          <UInput v-model="userQuery" placeholder="Search username or name" @keyup.enter="searchUsers" />
          <UButton label="Search" color="neutral" variant="outline" @click="searchUsers" />
        </div>
        <div v-if="userResults.length" class="mt-4 grid gap-3 sm:grid-cols-[1fr_12rem_auto]">
          <USelect v-model="membershipForm.username" :items="userResults.map(user => ({ label: `${user.display_name} (${user.username})`, value: user.username }))" placeholder="Select user" />
          <USelect v-model="membershipForm.role" :items="['reader', 'contributor', 'maintainer', 'owner']" />
          <UButton label="Add" :loading="saving" :disabled="!membershipForm.username" @click="addMember" />
        </div>
      </UCard>
      <div class="overflow-hidden rounded-lg border border-default">
        <div v-for="member in memberships" :key="member.id" class="grid gap-3 border-b border-default p-4 last:border-b-0 sm:grid-cols-[1fr_12rem_auto] sm:items-center">
          <div><p class="font-medium text-highlighted">{{ member.display_name }}</p><p class="text-sm text-muted">{{ member.username }}</p></div>
          <USelect :model-value="member.role" :items="['reader', 'contributor', 'maintainer', 'owner']" @update:model-value="value => changeMember(member, value as Role)" />
          <UButton icon="i-lucide-user-round-x" aria-label="Remove member" color="error" variant="ghost" @click="removeMember(member)" />
        </div>
      </div>
    </section>

    <section v-else-if="tab === 'settings' && (group.can_manage || group.can_delete)" class="mt-6 space-y-6">
      <UCard v-if="group.can_manage">
        <template #header><h2 class="font-medium text-highlighted">Group details</h2></template>
        <form class="grid gap-4 sm:grid-cols-2" @submit.prevent="updateGroup">
          <UFormField label="Name"><UInput v-model="editForm.name" class="w-full" /></UFormField>
          <UFormField label="Slug"><UInput v-model="editForm.slug" class="w-full" /></UFormField>
          <div class="flex justify-end sm:col-span-2"><UButton type="submit" label="Save changes" :loading="saving" /></div>
        </form>
      </UCard>
      <UCard v-if="group.can_delete" class="ring-error/30">
        <template #header><h2 class="font-medium text-error">Delete group</h2></template>
        <p class="mb-4 text-sm text-muted">The group must be empty. Type <strong class="font-mono text-highlighted">{{ group.path }}</strong> to confirm irreversible deletion.</p>
        <div class="flex flex-col gap-3 sm:flex-row"><UInput v-model="deleteConfirmation" class="flex-1" /><UButton label="Delete group" color="error" :disabled="deleteConfirmation !== group.path" :loading="saving" @click="deleteGroup" /></div>
      </UCard>
    </section>
  </UContainer>
</template>
