import logging
import time
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from ledger.domain import DataError, ReviewRequired, assemble, source_fingerprint, summarize
from ledger.jungpan import Jungpan
from ledger.models import Audit, Batch, ReportSnapshot
from ledger.reports import queue_reports, sync_reports
from ledger.sheets import Sheets

logger = logging.getLogger(__name__)


def process(batch):
    if batch.state == "fetching":
        def checkpoint(source, done, total):
            if done % 5 == 0 or done == total:
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
            batch.source_hash, batch.summary = source_fingerprint(source), summarize(orders, rules)
            batch.state, batch.error = "review", error
            batch.collected_at = timezone.now()
            if rules:
                batch.queue_sync()
            batch.revision += 1
            batch.save()
            Audit.objects.create(batch=batch, action="수집 완료", detail={"summary": batch.summary, "source_hash": batch.source_hash})
        queue_reports()
    else:
        client = Sheets()
        if not client.marker(batch):
            if client.rules(batch.rules["month"])["version"] != batch.rules["version"]:
                raise ReviewRequired("시트의 청구규칙이 바뀌었습니다. ‘청구규칙 다시 적용’을 눌러 주세요. 중판을 다시 조회할 필요는 없습니다.")
            if not batch.sheet_plan or "held" not in batch.sheet_plan:
                batch.sheet_plan = client.prepare(batch)
                with transaction.atomic():
                    if Batch.objects.filter(pk=batch.pk,revision=batch.revision,state="saving").update(sheet_plan=batch.sheet_plan)!=1:
                        raise ReviewRequired("다른 작업에서 내역이 바뀌었습니다. 최신 내용을 확인한 뒤 다시 동기화해 주세요.")
                    Audit.objects.create(batch=batch,action="자동 반영 준비",detail={"revision":batch.revision,"plan":batch.sheet_plan})
        result = client.save(batch)
        with transaction.atomic():
            batch = Batch.objects.select_for_update().get(pk=batch.pk)
            batch.state = "review" if batch.summary.get("pending") else "saved"
            batch.sheet_result = {**result,"synced_revision":batch.revision+1,"synced_at":timezone.now().isoformat(),
                                  "invoice_count":batch.summary["invoice_count"],"pending":batch.summary["pending"]}
            batch.error = ""
            batch.revision += 1
            batch.save()
            Audit.objects.create(batch=batch, action="시트 자동 반영 및 검산 완료", detail=batch.sheet_result)
        queue_reports()


class Command(BaseCommand):
    help = "중판 조회·확정 청구의 자동 시트 저장을 DB 대기열에서 처리합니다."

    def handle(self, *args, **options):
        if connection.vendor == "postgresql":
            while True:
                with connection.cursor() as c:
                    c.execute("SELECT pg_try_advisory_lock(734082914602)")
                    if c.fetchone()[0]:
                        break
                time.sleep(2)
        Batch.objects.filter(state="fetching").update(state="queued")
        Batch.objects.filter(state="saving").update(state="approved")
        ReportSnapshot.objects.get_or_create(pk=1)
        ReportSnapshot.objects.filter(state="refreshing").update(state="queued")
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
                report = ReportSnapshot.objects.get(pk=1)
                due = report.synced_at and (timezone.now() - report.synced_at).total_seconds() >= 600
                if report.state == "queued" or (report.state == "ready" and due):
                    try:
                        sync_reports(Sheets())
                    except Exception as e:
                        logger.error("Report sync failed (%s)", type(e).__name__)
                        message = str(e) if isinstance(e, DataError) else "보고서 갱신을 완료하지 못했습니다. 이전 집계는 보존했습니다. 다시 불러오기를 눌러 주세요."
                        ReportSnapshot.objects.filter(pk=1).update(state="error", error=message)
                time.sleep(2)
