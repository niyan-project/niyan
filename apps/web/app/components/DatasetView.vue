<script setup lang="ts">
import type { BlobMetadata, CommitItem, CommitList, Dataset, DatasetGrant, DownloadAction, GrantList, NamespaceList, Readme, RefList, Role, TreeEntry, TreeList, UserSearchItem } from '~/types/api'
import { NiyanApiError } from '~/composables/useApi'

const props = defineProps<{ dataset: Dataset }>()
const api = useApi()
const toast = useToast()
const route = useRoute()
const router = useRouter()
const runtimeConfig = useRuntimeConfig()
const { formatBytes, formatRelative } = useFormatting()
const tab = computed(() => typeof route.query.tab === 'string' ? route.query.tab : 'files')
const revision = computed(() => typeof route.query.revision === 'string' ? route.query.revision : props.dataset.default_branch)
const treePath = computed(() => typeof route.query.path === 'string' ? route.query.path : '')
const selectedBlob = ref<BlobMetadata | null>(null)
const saving = ref(false)
const editForm = reactive({ name: props.dataset.name, slug: props.dataset.slug, description: props.dataset.description })
const emptyGuide = ref<'new' | 'existing'>('new')
const deleteConfirmation = ref('')
const userQuery = ref('')
const userResults = ref<UserSearchItem[]>([])
const grantForm = reactive<{ principal_type: 'user' | 'group', role: Role }>({ principal_type: 'user', role: 'reader' })
const userGrantId = ref<number>()
const groupGrantId = ref<string>()
const selectedGrantPrincipal = computed(() => grantForm.principal_type === 'user' ? userGrantId.value : groupGrantId.value)
const canManage = computed(() => props.dataset.role === 'owner')
const canEdit = computed(() => ['owner', 'maintainer'].includes(props.dataset.role))
const datasetPath = computed(() => `${props.dataset.namespace_path}/${props.dataset.slug}`)
const installationUrl = computed(() => String(runtimeConfig.public.installationUrl || (import.meta.client ? window.location.origin : '')).replace(/\/$/, ''))
const authLoginCommand = computed(() => `niyan auth login ${installationUrl.value}`)
const cloneCommand = computed(() => `niyan dataset clone ${datasetPath.value}`)
const cloneUrl = computed(() => `${installationUrl.value}/git/${props.dataset.id}.git`)
const newDatasetCommands = computed(() => `${authLoginCommand.value}\n${cloneCommand.value}\ncd ${props.dataset.slug}\n# Add files to this directory, then:\nniyan add --all\nniyan commit -m "Initial dataset"\nniyan push`)
const existingDatasetCommands = computed(() => `${authLoginCommand.value}\nniyan dataset clone ${datasetPath.value} ${props.dataset.slug}-upload\ncp -R /path/to/your/files/. ${props.dataset.slug}-upload/\ncd ${props.dataset.slug}-upload\nniyan add --all\nniyan commit -m "Initial dataset"\nniyan push`)
const { data: initialData, error: loadError } = await useAsyncData(`dataset-view:${props.dataset.id}:${route.fullPath}`, async () => {
  const [branchPage, tagPage] = await Promise.all([
    api.get<RefList>(`/api/v1/datasets/${props.dataset.id}/repository/refs?kind=branches`),
    api.get<RefList>(`/api/v1/datasets/${props.dataset.id}/repository/refs?kind=tags`)
  ])
  const branches = branchPage.items.map(item => item.name)
  const tags = tagPage.items.map(item => item.name)
  let emptyRepository = !branches.length && !tags.length
  let tree: TreeList | null = null
  let readme: Readme | null = null
  let commits: CommitItem[] = []
  let grants: DatasetGrant[] = []
  let groupCandidates: { label: string, value: string }[] = []

  if (tab.value === 'files') {
    const query = new URLSearchParams({ revision: revision.value, path: treePath.value, limit: '100' })
    try {
      tree = await api.get<TreeList>(`/api/v1/datasets/${props.dataset.id}/repository/tree?${query}`)
      emptyRepository = false
      if (!treePath.value) {
        try { readme = await api.get<Readme>(`/api/v1/datasets/${props.dataset.id}/repository/readme?revision=${encodeURIComponent(tree.resolved_commit)}`) } catch { readme = null }
      }
    } catch (error) {
      if (!(error instanceof NiyanApiError) || error.code !== 'revision_not_found') throw error
      emptyRepository = true
    }
  }

  if (tab.value === 'history') {
    try {
      const response = await api.get<CommitList>(`/api/v1/datasets/${props.dataset.id}/repository/commits?revision=${encodeURIComponent(revision.value)}&limit=100`)
      commits = response.items
    } catch (error) {
      if (!(error instanceof NiyanApiError) || error.code !== 'revision_not_found') throw error
    }
  }

  if (tab.value === 'access' && canManage.value) {
    const [grantPage, namespacePage] = await Promise.all([
      api.get<GrantList>(`/api/v1/datasets/${props.dataset.id}/grants`),
      api.get<NamespaceList>('/api/v1/namespaces?limit=100')
    ])
    grants = grantPage.items
    groupCandidates = namespacePage.items.filter(item => item.kind === 'group' && item.id !== props.dataset.namespace_id).map(item => ({ label: item.path, value: item.id }))
  }

  return { branches, tags, emptyRepository, tree, readme, commits, grants, groupCandidates }
}, {
  lazy: false,
  getCachedData: () => undefined
})
const branches = ref(initialData.value?.branches || [])
const tags = ref(initialData.value?.tags || [])
const emptyRepository = ref(initialData.value?.emptyRepository || false)
const tree = ref<TreeList | null>(initialData.value?.tree || null)
const readme = ref<Readme | null>(initialData.value?.readme || null)
const commits = ref(initialData.value?.commits || [])
const grants = ref(initialData.value?.grants || [])
const groupCandidates = ref(initialData.value?.groupCandidates || [])
const resolvedCommit = ref<string | null>(tree.value?.resolved_commit || null)
const breadcrumbs = computed(() => treePath.value ? treePath.value.split('/').map((segment, index, parts) => ({ label: segment, path: parts.slice(0, index + 1).join('/') })) : [])

if (loadError.value) {
  toast.add({ title: 'Could not load repository', description: loadError.value.message, color: 'error' })
}

async function loadGrants() {
  if (!canManage.value) return
  const [grantPage, namespacePage] = await Promise.all([
    api.get<GrantList>(`/api/v1/datasets/${props.dataset.id}/grants`),
    api.get<NamespaceList>('/api/v1/namespaces?limit=100')
  ])
  grants.value = grantPage.items
  groupCandidates.value = namespacePage.items.filter(item => item.kind === 'group' && item.id !== props.dataset.namespace_id).map(item => ({ label: item.path, value: item.id }))
}

async function changeRevision(value: string) {
  await router.replace({ query: { ...route.query, revision: value, path: undefined } })
}

async function openEntry(entry: TreeEntry) {
  if (entry.object_type === 'tree') {
    await router.replace({ query: { ...route.query, path: entry.path, revision: revision.value } })
    return
  }
  const query = new URLSearchParams({ revision: resolvedCommit.value || revision.value, path: entry.path })
  selectedBlob.value = await api.get<BlobMetadata>(`/api/v1/datasets/${props.dataset.id}/repository/blob?${query}`)
}

async function navigatePath(path: string) {
  await router.replace({ query: { ...route.query, path: path || undefined, revision: revision.value } })
}

async function download(path: string) {
  const query = new URLSearchParams({ revision: selectedBlob.value?.resolved_commit || resolvedCommit.value || revision.value, path })
  const action = await api.get<DownloadAction>(`/api/v1/datasets/${props.dataset.id}/repository/download?${query}`)
  window.location.assign(action.url)
}

async function copyText(value: string, title: string) {
  await navigator.clipboard.writeText(value)
  toast.add({ title, color: 'success' })
}

async function searchUsers() {
  if (userQuery.value.trim().length < 2) return
  const response = await api.get<{ items: UserSearchItem[] }>(`/api/v1/auth/users?query=${encodeURIComponent(userQuery.value.trim())}`)
  userResults.value = response.items
}

async function addGrant() {
  if (selectedGrantPrincipal.value === undefined) return
  saving.value = true
  const payload = grantForm.principal_type === 'user' ? { user_id: userGrantId.value, role: grantForm.role } : { group_namespace_id: groupGrantId.value, role: grantForm.role }
  try {
    await api.post(`/api/v1/datasets/${props.dataset.id}/grants`, payload)
    userGrantId.value = undefined
    groupGrantId.value = undefined
    await loadGrants()
  } catch (error) {
    toast.add({ title: 'Could not add grant', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally { saving.value = false }
}

async function changeGrant(grant: DatasetGrant, role: Role) {
  try { await api.patch(`/api/v1/datasets/${props.dataset.id}/grants/${grant.id}`, { role }); await loadGrants() } catch (error) { toast.add({ title: 'Could not change grant', description: error instanceof Error ? error.message : undefined, color: 'error' }) }
}

async function removeGrant(grant: DatasetGrant) {
  try { await api.delete(`/api/v1/datasets/${props.dataset.id}/grants/${grant.id}`); await loadGrants() } catch (error) { toast.add({ title: 'Could not remove grant', description: error instanceof Error ? error.message : undefined, color: 'error' }) }
}

async function updateDataset() {
  saving.value = true
  try {
    const updated = await api.patch<Dataset>(`/api/v1/datasets/${props.dataset.id}`, editForm)
    toast.add({ title: 'Dataset updated', color: 'success' })
    await navigateTo(`/${updated.namespace_path}/${updated.slug}?tab=settings`)
  } catch (error) { toast.add({ title: 'Could not update dataset', description: error instanceof Error ? error.message : undefined, color: 'error' }) } finally { saving.value = false }
}

async function deleteDataset() {
  const path = `${props.dataset.namespace_path}/${props.dataset.slug}`
  if (deleteConfirmation.value !== path) return
  saving.value = true
  try { await api.delete(`/api/v1/datasets/${props.dataset.id}`); toast.add({ title: 'Dataset deleted', color: 'success' }); await navigateTo('/') } catch (error) { toast.add({ title: 'Could not delete dataset', description: error instanceof Error ? error.message : undefined, color: 'error' }) } finally { saving.value = false }
}
</script>

<template>
  <UContainer class="py-8">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div><p class="font-mono text-sm text-muted"><NuxtLink :to="`/${dataset.namespace_path}`" class="hover:text-primary">{{ dataset.namespace_path }}</NuxtLink> / {{ dataset.slug }}</p><h1 class="mt-1 text-2xl font-medium text-highlighted">{{ dataset.name }}</h1><p v-if="dataset.description" class="mt-2 max-w-3xl text-muted">{{ dataset.description }}</p></div>
      <UPopover :content="{ align: 'end' }">
        <UButton label="Clone" icon="i-lucide-terminal" trailing-icon="i-lucide-chevron-down" />
        <template #content>
          <div class="w-[min(28rem,calc(100vw-2rem))] p-4">
            <h2 class="font-medium text-highlighted">Clone with the Niyān CLI</h2>
            <p class="mt-1 text-sm text-muted">Niyān configures Git, authentication, shallow history, and Git LFS for this dataset.</p>
            <div class="mt-4 flex items-center gap-2 rounded-md bg-elevated p-2"><code class="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-sm">{{ cloneCommand }}</code><UButton icon="i-lucide-copy" aria-label="Copy clone command" color="neutral" variant="ghost" size="sm" @click="copyText(cloneCommand, 'Clone command copied')" /></div>
            <p class="mt-4 text-xs font-medium uppercase tracking-wide text-muted">Git URL</p>
            <div class="mt-2 flex items-center gap-2"><UInput :model-value="cloneUrl" readonly class="min-w-0 flex-1 font-mono" /><UButton icon="i-lucide-copy" aria-label="Copy Git URL" color="neutral" variant="outline" @click="copyText(cloneUrl, 'Git URL copied')" /></div>
          </div>
        </template>
      </UPopover>
    </div>

    <nav class="mt-6 flex gap-1 overflow-x-auto border-b border-default" aria-label="Dataset sections">
      <UButton to="?tab=files" label="Files" icon="i-lucide-folder-tree" color="neutral" :variant="tab === 'files' ? 'soft' : 'ghost'" />
      <UButton to="?tab=history" label="History" icon="i-lucide-history" color="neutral" :variant="tab === 'history' ? 'soft' : 'ghost'" />
      <UButton to="?tab=branches" label="Branches" icon="i-lucide-git-branch" color="neutral" :variant="tab === 'branches' ? 'soft' : 'ghost'" />
      <UButton to="?tab=tags" label="Tags" icon="i-lucide-tag" color="neutral" :variant="tab === 'tags' ? 'soft' : 'ghost'" />
      <UButton v-if="canManage" to="?tab=access" label="Access" icon="i-lucide-shield" color="neutral" :variant="tab === 'access' ? 'soft' : 'ghost'" />
      <UButton v-if="canEdit" to="?tab=settings" label="Settings" icon="i-lucide-settings" color="neutral" :variant="tab === 'settings' ? 'soft' : 'ghost'" />
    </nav>

    <section v-if="tab === 'files'" class="mt-6">
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <USelect :model-value="revision" :items="[...branches, ...tags]" icon="i-lucide-git-branch" class="min-w-40" :disabled="emptyRepository" @update:model-value="value => changeRevision(String(value))" />
        <UButton label="Root" color="neutral" variant="ghost" @click="navigatePath('')" />
        <template v-for="crumb in breadcrumbs" :key="crumb.path"><span class="text-muted">/</span><UButton :label="crumb.label" color="neutral" variant="ghost" @click="navigatePath(crumb.path)" /></template>
        <span v-if="tree" class="ml-auto font-mono text-xs text-muted">{{ tree.resolved_commit.slice(0, 10) }}</span>
      </div>

      <UCard v-if="emptyRepository">
        <template #header><div class="flex items-start gap-3"><UIcon name="i-lucide-git-commit-horizontal" class="mt-0.5 size-5 text-primary" /><div><h2 class="font-medium text-highlighted">This dataset is ready for its first files</h2><p class="mt-1 text-sm text-muted">Use the Niyān CLI so authentication, Git LFS, and the remote are configured safely.</p></div></div></template>
        <div class="mb-4 flex gap-1 border-b border-default">
          <UButton label="Start fresh" color="neutral" :variant="emptyGuide === 'new' ? 'soft' : 'ghost'" @click="emptyGuide = 'new'" />
          <UButton label="Upload existing files" color="neutral" :variant="emptyGuide === 'existing' ? 'soft' : 'ghost'" @click="emptyGuide = 'existing'" />
        </div>
        <p class="mb-3 text-sm text-muted">{{ emptyGuide === 'new' ? 'Clone the empty dataset, add files, and publish its first commit.' : 'Clone into a clean directory, copy your existing files into it, and publish them.' }}</p>
        <div class="relative rounded-md bg-elevated p-4 pr-12"><pre class="overflow-x-auto text-sm"><code>{{ emptyGuide === 'new' ? newDatasetCommands : existingDatasetCommands }}</code></pre><UButton icon="i-lucide-copy" aria-label="Copy setup commands" color="neutral" variant="ghost" size="sm" class="absolute right-2 top-2" @click="copyText(emptyGuide === 'new' ? newDatasetCommands : existingDatasetCommands, 'Setup commands copied')" /></div>
      </UCard>
      <div v-else class="overflow-hidden rounded-lg border border-default bg-default">
        <button v-for="entry in tree?.items" :key="entry.path" type="button" class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-default p-3 text-left last:border-b-0 hover:bg-elevated sm:grid-cols-[minmax(0,1fr)_10rem_8rem]" @click="openEntry(entry)">
          <span class="flex min-w-0 items-center gap-2"><UIcon :name="entry.object_type === 'tree' ? 'i-lucide-folder' : 'i-lucide-file'" class="size-4 shrink-0 text-primary" /><span class="truncate text-highlighted">{{ entry.name }}</span></span>
          <span class="hidden font-mono text-xs text-muted sm:block">{{ entry.object_id.slice(0, 10) }}</span>
          <span class="text-right text-sm text-muted">{{ entry.object_type === 'tree' ? 'Directory' : formatBytes(entry.size) }}</span>
        </button>
      </div>

      <UCard v-if="selectedBlob" class="mt-4">
        <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"><div><p class="font-medium text-highlighted">{{ selectedBlob.path }}</p><p class="mt-1 text-sm text-muted">{{ selectedBlob.is_lfs ? `Git LFS · ${formatBytes(selectedBlob.lfs_size)}` : `Git · ${formatBytes(selectedBlob.size)}` }} · {{ selectedBlob.object_id.slice(0, 12) }}</p></div><UButton label="Download" icon="i-lucide-download" @click="download(selectedBlob.path)" /></div>
      </UCard>

      <UCard v-if="readme" class="mt-6">
        <template #header><div class="flex items-center gap-2"><UIcon name="i-lucide-book-open" class="size-4" /><span class="font-medium">{{ readme.path }}</span></div></template>
        <SafeMarkdown :content="readme.content" />
      </UCard>
    </section>

    <section v-else-if="tab === 'history'" class="mt-6 space-y-3">
      <div class="mb-4"><USelect :model-value="revision" :items="[...branches, ...tags]" icon="i-lucide-git-branch" class="min-w-40" :disabled="emptyRepository" @update:model-value="value => changeRevision(String(value))" /></div>
      <div v-for="commit in commits" :key="commit.object_id" class="rounded-lg border border-default p-4"><div class="flex flex-col gap-2 sm:flex-row sm:justify-between"><div><p class="font-medium text-highlighted">{{ commit.subject }}</p><p class="mt-1 text-sm text-muted">{{ commit.author_name }} · {{ formatRelative(commit.authored_at) }}</p></div><code class="text-xs text-muted">{{ commit.object_id.slice(0, 10) }}</code></div></div>
      <UAlert v-if="!commits.length" color="neutral" variant="soft" description="No commits are available on this revision." />
    </section>

    <section v-else-if="tab === 'branches'" class="mt-6">
      <div class="mb-4"><h2 class="text-lg font-medium text-highlighted">Branches</h2><p class="mt-1 text-sm text-muted">Moving lines of dataset history maintained by Git.</p></div>
      <div class="overflow-hidden rounded-lg border border-default bg-default"><div v-for="branch in branches" :key="branch" class="flex items-center gap-3 border-b border-default p-4 last:border-b-0"><UIcon name="i-lucide-git-branch" class="size-4 text-primary" /><span class="font-mono text-sm text-highlighted">{{ branch }}</span><UBadge v-if="branch === dataset.default_branch" class="ml-auto" color="neutral" variant="subtle">Default</UBadge></div><p v-if="!branches.length" class="p-8 text-center text-muted">No branches</p></div>
    </section>

    <section v-else-if="tab === 'tags'" class="mt-6">
      <div class="mb-4"><h2 class="text-lg font-medium text-highlighted">Tags</h2><p class="mt-1 text-sm text-muted">Named immutable points in dataset history.</p></div>
      <div class="overflow-hidden rounded-lg border border-default bg-default"><div v-for="tag in tags" :key="tag" class="flex items-center gap-3 border-b border-default p-4 last:border-b-0"><UIcon name="i-lucide-tag" class="size-4 text-primary" /><span class="font-mono text-sm text-highlighted">{{ tag }}</span></div><p v-if="!tags.length" class="p-8 text-center text-muted">No tags</p></div>
    </section>

    <section v-else-if="tab === 'access' && canManage" class="mt-6 space-y-6">
      <UCard>
        <template #header><h2 class="font-medium">Add dataset grant</h2></template>
        <div class="grid gap-3 sm:grid-cols-[10rem_minmax(0,1fr)_10rem_auto]">
          <USelect v-model="grantForm.principal_type" :items="[{ label: 'User', value: 'user' }, { label: 'Group', value: 'group' }]" @update:model-value="userGrantId = undefined; groupGrantId = undefined" />
          <div v-if="grantForm.principal_type === 'user'" class="flex gap-2"><UInput v-model="userQuery" placeholder="Search users" class="flex-1" @keyup.enter="searchUsers" /><UButton icon="i-lucide-search" aria-label="Search users" color="neutral" variant="outline" @click="searchUsers" /></div>
          <USelect v-else v-model="groupGrantId" :items="groupCandidates" placeholder="Select group" />
          <USelect v-model="grantForm.role" :items="['reader', 'contributor', 'maintainer', 'owner']" />
          <UButton label="Add" :disabled="selectedGrantPrincipal === undefined" :loading="saving" @click="addGrant" />
        </div>
        <USelect v-if="grantForm.principal_type === 'user' && userResults.length" v-model="userGrantId" class="mt-3 w-full" :items="userResults.map(user => ({ label: `${user.display_name} (${user.username})`, value: user.id }))" placeholder="Select user" />
      </UCard>
      <div class="overflow-hidden rounded-lg border border-default"><div v-for="grant in grants" :key="grant.id" class="grid gap-3 border-b border-default p-4 last:border-b-0 sm:grid-cols-[1fr_12rem_auto] sm:items-center"><div><p class="font-medium text-highlighted">{{ grant.principal_label }}</p><p class="text-sm text-muted">{{ grant.principal_type }}</p></div><USelect :model-value="grant.role" :items="['reader', 'contributor', 'maintainer', 'owner']" @update:model-value="value => changeGrant(grant, value as Role)" /><UButton icon="i-lucide-trash-2" aria-label="Remove grant" color="error" variant="ghost" @click="removeGrant(grant)" /></div><p v-if="!grants.length" class="p-8 text-center text-muted">No explicit grants. Namespace membership may still provide access.</p></div>
    </section>

    <section v-else-if="tab === 'settings' && canEdit" class="mt-6 space-y-6">
      <UCard><template #header><h2 class="font-medium">Dataset details</h2></template><form class="grid gap-4 sm:grid-cols-2" @submit.prevent="updateDataset"><UFormField label="Name"><UInput v-model="editForm.name" class="w-full" /></UFormField><UFormField label="Slug"><UInput v-model="editForm.slug" class="w-full" /></UFormField><UFormField label="Description" hint="Optional" class="sm:col-span-2"><UTextarea v-model="editForm.description" :maxlength="500" autoresize class="w-full" /></UFormField><div class="flex justify-end sm:col-span-2"><UButton type="submit" label="Save changes" :loading="saving" /></div></form></UCard>
      <UCard v-if="canManage" class="ring-error/30"><template #header><h2 class="font-medium text-error">Delete dataset</h2></template><p class="mb-4 text-sm text-muted">Type <strong class="font-mono text-highlighted">{{ dataset.namespace_path }}/{{ dataset.slug }}</strong> to permanently delete the repository and its files.</p><div class="flex flex-col gap-3 sm:flex-row"><UInput v-model="deleteConfirmation" class="flex-1" /><UButton label="Delete dataset" color="error" :disabled="deleteConfirmation !== `${dataset.namespace_path}/${dataset.slug}`" :loading="saving" @click="deleteDataset" /></div></UCard>
    </section>
  </UContainer>
</template>
