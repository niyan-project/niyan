from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import AccessToken, DeviceAuthorization, User


admin.site.register(User, UserAdmin)


@admin.register(AccessToken)
class AccessTokenAdmin(admin.ModelAdmin):
    """Expose access-token audit metadata without revealing credentials."""

    list_display = ('name', 'user', 'origin', 'resource_boundary', 'dataset', 'created_at', 'last_used_at', 'expires_at', 'revoked_at')
    list_filter = ('origin', 'resource_boundary', 'created_at', 'expires_at', 'revoked_at')
    search_fields = ('name', 'user__username', 'selector', 'dataset__slug')
    ordering = ('-created_at', '-id')
    fields = ('id', 'name', 'user', 'origin', 'fingerprint', 'resource_boundary', 'dataset', 'scopes', 'created_at', 'last_used_at', 'expires_at', 'revoked_at')
    readonly_fields = fields

    def has_add_permission(self, request):
        """Prevent creation because admin cannot safely reveal a token secret."""

        return False


@admin.register(DeviceAuthorization)
class DeviceAuthorizationAdmin(admin.ModelAdmin):
    """Expose device-login lifecycle metadata without private device codes."""

    list_display = ('user_code', 'name', 'status', 'approved_by', 'dataset', 'created_at', 'expires_at', 'decided_at', 'consumed_at')
    list_filter = ('status', 'created_at', 'expires_at', 'decided_at', 'consumed_at')
    search_fields = ('user_code', 'name', 'approved_by__username', 'requested_dataset_path')
    ordering = ('-created_at', '-id')
    fields = ('id', 'user_code', 'name', 'requested_scopes', 'requested_dataset_path', 'status', 'approved_by', 'dataset', 'created_at', 'expires_at', 'last_polled_at', 'decided_at', 'consumed_at')
    readonly_fields = fields

    def has_add_permission(self, request):
        """Prevent creation outside the device-authorization service."""

        return False
