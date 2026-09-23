<script setup lang="ts">
import { sha256 } from '@noble/hashes/sha2.js'
import { bytesToHex } from '@noble/hashes/utils.js'
import type { BlobMetadata, BrowserDraft, BrowserDraftList, CommitItem, CommitList, Dataset, DatasetGrant, DirectUploadAction, DownloadAction, GrantList, LfsStage, NamespaceList, Readme, RefList, Role, TreeEntry, TreeList, UserSearchItem } from '~/types/api'
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
const userGrantUsername = ref<string>()
const groupGrantPath = ref<string>()
const selectedGrantPrincipal = computed(() => grantForm.principal_type === 'user' ? userGrantUsername.value : groupGrantPath.value)
const canManage = computed(() => props.dataset.can_manage_access)
const canEdit = computed(() => props.dataset.can_update)
const canWrite = computed(() => props.dataset.can_write)
const canDelete = computed(() => props.dataset.can_delete)
const datasetPath = computed(() => `${props.dataset.namespace_path}/${props.dataset.slug}`)
const installationUrl = computed(() => String(runtimeConfig.public.installationUrl || (import.meta.client ? window.location.origin : '')).replace(/\/$/, ''))
const authLoginCommand = computed(() => `niyan auth login ${installationUrl.value}`)
const cloneCommand = computed(() => `niyan dataset clone ${datasetPath.value}`)
const cloneUrl = computed(() => `${installationUrl.value}/git/${props.dataset.id}.git`)
const newDatasetCommands = computed(() => `${authLoginCommand.value}\n${cloneCommand.value}\ncd ${props.dataset.slug}\n# Add files to this directory, then:\nniyan add --all --verbose\nniyan commit -m "Initial dataset"\nniyan push`)
const existingDatasetCommands = computed(() => `${authLoginCommand.value}\nniyan dataset clone ${datasetPath.value} ${props.dataset.slug}-upload\ncp -R /path/to/your/files/. ${props.dataset.slug}-upload/\ncd ${props.dataset.slug}-upload\nniyan add --all --verbose\nniyan commit -m "Initial dataset"\nniyan push`)
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
  let browserDraft: BrowserDraft | null = null

  if (canWrite.value) {
    const targetBranch = branches.includes(revision.value) ? revision.value : props.dataset.default_branch
    try {
      const draftPage = await api.get<BrowserDraftList>(`/api/v1/datasets/${props.dataset.id}/drafts?target_branch=${encodeURIComponent(targetBranch)}`)
      browserDraft = draftPage.items[0] || null
    } catch { browserDraft = null }
  }

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
    groupCandidates = namespacePage.items.filter(item => item.kind === 'group' && item.id !== props.dataset.namespace_id).map(item => ({ label: item.path, value: item.path }))
  }

  return { branches, tags, emptyRepository, tree, readme, commits, grants, groupCandidates, browserDraft }
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
const datasetBreadcrumbs = computed(() => {
  const namespaces = props.dataset.namespace_path.split('/').map((segment, index, parts) => ({ label: segment, to: `/${parts.slice(0, index + 1).join('/')}` }))
  return [...namespaces, { label: props.dataset.slug, to: `/${datasetPath.value}` }]
})
const breadcrumbs = computed(() => treePath.value ? treePath.value.split('/').map((segment, index, parts) => ({ label: segment, path: parts.slice(0, index + 1).join('/') })) : [])
const uploadOpen = ref(false)
const selectedFiles = ref<File[]>([])
const storageChoice = ref<'auto' | 'git' | 'lfs'>('auto')
const activeDraft = ref<BrowserDraft | null>(initialData.value?.browserDraft || null)
const commitMessage = ref('')
const transferring = ref(false)
const transferLabel = ref('')

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
  groupCandidates.value = namespacePage.items.filter(item => item.kind === 'group' && item.id !== props.dataset.namespace_id).map(item => ({ label: item.path, value: item.path }))
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
  const payload = grantForm.principal_type === 'user' ? { username: userGrantUsername.value, role: grantForm.role } : { group_path: groupGrantPath.value, role: grantForm.role }
  try {
    await api.post(`/api/v1/datasets/${props.dataset.id}/grants`, payload)
    userGrantUsername.value = undefined
    groupGrantPath.value = undefined
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

async function ensureDraft() {
  if (activeDraft.value?.state === 'open') return activeDraft.value
  const targetBranch = branches.value.includes(revision.value) ? revision.value : props.dataset.default_branch
  activeDraft.value = await api.post<BrowserDraft>(`/api/v1/datasets/${props.dataset.id}/drafts`, { target_branch: targetBranch })
  return activeDraft.value
}

function selectedPath(file: File) {
  return file.webkitRelativePath || file.name
}

async function storageFor(file: File) {
  if (file.name === '.gitattributes') return 'git'
  if (storageChoice.value !== 'auto') return storageChoice.value
  if (file.size > 10 * 1024 * 1024) return 'lfs'
  const sample = new Uint8Array(await file.slice(0, 8000).arrayBuffer())
  return sample.includes(0) ? 'lfs' : 'git'
}

async function hashFile(file: File) {
  const hash = sha256.create()
  const chunkSize = 8 * 1024 * 1024
  for (let offset = 0; offset < file.size; offset += chunkSize) hash.update(new Uint8Array(await file.slice(offset, Math.min(file.size, offset + chunkSize)).arrayBuffer()))
  return bytesToHex(hash.digest())
}

function usableUploadHeaders(headers: Record<string, string>) {
  return Object.fromEntries(Object.entries(headers).filter(([name]) => name.toLowerCase() !== 'content-length'))
}

async function putDirect(action: DirectUploadAction, body: Blob) {
  const response = await fetch(action.href, { method: action.method, headers: usableUploadHeaders(action.header), body })
  if (!response.ok) throw new Error(`Object storage rejected the upload (${response.status}).`)
  return response
}

async function uploadMultipart(file: File, oid: string, layout: NonNullable<LfsStage['multipart']>) {
  const parts: { part_number: number, etag: string, checksum_sha256?: string, checksum_crc32c?: string, checksum_crc32?: string }[] = []
  for (let number = 1; number <= layout.part_count; number += 1) {
    const start = (number - 1) * layout.part_size
    const part = file.slice(start, Math.min(file.size, start + layout.part_size))
    const action = await api.post<DirectUploadAction>(`/api/v1/datasets/${props.dataset.id}/lfs/objects/${oid}/multipart/${layout.session_id}/parts/${number}`, { size: part.size })
    const response = await putDirect(action, part)
    const etag = response.headers.get('etag')
    if (!etag) throw new Error('Object storage did not expose the multipart ETag. Its CORS policy must expose the ETag header.')
    const completedPart: { part_number: number, etag: string, checksum_sha256?: string, checksum_crc32c?: string, checksum_crc32?: string } = { part_number: number, etag }
    const checksumSha256 = response.headers.get('x-amz-checksum-sha256')
    const checksumCrc32c = response.headers.get('x-amz-checksum-crc32c')
    const checksumCrc32 = response.headers.get('x-amz-checksum-crc32')
    if (checksumSha256) completedPart.checksum_sha256 = checksumSha256
    if (checksumCrc32c) completedPart.checksum_crc32c = checksumCrc32c
    if (checksumCrc32) completedPart.checksum_crc32 = checksumCrc32
    parts.push(completedPart)
  }
  await api.post(`/api/v1/datasets/${props.dataset.id}/lfs/objects/${oid}/multipart/${layout.session_id}/complete`, { parts })
}

async function stageSelectedFiles() {
  if (!selectedFiles.value.length) return
  transferring.value = true
  try {
    const draft = await ensureDraft()
    for (const [index, file] of selectedFiles.value.entries()) {
      const path = selectedPath(file)
      const storage = await storageFor(file)
      transferLabel.value = `${index + 1} of ${selectedFiles.value.length}: ${path}`
      if (storage === 'git') {
        if (file.size > 10 * 1024 * 1024) throw new Error(`${path} exceeds the 10 MiB ordinary Git limit. Use Git LFS.`)
        const query = new URLSearchParams({ path, size: String(file.size) })
        activeDraft.value = await api.put<BrowserDraft>(`/api/v1/datasets/${props.dataset.id}/drafts/${draft.id}/files/git?${query}`, file, { headers: { 'Content-Type': 'application/octet-stream' } })
        continue
      }
      const oid = await hashFile(file)
      const staged = await api.post<LfsStage>(`/api/v1/datasets/${props.dataset.id}/drafts/${draft.id}/files/lfs`, { path, oid, size: file.size })
      if (staged.transfer === 'basic' && staged.upload) {
        await putDirect(staged.upload, file)
        activeDraft.value = await api.post<BrowserDraft>(`/api/v1/datasets/${props.dataset.id}/drafts/${draft.id}/files/lfs/${oid}/complete`)
      } else if (staged.transfer === 'multipart' && staged.multipart) {
        await uploadMultipart(file, oid, staged.multipart)
        activeDraft.value = await api.get<BrowserDraft>(`/api/v1/datasets/${props.dataset.id}/drafts/${draft.id}`)
      } else {
        activeDraft.value = await api.get<BrowserDraft>(`/api/v1/datasets/${props.dataset.id}/drafts/${draft.id}`)
      }
    }
    selectedFiles.value = []
    uploadOpen.value = false
    toast.add({ title: 'Files staged in browser draft', color: 'success' })
  } catch (error) {
    toast.add({ title: 'Could not stage files', description: error instanceof Error ? error.message : undefined, color: 'error' })
  } finally {
    transferring.value = false
    transferLabel.value = ''
  }
}

async function stageFileDeletion(path: string) {
  try {
    const draft = await ensureDraft()
    activeDraft.value = await api.delete<BrowserDraft>(`/api/v1/datasets/${props.dataset.id}/drafts/${draft.id}/files?path=${encodeURIComponent(path)}`)
    toast.add({ title: 'Deletion staged', color: 'success' })
  } catch (error) { toast.add({ title: 'Could not stage deletion', description: error instanceof Error ? error.message : undefined, color: 'error' }) }
}

async function discardDraft() {
  if (!activeDraft.value) return
  try {
    await api.delete(`/api/v1/datasets/${props.dataset.id}/drafts/${activeDraft.value.id}`)
    activeDraft.value = null
    commitMessage.value = ''
    toast.add({ title: 'Draft discarded', color: 'success' })
  } catch (error) { toast.add({ title: 'Could not discard draft', description: error instanceof Error ? error.message : undefined, color: 'error' }) }
}

async function publishDraft() {
  if (!activeDraft.value || !commitMessage.value.trim()) return
  saving.value = true
  try {
    activeDraft.value = await api.post<BrowserDraft>(`/api/v1/datasets/${props.dataset.id}/drafts/${activeDraft.value.id}/commit`, { message: commitMessage.value.trim() })
    toast.add({ title: 'Dataset commit published', color: 'success' })
    window.location.reload()
  } catch (error) { toast.add({ title: 'Could not publish draft', description: error instanceof Error ? error.message : undefined, color: 'error' }) } finally { saving.value = false }
}
</script>

<template>
  <UContainer class="py-8">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div><nav class="flex flex-wrap items-center gap-1 font-mono text-sm text-muted" aria-label="Dataset breadcrumb"><template v-for="(crumb, index) in datasetBreadcrumbs" :key="crumb.to"><span v-if="index" aria-hidden="true">/</span><NuxtLink :to="crumb.to" class="rounded px-1 py-0.5 hover:text-primary">{{ crumb.label }}</NuxtLink></template></nav><h1 class="mt-1 text-2xl font-medium text-highlighted">{{ dataset.name }}</h1><SafeMarkdown v-if="dataset.description" class="mt-2 max-w-3xl text-muted" :content="dataset.description" /></div>
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
      <UButton v-if="canEdit || canDelete" to="?tab=settings" label="Settings" icon="i-lucide-settings" color="neutral" :variant="tab === 'settings' ? 'soft' : 'ghost'" />
    </nav>

    <section v-if="tab === 'files'" class="mt-6">
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <USelect :model-value="revision" :items="[...branches, ...tags]" icon="i-lucide-git-branch" class="min-w-40" :disabled="emptyRepository" @update:model-value="value => changeRevision(String(value))" />
        <UButton label="Root" color="neutral" variant="ghost" @click="navigatePath('')" />
        <template v-for="crumb in breadcrumbs" :key="crumb.path"><span class="text-muted">/</span><UButton :label="crumb.label" color="neutral" variant="ghost" @click="navigatePath(crumb.path)" /></template>
        <span v-if="tree" class="ml-auto font-mono text-xs text-muted">{{ tree.resolved_commit.slice(0, 10) }}</span>
        <UButton v-if="canWrite" label="Upload files" icon="i-lucide-upload" @click="uploadOpen = true" />
      </div>

      <UCard v-if="activeDraft" class="mb-4">
        <template #header><div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><div><h2 class="font-medium text-highlighted">Browser commit draft</h2><p class="mt-1 text-sm text-muted">{{ activeDraft.target_branch }} at {{ activeDraft.base_commit?.slice(0, 10) || 'unborn branch' }}</p></div><UBadge color="neutral" variant="subtle">{{ activeDraft.changes.length }} staged</UBadge></div></template>
        <div class="space-y-2"><div v-for="change in activeDraft.changes" :key="change.path" class="flex flex-wrap items-center gap-2 rounded-md bg-elevated px-3 py-2 text-sm"><UIcon :name="change.operation === 'delete' ? 'i-lucide-trash-2' : 'i-lucide-file-plus-2'" :class="change.operation === 'delete' ? 'text-error' : 'text-primary'" /><span class="min-w-0 flex-1 truncate font-mono">{{ change.path }}</span><UBadge color="neutral" variant="outline">{{ change.operation === 'delete' ? 'delete' : change.storage }}</UBadge><UBadge v-if="!change.ready" color="warning" variant="subtle">upload incomplete</UBadge></div></div>
        <div class="mt-4 grid gap-3"><UFormField label="Commit message"><UTextarea v-model="commitMessage" class="w-full" :maxlength="100000" autoresize /></UFormField><div class="flex justify-end gap-2"><UButton label="Discard" color="neutral" variant="outline" @click="discardDraft" /><UButton label="Commit changes" icon="i-lucide-git-commit-horizontal" :disabled="!activeDraft.changes.length || activeDraft.changes.some(change => !change.ready) || !commitMessage.trim()" :loading="saving" @click="publishDraft" /></div></div>
      </UCard>

      <UCard v-if="emptyRepository">
        <template #header><div class="flex items-start gap-3"><UIcon name="i-lucide-git-commit-horizontal" class="mt-0.5 size-5 text-primary" /><div><h2 class="font-medium text-highlighted">This dataset is ready for its first files</h2><p class="mt-1 text-sm text-muted">Upload files above to create a browser commit, or use the Niyān CLI for a local working copy.</p></div></div></template>
        <div class="mb-4 flex gap-1 border-b border-default">
          <UButton label="Start fresh" color="neutral" :variant="emptyGuide === 'new' ? 'soft' : 'ghost'" @click="emptyGuide = 'new'" />
          <UButton label="Upload existing files" color="neutral" :variant="emptyGuide === 'existing' ? 'soft' : 'ghost'" @click="emptyGuide = 'existing'" />
        </div>
        <p class="mb-3 text-sm text-muted">{{ emptyGuide === 'new' ? 'Clone the empty dataset, add files, and publish its first commit.' : 'Clone into a clean directory, copy your existing files into it, and publish them.' }}</p>
        <div class="relative rounded-md bg-elevated p-4 pr-12"><pre class="overflow-x-auto text-sm"><code>{{ emptyGuide === 'new' ? newDatasetCommands : existingDatasetCommands }}</code></pre><UButton icon="i-lucide-copy" aria-label="Copy setup commands" color="neutral" variant="ghost" size="sm" class="absolute right-2 top-2" @click="copyText(emptyGuide === 'new' ? newDatasetCommands : existingDatasetCommands, 'Setup commands copied')" /></div>
      </UCard>
      <div v-else class="overflow-hidden rounded-lg border border-default bg-default">
        <div v-for="entry in tree?.items" :key="entry.path" class="flex items-center border-b border-default last:border-b-0 hover:bg-elevated">
          <button type="button" class="grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)_auto] items-center gap-4 p-3 text-left sm:grid-cols-[minmax(0,1fr)_10rem_8rem]" @click="openEntry(entry)"><span class="flex min-w-0 items-center gap-2"><UIcon :name="entry.object_type === 'tree' ? 'i-lucide-folder' : 'i-lucide-file'" class="size-4 shrink-0 text-primary" /><span class="truncate text-highlighted">{{ entry.name }}</span></span><span class="hidden font-mono text-xs text-muted sm:block">{{ entry.object_id.slice(0, 10) }}</span><span class="text-right text-sm text-muted">{{ entry.object_type === 'tree' ? 'Directory' : formatBytes(entry.size) }}</span></button>
          <UButton v-if="canWrite && entry.object_type === 'blob'" icon="i-lucide-trash-2" :aria-label="`Stage deletion of ${entry.name}`" color="error" variant="ghost" class="mr-2 shrink-0" @click="stageFileDeletion(entry.path)" />
        </div>
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
          <USelect v-model="grantForm.principal_type" :items="[{ label: 'User', value: 'user' }, { label: 'Group', value: 'group' }]" @update:model-value="userGrantUsername = undefined; groupGrantPath = undefined" />
          <div v-if="grantForm.principal_type === 'user'" class="flex gap-2"><UInput v-model="userQuery" placeholder="Search users" class="flex-1" @keyup.enter="searchUsers" /><UButton icon="i-lucide-search" aria-label="Search users" color="neutral" variant="outline" @click="searchUsers" /></div>
          <USelect v-else v-model="groupGrantPath" :items="groupCandidates" placeholder="Select group" />
          <USelect v-model="grantForm.role" :items="['reader', 'contributor', 'maintainer', 'owner']" />
          <UButton label="Add" :disabled="selectedGrantPrincipal === undefined" :loading="saving" @click="addGrant" />
        </div>
        <USelect v-if="grantForm.principal_type === 'user' && userResults.length" v-model="userGrantUsername" class="mt-3 w-full" :items="userResults.map(user => ({ label: `${user.display_name} (${user.username})`, value: user.username }))" placeholder="Select user" />
      </UCard>
      <div class="overflow-hidden rounded-lg border border-default"><div v-for="grant in grants" :key="grant.id" class="grid gap-3 border-b border-default p-4 last:border-b-0 sm:grid-cols-[1fr_12rem_auto] sm:items-center"><div><p class="font-medium text-highlighted">{{ grant.principal_label }}</p><p class="text-sm text-muted">{{ grant.principal_type }}</p></div><USelect :model-value="grant.role" :items="['reader', 'contributor', 'maintainer', 'owner']" @update:model-value="value => changeGrant(grant, value as Role)" /><UButton icon="i-lucide-trash-2" aria-label="Remove grant" color="error" variant="ghost" @click="removeGrant(grant)" /></div><p v-if="!grants.length" class="p-8 text-center text-muted">No explicit grants. Namespace membership may still provide access.</p></div>
    </section>

    <section v-else-if="tab === 'settings' && (canEdit || canDelete)" class="mt-6 space-y-6">
      <UCard v-if="canEdit"><template #header><h2 class="font-medium">Dataset details</h2></template><form class="grid gap-4 sm:grid-cols-2" @submit.prevent="updateDataset"><UFormField label="Name"><UInput v-model="editForm.name" class="w-full" /></UFormField><UFormField label="Slug"><UInput v-model="editForm.slug" class="w-full" /></UFormField><UFormField label="Description" hint="Optional" class="sm:col-span-2"><UTextarea v-model="editForm.description" :maxlength="500" autoresize class="w-full" /></UFormField><div class="flex justify-end sm:col-span-2"><UButton type="submit" label="Save changes" :loading="saving" /></div></form></UCard>
      <UCard v-if="canDelete" class="ring-error/30"><template #header><h2 class="font-medium text-error">Delete dataset</h2></template><p class="mb-4 text-sm text-muted">Type <strong class="font-mono text-highlighted">{{ dataset.namespace_path }}/{{ dataset.slug }}</strong> to permanently delete the repository and its files.</p><div class="flex flex-col gap-3 sm:flex-row"><UInput v-model="deleteConfirmation" class="flex-1" /><UButton label="Delete dataset" color="error" :disabled="deleteConfirmation !== `${dataset.namespace_path}/${dataset.slug}`" :loading="saving" @click="deleteDataset" /></div></UCard>
    </section>

    <UModal v-model:open="uploadOpen" title="Stage files for a browser commit" description="Files are staged in a private draft until you explicitly commit them.">
      <template #body><div class="space-y-4"><UFileUpload v-model="selectedFiles" multiple class="w-full" label="Choose dataset files" description="Binary files and files above 10 MiB use Git LFS automatically." /><UFormField label="Storage"><USelect v-model="storageChoice" :items="[{ label: 'Automatic (recommended)', value: 'auto' }, { label: 'Ordinary Git', value: 'git' }, { label: 'Git LFS', value: 'lfs' }]" class="w-full" /></UFormField><UAlert v-if="transferLabel" color="neutral" variant="soft" :description="transferLabel" /></div></template>
      <template #footer><div class="flex w-full justify-end gap-2"><UButton label="Cancel" color="neutral" variant="outline" :disabled="transferring" @click="uploadOpen = false" /><UButton label="Stage files" icon="i-lucide-upload" :disabled="!selectedFiles.length" :loading="transferring" @click="stageSelectedFiles" /></div></template>
    </UModal>
  </UContainer>
</template>
