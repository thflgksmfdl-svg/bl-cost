import uuid
from django.conf import settings
from django.db import models


class Batch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bl = models.CharField(max_length=12, unique=True)
    state = models.CharField(max_length=20, default="queued", db_index=True)
    progress = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    source = models.JSONField(default=dict)
    orders = models.JSONField(default=list)
    rules = models.JSONField(default=dict)
    summary = models.JSONField(default=dict)
    sheet_plan = models.JSONField(default=dict)
    sheet_result = models.JSONField(default=dict)
    source_hash = models.CharField(max_length=64, blank=True)
    revision = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="batches")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="approvals")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "blcost_batch"
        ordering = ["-created_at"]


class Audit(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=40)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "blcost_audit"


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    since = models.DateTimeField()

    class Meta:
        db_table = "blcost_login_attempt"
