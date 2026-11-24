import logging
import time
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from ledger.domain import DataError, ReviewRequired, assemble, fingerprint, summarize
from ledger.jungpan import Jungpan
from ledger.models import Audit, Batch
from ledger.sheets import Sheets

logger = logging.getLogger(__name__)


def process(batch):
    if batch.state == "fetching":
        def checkpoint(source, done, total):
            Batch.objects.filter(pk=batch.pk).update(source=source, progress=done, total=total, updated_at=timezone.now())
        with Jungpan() as jp:
            source = jp.collect(batch.bl, checkpoint, cached=batch.source)
        orders = assemble(source)
        try:
            rules = Sheets().rules(f"{batch.bl[:4]}-{batch.bl[4:6]}")
            error = ""
        except DataError as e:
            rules, error = {}, str(e)
        with transaction.atomic():
            batch = Batch.objects.select_for_update().get(pk=batch.pk)
            batch.source, batch.orders, batch.rules = source, orders, rules
            batch.source_hash, batch.summary = fingerprint(source), summarize(orders, rules)
            batch.state, batch.error = "review", error
            batch.revision += 1
            batch.save()
            Audit.objects.create(batch=batch, action="수집 완료", detail={"summary": batch.summary, "source_hash": batch.source_hash})
    else:
        client = Sheets()
        if not client.marker(batch.id):
            with Jungpan() as jp:
                source = jp.collect(batch.bl, lambda data, done, total: Batch.objects.filter(pk=batch.pk).update(
                    progress=done, total=total, updated_at=timezone.now()))
            if fingerprint(source) != batch.source_hash:
                raise DataError("승인 후 중판 원본이 변경되었습니다. 원본을 다시 조회하고 검토해 주세요.")
            if client.rules(batch.rules["month"])["version"] != batch.rules["version"]:
                raise ReviewRequired("승인 후 청구규칙이 변경되었습니다. 규칙을 다시 불러와 검토해 주세요.")
        result = client.save(batch)
        with transaction.atomic():
            batch = Batch.objects.select_for_update().get(pk=batch.pk)
            batch.state, batch.sheet_result, batch.error = "saved", result, ""
            batch.revision += 1
            batch.save()
            Audit.objects.create(batch=batch, action="시트 반영 및 검산 완료", detail=result)


class Command(BaseCommand):
    help = "중판 조회·승인된 시트 저장을 DB 대기열에서 처리합니다."

    def handle(self, *args, **options):
        if connection.vendor == "postgresql":
            with connection.cursor() as c:
                c.execute("SELECT pg_try_advisory_lock(734082914602)")
                if not c.fetchone()[0]:
                    self.stdout.write("다른 작업 처리기가 실행 중입니다.")
                    return
        Batch.objects.filter(state="fetching").update(state="queued")
        Batch.objects.filter(state="saving").update(state="approved")
        while True:
            with transaction.atomic():
                batch = Batch.objects.select_for_update().filter(state__in=["queued", "approved"]).order_by("created_at").first()
                if batch:
                    batch.state = "fetching" if batch.state == "queued" else "saving"
                    batch.save(update_fields=["state", "updated_at"])
            if batch:
                try:
                    process(batch)
                except Exception as e:
                    message = str(e) if isinstance(e, DataError) else "처리 중 오류가 발생했습니다. 원본과 진행 내역은 보존되어 있습니다."
                    logger.error("BL task failed: %s (%s)", batch.bl, type(e).__name__)
                    values = {"state": "error", "error": message, "updated_at": timezone.now()}
                    if isinstance(e, ReviewRequired):
                        values.update(state="review", approved_by=None, approved_at=None, sheet_plan={})
                    Batch.objects.filter(pk=batch.pk).update(**values)
                    Audit.objects.create(batch=batch, action="처리 오류", detail={"error": message})
            else:
                time.sleep(2)
