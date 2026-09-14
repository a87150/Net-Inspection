"""Shared Web/Worker local environment; process settings always take precedence."""
import os
from pathlib import Path
from dotenv import load_dotenv


def load_environment():
    root = Path(__file__).resolve().parents[2]
    path = os.getenv('NET_ENV_FILE') or str(root / '.env')
    load_dotenv(path, override=False)
    if not os.getenv('DB_PASSWORD') and os.getenv('DB_PASSWORD_KEYRING_SERVICE'):
        import keyring
        password = keyring.get_password(os.environ['DB_PASSWORD_KEYRING_SERVICE'], os.getenv('DB_USER', ''))
        if not password:
            raise RuntimeError('Database credential not found in OS credential store for this service account.')
        os.environ['DB_PASSWORD'] = password
    backup_service = os.getenv('DEVICE_BACKUP_KEYRING_SERVICE')
    if backup_service and not os.getenv('DEVICE_BACKUP_ENCRYPTION_KEY'):
        import keyring
        key = keyring.get_password(backup_service, 'device-backups')
        if not key:
            raise RuntimeError('Device backup key not found in OS credential store for this service account.')
        os.environ['DEVICE_BACKUP_ENCRYPTION_KEY'] = key
    for name in ('PC_LOG_SOURCE_ENCRYPTION_KEY', 'DOMAIN_OPERATION_ENCRYPTION_KEY',
                 'DEVICE_BACKUP_ENCRYPTION_KEY'):
        filename = os.getenv(name + '_FILE')
        if filename and not os.getenv(name):
            key_path = Path(filename)
            if not key_path.is_absolute():
                key_path = root / key_path
            os.environ[name] = key_path.read_text(encoding='ascii').strip()
