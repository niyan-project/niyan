from django.contrib import admin

from datasets.models import Dataset, DatasetGrant


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
