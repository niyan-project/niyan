export type Role = 'reader' | 'contributor' | 'maintainer' | 'owner'

export interface ApiErrorBody { code: string, detail: string }
export interface CurrentUser { id: number, username: string, display_name: string, first_name?: string, last_name?: string, email: string, is_staff: boolean, is_superuser: boolean, system_permissions: SystemPermission[], authentication_method: 'session' | 'access_token', access_token: AccessTokenMetadata | null }
export interface UserProfile { id: number, username: string, display_name: string, first_name: string, last_name: string, email: string }
export type SystemPermission = 'users.view' | 'users.add' | 'users.change' | 'staff.view' | 'staff.change' | 'permission_groups.view' | 'permission_groups.add' | 'permission_groups.change' | 'permission_groups.delete' | 'groups.view' | 'groups.add' | 'groups.change' | 'groups.delete' | 'datasets.view' | 'datasets.add' | 'datasets.change' | 'datasets.delete'
export interface SystemUser { id: number, username: string, display_name: string, email: string, first_name: string, last_name: string, is_active: boolean, is_staff: boolean, is_superuser: boolean, date_joined: string, last_login: string | null, permission_group_ids: number[] }
export interface SystemUserList { count: number, limit: number, offset: number, items: SystemUser[] }
export interface PermissionGroup { id: number, name: string, permissions: SystemPermission[] }
export interface PermissionGroupList { count: number, available_permissions: SystemPermission[], items: PermissionGroup[] }
export interface Namespace { id: string, parent_id: string | null, parent_path: string | null, path: string, slug: string, name: string, kind: 'personal' | 'group', role: Role | null, can_create_dataset: boolean, can_create_group: boolean, can_manage: boolean, can_delete: boolean, created_at: string, updated_at: string }
export interface NamespaceList { count: number, limit: number, offset: number, items: Namespace[] }
export interface Dataset { id: string, namespace_id: string, namespace_path: string, slug: string, name: string, description: string, default_branch: string, role: Role | null, can_write: boolean, can_update: boolean, can_manage_access: boolean, can_delete: boolean, created_at: string }
export interface DatasetList { count: number, limit: number, offset: number, items: Dataset[] }
export interface RefItem { name: string, full_name: string, object_id: string, object_type: string }
export interface RefList { kind: 'branches' | 'tags', limit: number, offset: number, next_offset: number | null, items: RefItem[] }
export interface CommitAuthorUser { id: number, username: string, display_name: string }
export interface CommitItem { object_id: string, parent_ids: string[], author_name: string, author_email: string, authored_at: string, subject: string, body: string, author_user: CommitAuthorUser | null }
export interface CommitList { resolved_commit: string, limit: number, offset: number, next_offset: number | null, items: CommitItem[] }
export interface TreeEntry { name: string, path: string, mode: string, object_type: 'tree' | 'blob', object_id: string, size: number | null }
export interface TreeList { resolved_commit: string, path: string, limit: number, offset: number, next_offset: number | null, items: TreeEntry[] }
export interface BlobMetadata { resolved_commit: string, path: string, object_id: string, size: number, is_lfs: boolean, lfs_object_id: string | null, lfs_size: number | null }
export interface Readme extends BlobMetadata { content: string }
export interface DownloadAction { resolved_commit: string, path: string, size: number, storage: 'git' | 'lfs', method: 'GET', url: string, headers: Record<string, string>, expires_in: number | null }
export interface Membership { id: number, namespace_id: string, user_id: number, username: string, display_name: string, role: Role, created_at: string, updated_at: string }
export interface MembershipList { count: number, items: Membership[] }
export interface DatasetGrant { id: number, dataset_id: string, principal_type: 'user' | 'group', principal_label: string, user_id: number | null, group_namespace_id: string | null, role: Role, created_at: string, updated_at: string }
export interface GrantList { count: number, items: DatasetGrant[] }
export interface UserSearchItem { id: number, username: string, display_name: string }
export interface AccessTokenMetadata { id: string, name: string, origin: 'manual' | 'cli', fingerprint: string, resource_boundary: 'user' | 'dataset', dataset_id: string | null, dataset_path: string | null, scopes: string[], created_at: string, last_used_at: string | null, expires_at: string, revoked_at: string | null, active: boolean }
export interface AccessTokenCreated extends AccessTokenMetadata { token: string }
export interface AccessTokenList { count: number, items: AccessTokenMetadata[] }
export interface DeviceAuthorization { user_code: string, name: string, scopes: string[], dataset_path: string | null, expires_at: string }
export interface BrowserDraftChange { path: string, operation: 'upsert' | 'delete', storage: 'git' | 'lfs' | '', size: number | null, oid: string | null, ready: boolean }
export interface BrowserDraft { id: string, dataset_id: string, target_branch: string, base_commit: string | null, state: 'open' | 'committed' | 'discarded', committed_oid: string | null, expires_at: string, changes: BrowserDraftChange[] }
export interface BrowserDraftList { count: number, items: BrowserDraft[] }
export interface DirectUploadAction { method: 'PUT', href: string, header: Record<string, string>, expires_in: number }
export interface MultipartLayout { session_id: string, part_size: number, part_count: number }
export interface LfsStage { change: BrowserDraftChange, transfer: 'existing' | 'basic' | 'multipart', upload: DirectUploadAction | null, multipart: MultipartLayout | null }
