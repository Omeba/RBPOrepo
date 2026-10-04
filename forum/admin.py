"""Inspection only: content mutations retain the forum's service boundary."""
from django.contrib import admin
from django.shortcuts import redirect
from .models import User, Tag, Topic, Message, Revision


class ForumAdminSite(admin.AdminSite):
    site_header = 'humAIn — администрирование'
    site_title = 'humAIn'
    index_title = 'Просмотр данных; управление контентом — через форум'

    def login(self, request, extra_context=None):
        # All logins must use the locked password/version check from D-03.
        return redirect('login')

    def password_change(self, request, extra_context=None):
        return redirect('credentials')

    def password_change_done(self, request, extra_context=None):
        return redirect('credentials')


class ReadOnlyAdmin(admin.ModelAdmin):
    actions = None

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class UserAdmin(ReadOnlyAdmin):
    fields = ('username', 'is_active', 'is_staff', 'is_superuser', 'is_moderator',
              'credential_version', 'date_joined', 'last_login')
    list_display = ('username', 'is_active', 'is_staff', 'is_moderator')
    search_fields = ('username',)


admin_site = ForumAdminSite(name='admin')
admin_site.register(User, UserAdmin)
for model in (Tag, Topic, Message, Revision):
    admin_site.register(model, ReadOnlyAdmin)
