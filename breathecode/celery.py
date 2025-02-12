from __future__ import absolute_import, unicode_literals

# the rest of your Celery file contents go here
import os
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from celery import Celery

# from celery import Celery,
from celery.signals import worker_process_init
from kombu import Exchange, Queue

from breathecode.setup import get_redis_config

# # keeps this adobe
# import newrelic.agent

# newrelic.agent.initialize()


# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "breathecode.settings")

# django.setup()

settings, kwargs, REDIS_URL = get_redis_config()
ENV_KEY = os.getenv("RMQ_URL_KEY", "CLOUDAMQP_URL")
CLOUDAMQP_URL = os.getenv(ENV_KEY, "")

# Decide SSL usage by checking the scheme
if CLOUDAMQP_URL.startswith("amqps://"):
    # Convert 'amqps://' to 'pyamqp://'
    BROKER_URL = CLOUDAMQP_URL.replace("amqps://", "pyamqp://", 1)
    BROKER_USE_SSL = True
else:
    # Convert 'amqp://' to 'pyamqp://'
    BROKER_URL = CLOUDAMQP_URL.replace("amqp://", "pyamqp://", 1)
    BROKER_USE_SSL = False

app = Celery("celery_breathecode", **kwargs)
if os.getenv("ENV") == "test":
    app.conf.update(task_always_eager=True)

if os.getenv("ENV") == "test" or not CLOUDAMQP_URL:
    BROKER_URL = REDIS_URL


if x := os.getenv("RMQ_QUEUE_TYPE"):
    app.conf.task_queues = (
        Queue(
            "celery",
            Exchange("celery"),
            routing_key="celery",
            queue_arguments={"x-queue-type": x},  # Explicitly set Quorum Queue
        ),
    )

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings")


# class NoChannelGlobalQoS(bootsteps.StartStopStep):
#     requires = {"celery.worker.consumer.tasks:Tasks"}

#     def start(self, c):
#         qos_global = False

#         c.connection.default_channel.basic_qos(
#             0,
#             c.initial_prefetch_count,
#             qos_global,
#         )

#         def set_prefetch_count(prefetch_count):
#             return c.task_consumer.qos(
#                 prefetch_count=prefetch_count,
#                 apply_global=qos_global,
#             )


# app.steps["consumer"].add(NoChannelGlobalQoS)

app.conf.update(
    broker_url=BROKER_URL,
    result_backend=REDIS_URL,
    broker_use_ssl=BROKER_USE_SSL,
    namespace="CELERY",
    result_expires=10,
    worker_max_memory_per_child=int(os.getenv("CELERY_MAX_MEMORY_PER_WORKER", "470000")),
    worker_max_tasks_per_child=int(os.getenv("CELERY_MAX_TASKS_PER_WORKER", "1000")),
    #
    worker_disable_rate_limits=True,
    broker_connection_retry_on_startup=True,
    task_acks_late=True,
    worker_prefetch_multiplier=None,
)


# class NoChannelGlobalQoS(bootsteps.StartStopStep):
#     requires = {"celery.worker.consumer.tasks:Tasks"}

#     def start(self, c):
#         qos_global = False

#         c.connection.default_channel.basic_qos(
#             0,
#             c.initial_prefetch_count,
#             qos_global,
#         )

#         def set_prefetch_count(prefetch_count):
#             return c.task_consumer.qos(
#                 prefetch_count=prefetch_count,
#                 apply_global=qos_global,
#             )

#         from kombu.transport.base import QoS

#         c.qos = QoS(set_prefetch_count, c.initial_prefetch_count)


# app.steps["consumer"].add(NoChannelGlobalQoS)

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()
app.conf.broker_transport_options = {
    "priority_steps": list(range(11)),
    "sep": ":",
    "queue_order_strategy": "priority",
    #
    # "confirm_publish": True,
}


class Worker(TypedDict):
    pid: int
    created_at: datetime


Workers = dict[int, list[Worker]]
DataMap = dict[int, bool]


def get_workers_amount():
    dynos = int(os.getenv("CELERY_DYNOS") or 1)
    workers = int(os.getenv("CELERY_MAX_WORKERS") or 1)
    return dynos * workers


if os.getenv("ENV") != "test" and (WORKER := os.getenv("CELERY_POOL", "prefork")) not in ["prefork"]:
    raise ValueError(f'CELERY_POOL must be "prefork" but got {WORKER} that is not supported yet.')


def get_worker_id():
    if WORKER == "gevent":
        from gevent import getcurrent

        return id(getcurrent())
    return os.getpid()


@worker_process_init.connect
def worker_process_init_handler(**kwargs):
    from django.core.cache import cache
    from django_redis import get_redis_connection
    from redis.exceptions import LockError

    is_django_redis = hasattr(cache, "delete_pattern")
    if is_django_redis:
        from redis.lock import Lock

    else:

        class Lock:

            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                pass

            def __exit__(self, *args, **kwargs):
                pass

    worker_id = get_worker_id()
    print(f"Worker process initialized with id: {worker_id}")

    client = None
    if is_django_redis:

        client = get_redis_connection("default")

    workers = get_workers_amount()
    delta = timedelta(minutes=2)

    # save the workers pid in cache for know its worker number
    while True:
        try:
            with Lock(client, "lock:workers", timeout=3, blocking_timeout=3):
                data: Workers = cache.get("workers")
                available: DataMap = {}
                if data is None:
                    data = {}

                for i in range(workers):
                    if i not in data:
                        data[i] = []

                    if len(data[i]) >= 2:
                        data[i].sort(key=lambda x: x["created_at"].replace(tzinfo=UTC))

                        if datetime.now(UTC) - data[i][-1]["created_at"].replace(tzinfo=UTC) < delta:
                            available[i] = False
                            data[i] = data[i][-2:]
                        else:
                            available[i] = True
                            data[i] = data[i][-1:]

                    elif len(data[i]) == 1:
                        if datetime.now(UTC) - data[i][0]["created_at"].replace(tzinfo=UTC) < delta:
                            available[i] = False
                        else:
                            available[i] = True

                    else:
                        available[i] = True

                found = False
                for i in range(workers):
                    if available[i]:
                        data[i].append({"pid": worker_id, "created_at": datetime.now(UTC)})
                        found = True
                        break

                if not found:
                    pointer = data[0]

                    for i in range(1, workers):
                        if len(data[i]) < len(pointer):
                            pointer = data[i]

                    pointer.append({"pid": worker_id, "created_at": datetime.now(UTC)})

                cache.set("workers", data, timeout=None)
                break

        except LockError:
            ...


app.conf.task_default_priority = 5  # Default priority value
