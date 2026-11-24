from django.contrib import admin
from .models import Audit, Batch


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("bl", "state", "created_by", "updated_at")
    readonly_fields = [f.name for f in Batch._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Audit)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("batch", "action", "actor", "created_at")
    readonly_fields = [f.name for f in Audit._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
