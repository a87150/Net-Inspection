# Legacy manual-deployment reference. The one-click installer (Install-NetInspection.ps1)
# generates these values for you via `deploy.setup configure`; prefer it over editing by hand.
#
# DJANGO_SECRET_KEY must be 32+ characters and must NOT contain: change-me, change_me,
# replace-with, django-insecure, demo-only. Generate with:
#     python -c "import secrets; print(secrets.token_urlsafe(64))"
$env:DJANGO_SETTINGS_MODULE = 'net.settings'
$env:DJANGO_DEBUG = 'false'
$env:DJANGO_SECRET_KEY = 'CHANGE_ME_RANDOM_SECRET'
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1,inspection.example.invalid'
$env:DJANGO_STATIC_ROOT = 'C:\NetInspectionData\staticfiles'
$env:DB_ENGINE = 'mysql'
$env:DB_NAME = 'net_inspection'
$env:DB_USER = 'net_inspection'
$env:DB_PASSWORD = 'CHANGE_ME_DATABASE_PASSWORD'
$env:DB_HOST = '127.0.0.1'
$env:DB_PORT = '3306'
$env:WEB_LISTEN = '127.0.0.1:8000'
$env:WEB_THREADS = '4'
$env:WORKER_THREADS = '4'
$env:WORKER_POLL_SECONDS = '5'
$env:WORKER_LEASE_SECONDS = '60'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONUTF8 = '1'

# Three independent Fernet keys (32 random bytes, base64url, 44 chars). All three are required
# even before you import data. Generate each with:
#     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# NEVER regenerate these for an existing database: encrypted rows become unreadable.
$env:DOMAIN_OPERATION_ENCRYPTION_KEY = 'CHANGE_ME_44_CHAR_FERNET_KEY_1_'
$env:PC_LOG_SOURCE_ENCRYPTION_KEY = 'CHANGE_ME_44_CHAR_FERNET_KEY_2_'
$env:DEVICE_BACKUP_ENCRYPTION_KEY = 'CHANGE_ME_44_CHAR_FERNET_KEY_3_'
