from django.contrib import admin

from namespaces.models import Namespace, NamespaceMembership


class NamespaceMembershipInline(admin.TabularInline):
    """Edit Niyān memberships directly from a group namespace page."""

    model = NamespaceMembership
    extra = 0
    autocomplete_fields = ('user',)


@admin.register(Namespace)
class NamespaceAdmin(admin.ModelAdmin):
    """Expose namespace identity and hierarchy to installation administrators."""

    list_display = ('path', 'kind', 'name', 'owner_user', 'created_at')
    list_filter = ('kind',)
    search_fields = ('name', 'slug')

    def get_inlines(self, request, obj):
        """Show membership editing only for persisted group namespaces.

        Parameters
        ----------
        request : django.http.HttpRequest
            Current Django admin request.
        obj : namespaces.models.Namespace or None
            Namespace being changed, or ``None`` on the creation form.

        Returns
        -------
        tuple[type]
            Inline administrator classes appropriate for the namespace.
        """

        if obj is None or obj.kind != Namespace.Kind.GROUP:
            return ()
        return (NamespaceMembershipInline,)


@admin.register(NamespaceMembership)
class NamespaceMembershipAdmin(admin.ModelAdmin):
    """Expose Niyān group membership without using Django auth groups."""

    list_display = ('namespace', 'user', 'role', 'created_at')
    list_filter = ('role',)
    search_fields = ('namespace__name', 'namespace__slug', 'user__username')
