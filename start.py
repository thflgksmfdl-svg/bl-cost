import os
import signal
import subprocess
import sys
import time

if os.getenv("RENDER") == "true":
    for name in ("DATABASE_URL", "SECRET_KEY"):
        if not os.getenv(name):
            sys.exit(f"필수 환경변수를 설정해 주세요: {name}")
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as db:
        db.execute("CREATE SCHEMA IF NOT EXISTS bl_cost")

for args in (["migrate", "--noinput"], ["bootstrap_admin"]):
    subprocess.run([sys.executable, "manage.py", *args], check=True)

processes = [subprocess.Popen([sys.executable, "manage.py", "runworker"]),
             subprocess.Popen([sys.executable, "-m", "gunicorn", "config.wsgi:application", "--bind",
                               f"0.0.0.0:{os.getenv('PORT', '8000')}", "--workers", "2", "--threads", "2", "--timeout", "90"])]


def stop(*args):
    for p in processes:
        if p.poll() is None:
            p.terminate()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while all(p.poll() is None for p in processes):
    time.sleep(1)
stop()
for p in processes:
    try:
        p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
sys.exit(next((p.returncode for p in processes if p.returncode not in (None, 0)), 0))
