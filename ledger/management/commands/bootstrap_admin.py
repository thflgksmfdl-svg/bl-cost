import os
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "환경변수로 최초 관리자만 생성하고 기존 비밀번호를 덮어쓰지 않습니다."

    def handle(self, *args, **options):
        users = get_user_model()
        if users.objects.filter(is_superuser=True).exists():
            return
        username, password = os.getenv("BL_COST_ADMIN_USERNAME", "admin"), os.getenv("BL_COST_ADMIN_PASSWORD", "")
        if not password:
            raise CommandError("BL_COST_ADMIN_PASSWORD 환경변수에 최초 관리자 비밀번호가 필요합니다.")
        validate_password(password)
        users.objects.create_superuser(username=username, password=password)
        self.stdout.write("최초 관리자를 생성했습니다.")
