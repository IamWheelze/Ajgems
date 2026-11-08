from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Organisation, User

admin.site.register(Organisation)


@admin.register(User)
class MinefootUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Organisation and roles", {"fields": ("organisation", "roles")}),)
    list_display = ("username", "organisation", "roles", "is_active")
