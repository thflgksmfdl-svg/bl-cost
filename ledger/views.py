import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .domain import DataError, calculate, decimal, summarize, review_issues, invoice_message
from .models import Audit, Batch, LoginAttempt
from .sheets import Sheets
from .jungpan import tracking_url


def payload(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeDecodeError):
        raise DataError("요청 형식이 올바르지 않습니다.") from None


def serialized(batch, detail=False):
    data = {"id": str(batch.id), "bl": batch.bl, "state": batch.state, "progress": batch.progress,
            "total": batch.total, "summary": batch.summary, "revision": batch.revision, "error": batch.error,
            "created_at": batch.created_at.isoformat(), "updated_at": batch.updated_at.isoformat()}
    if batch.orders:
        data["summary"] = summarize(batch.orders, batch.rules)
    if detail:
        decisions, invoices = [], []
        for order in batch.orders:
            o = {k:v for k,v in order.items() if k in {"id","customer","jungpan_order","shop_orders","trackings","mode","amount","memo","opp","boxes","extra_hours","extra_amount","reason","free_hours_override","reviewed"}}
            issues = review_issues(order, batch.rules)
            c=calculate(order,batch.rules)
            o.update(review_issues=issues,calculation=c,
                source_links=[{"tracking":t,"url":tracking_url(t)} for t in order["trackings"] if str(t).strip()])
            if issues and not order["reviewed"]:
                decisions.append(o)
            elif c["ready"] and decimal(c["total"]) > 0:
                o["message"]=invoice_message(order,batch.rules)
                invoices.append(o)
        data.update(decisions=decisions,invoices=invoices,
                    rules={k:v for k,v in batch.rules.items() if k in ("month","version","free_boxes")},
                    audit=list(batch.audit_set.order_by("-created_at").values("action", "created_at", "actor__username")[:25]))
    return data


@never_cache
@require_http_methods(["GET", "POST"])
def sign_in(request):
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "")[:150]
        ip = request.META.get("REMOTE_ADDR", "")
        key = hashlib.sha256(f"{ip}:{username.casefold()}".encode()).hexdigest()
        now = timezone.now()
        with transaction.atomic():
            attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(key=key, defaults={"since": now})
            if now - attempt.since > timedelta(minutes=15):
                attempt.count, attempt.since = 0, now
            if attempt.count >= 8:
                error = "로그인 시도가 많습니다. 15분 후 다시 시도해 주세요."
            else:
                user = authenticate(request, username=username, password=request.POST.get("password", ""))
                if user:
                    attempt.delete()
                    login(request, user)
                    return redirect("/")
                attempt.count += 1
                attempt.save()
                error = "아이디와 비밀번호를 확인해 주세요."
    return render(request, "login.html", {"error": error})


@require_POST
def sign_out(request):
    logout(request)
    return redirect("/login/")


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@never_cache
@login_required
def index(request):
    return render(request, "index.html")


@never_cache
@login_required
@require_GET
def status(request):
    return JsonResponse({"user": request.user.username, "can_approve": request.user.is_staff,
       "jungpan_configured": all(os.getenv(k) for k in ("JUNGPAN_ADMIN_ID", "JUNGPAN_ADMIN_PW", "JUNGPAN_ADMIN_PWD2")),
       "sheets_configured": bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")),
       "database_persistent": bool(os.getenv("DATABASE_URL"))})


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def batches(request):
    if request.method == "GET":
        return JsonResponse({"batches": [serialized(b) for b in Batch.objects.all()[:100]]})
    try:
        data = payload(request)
        bl = str(data.get("bl", "")).strip()
        if not re.fullmatch(r"20\d{10}", bl):
            raise DataError("12자리 BL 번호를 입력해 주세요.")
        try:
            datetime.strptime(bl[:8], "%Y%m%d")
        except ValueError:
            raise DataError("BL 앞 8자리의 날짜를 확인해 주세요.") from None
        with transaction.atomic():
            batch, created = Batch.objects.get_or_create(bl=bl, defaults={"created_by": request.user})
            if created:
                Audit.objects.create(batch=batch, actor=request.user, action="조회 요청")
        return JsonResponse(serialized(batch, True), status=201 if created else 200)
    except DataError as e:
        return JsonResponse({"error": str(e)}, status=400)


@never_cache
@login_required
@require_GET
def batch_detail(request, pk):
    try:
        return JsonResponse(serialized(Batch.objects.get(pk=pk), True))
    except Batch.DoesNotExist:
        return JsonResponse({"error": "조회 이력이 없습니다."}, status=404)


def edited_order(order, changes, require_reason=True):
    allowed = {"customer", "mode", "opp", "boxes", "cbm", "extra_hours", "extra_amount", "reason",
               "reviewed", "waive_work", "waive_total", "manual_total", "free_hours_override"}
    if set(changes) - allowed:
        raise DataError("자동 수집한 원본은 수정할 수 없습니다.")
    o = dict(order)
    for key, value in changes.items():
        if key in ("reviewed", "waive_work", "waive_total"):
            if not isinstance(value, bool):
                raise DataError("선택값을 확인해 주세요.")
            o[key] = value
        elif key in ("customer", "reason"):
            if not isinstance(value, str) or len(value) > (200 if key == "customer" else 1000):
                raise DataError("고객명·사유의 길이를 확인해 주세요.")
            o[key] = value.strip()
        elif key == "mode":
            if value not in ("직접수령", "로켓그로스"):
                raise DataError("입고방식을 선택해 주세요.")
            o[key] = value
        elif value is None or value == "":
            o[key] = None if key in ("opp", "boxes", "manual_total", "free_hours_override") else "0"
        else:
            n = decimal(value, maximum=Decimal("1000000") if key in ("opp", "boxes") else Decimal("1000000000"))
            if key in ("opp", "boxes") and n != n.to_integral_value():
                raise DataError("포장재 수량은 정수로 입력해 주세요.")
            o[key] = int(n) if key in ("opp", "boxes") else str(n)
    adjusted = (decimal(o["extra_hours"]) or decimal(o["extra_amount"]) or o["waive_work"] or o["waive_total"]
                or o["manual_total"] is not None or o["free_hours_override"] is not None)
    if require_reason and adjusted and not o["reason"]:
        raise DataError("추가 청구·면제·직접 입력의 사유를 적어 주세요.")
    if require_reason and decimal(o["extra_hours"]) and decimal(o["extra_amount"]) and not o["reason"]:
        raise DataError("시간·금액을 함께 추가하는 경우 각각의 사유를 적어 주세요.")
    return o


@never_cache
@login_required
@require_POST
def batch_action(request, pk, action):
    try:
        data = payload(request)
        with transaction.atomic():
            batch = Batch.objects.select_for_update().get(pk=pk)
            if data.get("revision") != batch.revision:
                return JsonResponse({"error": "다른 직원이 내용을 바꿨습니다. 최신 내용을 불러와 다시 확인해 주세요."}, status=409)
            if action == "preview":
                if batch.state != "review":
                    raise DataError("검토 중인 초안만 미리 계산할 수 있습니다.")
                order = next((o for o in batch.orders if o["id"] == str(data.get("order_id"))), None)
                if not order:
                    raise DataError("주문을 찾지 못했습니다.")
                edited = edited_order(order, data.get("changes", {}), require_reason=False)
                return JsonResponse({"calculation": calculate(edited, batch.rules)})
            if action == "edit":
                if batch.state != "review":
                    raise DataError("검토 중인 초안만 수정할 수 있습니다.")
                order_id = str(data.get("order_id", ""))
                found = False
                new = []
                for o in batch.orders:
                    if o["id"] == order_id:
                        edited = edited_order(o, data.get("changes", {}))
                        if edited["reviewed"] and not calculate(edited,batch.rules)["ready"]:
                            raise DataError("고객명·입고방식·계산 기준을 확인해 주세요.")
                        new.append(edited)
                        found = True
                    else:
                        new.append(o)
                if not found:
                    raise DataError("주문을 찾지 못했습니다.")
                batch.orders = new
                batch.summary = summarize(new, batch.rules)
                Audit.objects.create(batch=batch, actor=request.user, action="검토 수정", detail={"order_id": order_id, "changes": data["changes"]})
            elif action == "rules":
                if batch.state != "review":
                    raise DataError("검토 중인 초안에서만 규칙을 불러올 수 있습니다.")
                batch.rules = Sheets().rules(f"{batch.bl[:4]}-{batch.bl[4:6]}")
                batch.summary = summarize(batch.orders, batch.rules)
                Audit.objects.create(batch=batch, actor=request.user, action="청구규칙 갱신", detail={"version": batch.rules["version"]})
            elif action == "approve":
                if not request.user.is_staff:
                    return JsonResponse({"error": "최종 승인 권한이 필요합니다."}, status=403)
                if batch.state != "review":
                    raise DataError("검토 중인 초안만 승인할 수 있습니다.")
                batch.summary = summarize(batch.orders, batch.rules)
                if not batch.summary["calculation_ready"] or batch.summary["pending"]:
                    raise DataError("금액을 계산할 수 없는 주문이나 미검토 항목이 남아 있습니다.")
                if any(not o["customer"] for o in batch.orders):
                    raise DataError("고객명이 비어 있는 주문을 확인해 주세요.")
                client = Sheets()
                if client.rules(batch.rules["month"])["version"] != batch.rules["version"]:
                    raise DataError("시트의 단가·환율·청구규칙이 바뀌었습니다. 규칙을 다시 불러와 검토해 주세요.")
                batch.approved_by, batch.approved_at = request.user, timezone.now()
                batch.sheet_plan = client.prepare(batch)
                if batch.sheet_plan.get("already_saved"):
                    raise DataError("이미 시트에 반영한 BL입니다. 검산 결과를 확인해 주세요.")
                batch.state, batch.error = "approved", ""
                Audit.objects.create(batch=batch, actor=request.user, action="최종 승인", detail={"summary": batch.summary, "plan": batch.sheet_plan})
            elif action == "retry":
                if batch.state != "error":
                    raise DataError("오류가 발생한 작업만 재시도할 수 있습니다.")
                if batch.approved_by_id and not request.user.is_staff:
                    return JsonResponse({"error": "승인된 저장 작업은 승인자가 재시도해야 합니다."}, status=403)
                batch.state = "approved" if batch.approved_by_id and batch.sheet_plan else "queued"
                batch.error = ""
                Audit.objects.create(batch=batch, actor=request.user, action="재시도")
            elif action == "refresh":
                if batch.state not in ("review", "error") or not data.get("confirm_discard"):
                    raise DataError("기존 검토 내용을 초기화할지 확인해 주세요.")
                Audit.objects.create(batch=batch, actor=request.user, action="원본 다시 조회", detail={"previous_orders": batch.orders})
                batch.state, batch.source, batch.orders, batch.rules = "queued", {}, [], {}
                batch.progress, batch.total, batch.approved_by, batch.approved_at = 0, 0, None, None
                batch.sheet_plan, batch.error = {}, ""
            else:
                raise DataError("지원하지 않는 작업입니다.")
            batch.revision += 1
            batch.save()
        return JsonResponse(serialized(batch, True))
    except Batch.DoesNotExist:
        return JsonResponse({"error": "조회 이력이 없습니다."}, status=404)
    except (DataError, TypeError, ValueError) as e:
        return JsonResponse({"error": str(e) if isinstance(e, DataError) else "입력값을 확인해 주세요."}, status=400)
