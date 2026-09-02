"""Run the lease-based infrastructure task Worker."""

from django.core.management.base import BaseCommand, CommandError
from contextlib import contextmanager
import signal
from threading import Event, current_thread, main_thread

from net.inspections.worker import TaskWorker


@contextmanager
def stop_signals(stop_event):
    previous = {}
    try:
        if current_thread() is main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, lambda *_: stop_event.set())
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


class Command(BaseCommand):
    help = '运行数据库任务队列 Worker（支持 Windows Server 与 Linux）'

    def add_arguments(self, parser):
        parser.add_argument('--threads', type=int, default=4, help='全局最大并发数')
        parser.add_argument('--poll-seconds', type=float, default=5, help='空闲轮询秒数')
        parser.add_argument('--lease-seconds', type=int, default=60, help='任务租约秒数')
        parser.add_argument('--once', action='store_true', help='仅处理一次后退出')

    def handle(self, *args, **options):
        try:
            worker = TaskWorker(
                threads=options['threads'],
                poll_seconds=options['poll_seconds'],
                lease_seconds=options['lease_seconds'],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        stop_event = Event()
        with stop_signals(stop_event):
            try:
                if options['once']:
                    handled = worker.run_once(stop_event)
                    if stop_event.is_set():
                        self.stdout.write('Worker 已停止；未完成目标等待租约恢复。')
                    elif handled:
                        self.stdout.write(self.style.SUCCESS('已处理一项巡检任务。'))
                    else:
                        self.stdout.write('没有可执行任务。')
                    return
                self.stdout.write(f'Worker 已启动：{worker.worker_id}')
                worker.run_forever(stop_event)
            except KeyboardInterrupt:
                stop_event.set()
            self.stdout.write('Worker 已停止。')
