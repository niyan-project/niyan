from django.contrib import admin

from datasets.models import Dataset


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    """Expose dataset control-plane records to installation administrators."""

    list_display = ('path', 'name', 'created_by', 'created_at')
    search_fields = ('name', 'slug', 'namespace__name', 'namespace__slug')
