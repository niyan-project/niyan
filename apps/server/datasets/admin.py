from django.contrib import admin

from datasets.models import Dataset, DatasetGrant, GitPushContext, GitPushLfsLease, GitPushRef, LfsMultipartUpload, LfsObject


class DatasetGrantInline(admin.TabularInline):
    """Edit dataset grants from the dataset administration page."""

    model = DatasetGrant
    extra = 0
    autocomplete_fields = ('user', 'group_namespace')


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    """Expose dataset control-plane records to installation administrators."""

    list_display = ('path', 'name', 'created_by', 'created_at')
    search_fields = ('name', 'slug', 'namespace__name', 'namespace__slug')
    inlines = (DatasetGrantInline,)


@admin.register(DatasetGrant)
class DatasetGrantAdmin(admin.ModelAdmin):
    """Expose dataset access grants to installation administrators."""

    list_display = ('dataset', 'principal_label', 'principal_type', 'role', 'created_at')
    list_filter = ('role',)
    search_fields = ('dataset__name', 'dataset__slug', 'user__username', 'group_namespace__name', 'group_namespace__slug')
    autocomplete_fields = ('dataset', 'user', 'group_namespace')
    list_select_related = ('dataset__namespace', 'user', 'group_namespace')


@admin.register(LfsObject)
class LfsObjectAdmin(admin.ModelAdmin):
    """Expose immutable LFS lifecycle metadata for operational inspection."""

    list_display = ('oid', 'dataset', 'size', 'state', 'verification_method', 'created_at', 'available_at', 'referenced_at')
    list_filter = ('state', 'verification_method')
    search_fields = ('oid', 'dataset__name', 'dataset__slug', 'dataset__namespace__name', 'dataset__namespace__slug')
    readonly_fields = ('dataset', 'oid', 'size', 'state', 'verification_method', 'verified_checksum', 'storage_key', 'available_at', 'referenced_at', 'created_at', 'updated_at')
    list_select_related = ('dataset__namespace',)

    def has_add_permission(self, request):
        """Prevent administrators from bypassing transfer negotiation."""

        return False

    def has_delete_permission(self, request, obj=None):
        """Require lifecycle services to coordinate metadata and object deletion."""

        return False


@admin.register(LfsMultipartUpload)
class LfsMultipartUploadAdmin(admin.ModelAdmin):
    """Expose multipart lifecycle state without provider upload credentials."""

    list_display = ('id', 'lfs_object', 'state', 'part_size', 'expires_at', 'created_at', 'completed_at', 'aborted_at')
    list_filter = ('state',)
    search_fields = ('id', 'lfs_object__oid', 'lfs_object__dataset__name', 'lfs_object__dataset__slug')
    readonly_fields = ('id', 'lfs_object', 'part_size', 'state', 'expires_at', 'completed_at', 'aborted_at', 'created_at', 'updated_at')
    list_select_related = ('lfs_object__dataset__namespace',)

    def has_add_permission(self, request):
        """Require multipart initiation through the authenticated service."""

        return False

    def has_delete_permission(self, request, obj=None):
        """Require provider-aware abort and cleanup before metadata deletion."""

        return False


class ReadOnlyLifecycleAdmin(admin.ModelAdmin):
    """Keep internal receive records inspectable but service-owned."""

    def has_add_permission(self, request):
        """Require lifecycle records to originate from receive-pack."""

        return False

    def has_change_permission(self, request, obj=None):
        """Prevent manual mutation of receive evidence."""

        return False

    def has_delete_permission(self, request, obj=None):
        """Leave lifecycle retention to maintenance services."""

        return False


@admin.register(GitPushContext)
class GitPushContextAdmin(ReadOnlyLifecycleAdmin):
    """Expose opaque receive authorization lifecycle for operations."""

    list_display = ('id', 'dataset', 'user', 'role', 'created_at', 'expires_at', 'consumed_at', 'validated_at', 'completed_at')
    list_filter = ('role',)
    search_fields = ('id', 'request_id', 'dataset__slug', 'dataset__name', 'user__username')
    list_select_related = ('dataset__namespace', 'user')


@admin.register(GitPushRef)
class GitPushRefAdmin(ReadOnlyLifecycleAdmin):
    """Expose validated and accepted ref proposals for reconciliation."""

    list_display = ('ref_name', 'push_context', 'old_oid', 'new_oid', 'accepted_at')
    search_fields = ('ref_name', 'old_oid', 'new_oid', 'push_context__id')
    list_select_related = ('push_context__dataset',)


@admin.register(GitPushLfsLease)
class GitPushLfsLeaseAdmin(ReadOnlyLifecycleAdmin):
    """Expose temporary cleanup protection for verified LFS objects."""

    list_display = ('push_ref', 'lfs_object', 'created_at', 'expires_at')
    search_fields = ('push_ref__ref_name', 'lfs_object__oid', 'push_ref__push_context__id')
    list_select_related = ('push_ref__push_context', 'lfs_object__dataset')
