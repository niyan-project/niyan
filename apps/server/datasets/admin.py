from django.contrib import admin

from datasets.models import Dataset, DatasetGrant, LfsObject


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
