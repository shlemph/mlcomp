import time
from mlcomp.utils.logging import logger, logging
import traceback
from mlcomp.task.tasks import execute
from mlcomp.db.providers import *
from apscheduler.schedulers.background import BackgroundScheduler
import atexit


def supervisor():
    provider = TaskProvider()
    computer_provider = ComputerProvider()

    try:
        time.sleep(1)
        not_ran_tasks = provider.by_status(TaskStatus.NotRan)
        logger.info(f'Found {len(not_ran_tasks)} not ran tasks')

        dep_status = provider.dependency_status(not_ran_tasks)
        computers = computer_provider.computers()
        for task in provider.by_status(TaskStatus.InProgress):
            assigned = task.computer_assigned
            computers[assigned]['cpu'] -= task.cpu
            computers[assigned]['gpu'] -= task.gpu
            computers[assigned]['memory'] -= task.memory

        for task in not_ran_tasks:
            if TaskStatus.Stopped.value in dep_status[task.id] or TaskStatus.Failed.value in dep_status[task.id]:
                provider.change_status(task, TaskStatus.Skipped)
                continue

            status_set = set(dep_status[task.id])
            if len(status_set) != 0 and status_set != {TaskStatus.Success.value}:
                continue

            for name, computer in computers.items():
                if task.gpu > computer['gpu'] or task.cpu > computer['cpu'] or task.memory > computer['memory']:
                    continue
                if task.computer is not None and task.computer != computer.name:
                    continue

                r = execute.apply_async((task.id,), queue=computer['name'])
                task.status = TaskStatus.Queued.value
                task.computer_assigned = computer['name']
                task.celery_id = r.id

                provider.session.update()

                computer['gpu'] -= task.gpu
                computer['cpu'] -= task.cpu
                computer['memory'] -= task.memory
                break

    except Exception:
        logger.error(traceback.format_exc())


def register_supervisor():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=supervisor, trigger="interval", seconds=1)
    scheduler.start()

    # Shut down the scheduler when exiting the app
    atexit.register(lambda: scheduler.shutdown())

    class NoRunningFilter(logging.Filter):
        def filter(self, record):
            return isinstance(record.msg, str) and not 'ran tasks' in record.msg

    for k in logging.root.manager.loggerDict:
        if 'apscheduler' in k:
            logging.getLogger(k).setLevel(logging.ERROR)
        if 'mlcomp' in k:
            logging.getLogger(k).addFilter(NoRunningFilter())