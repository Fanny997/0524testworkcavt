from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from rq import Queue
from rq.job import Job
from rq.registry import ScheduledJobRegistry


class Scheduler:
    def __init__(
        self,
        queue_name: str,
        connection: Any,
        interval: int = 60,
        queue: Queue | None = None,
        job_class: type[Job] | str | None = None,
    ) -> None:
        if queue is None:
            queue = Queue(
                queue_name,
                connection=connection,
                job_class=job_class,
            )

        self.queue_name = queue_name
        self.interval = interval
        self.queue = queue
        self.connection = connection or queue.connection
        self.job_class = queue.job_class
        self._registry = ScheduledJobRegistry(
            queue_name,
            connection=self.connection,
            job_class=self.job_class,
        )

    def enqueue_at(self, when: datetime, func, *args, **kwargs):
        return self.queue.enqueue_at(when, func, *args, **kwargs)

    def enqueue_in(self, time_delta: timedelta, func, *args, **kwargs):
        return self.queue.enqueue_in(time_delta, func, *args, **kwargs)

    def get_jobs(self):
        job_ids = self._registry.get_job_ids()
        jobs = self.job_class.fetch_many(job_ids, connection=self.connection)
        return [job for job in jobs if job]

    def cancel(self, job, delete_job: bool = False):
        return self._registry.remove(job, delete_job=delete_job)

