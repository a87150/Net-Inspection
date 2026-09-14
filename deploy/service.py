"""One production process per role, with a shared environment and rotating logs."""
import argparse
from contextlib import contextmanager
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from deploy.setup import ROOT, load_config, listen_address


@contextmanager
def role_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError('This project role is already running; refusing a duplicate process.') from None
    try:
        yield
    finally:
        stream.close()


class LogStream:
    def __init__(self, logger, level):
        self.logger, self.level = logger, level
    def write(self, value):
        for line in value.rstrip().splitlines():
            self.logger.log(self.level, line)
        return len(value)
    def flush(self):
        pass
    def isatty(self):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=['web', 'worker'])
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    args = parser.parse_args()
    os.chdir(ROOT)
    # Configure logs before loading keys/DB credentials, so startup failures are visible.
    runtime = ROOT / 'runtime' / 'deployment'
    (runtime / 'logs').mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(runtime / 'logs' / f'{args.role}.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger = logging.getLogger('deployment.' + args.role)
    logger.setLevel(logging.INFO); logger.addHandler(handler); logger.propagate = False
    sys.stdout, sys.stderr = LogStream(logger, logging.INFO), LogStream(logger, logging.ERROR)
    try:
        with role_lock(runtime / f'{args.role}.lock'):
            load_config(args.env_file.resolve())
            import django
            django.setup()
            logging.getLogger().addHandler(handler)
            if args.role == 'web':
                from waitress import serve
                from net.wsgi import application
                host, port = listen_address()
                print(f'Web starting on {host}:{port}; environment: {args.env_file.resolve()}')
                serve(application, host=host, port=port, threads=int(os.environ.get('WEB_THREADS', 4)))
            else:
                from django.core.management import call_command
                call_command('run_task_worker', threads=int(os.environ.get('WORKER_THREADS', 4)),
                             poll_seconds=int(os.environ.get('WORKER_POLL_SECONDS', 5)),
                             lease_seconds=int(os.environ.get('WORKER_LEASE_SECONDS', 60)))
    except Exception as exc:
        # Do not serialize the environment or connection objects into error logs.
        logger.error('Service stopped (%s): %s', type(exc).__name__, str(exc) if isinstance(exc, (ValueError, RuntimeError)) else 'Check configuration, database access and file permissions.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
