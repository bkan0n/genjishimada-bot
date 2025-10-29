from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from genjipk_sdk.models import JobStatus

    from extensions.api_client import APIClient


def time_convert(string: str) -> float:
    """Convert HH:MM:SS.ss string into seconds (float)."""
    negative = -1 if string[0] == "-" else 1
    time = string.split(":")
    match len(time):
        case 1:
            res = float(time[0])
        case 2:
            res = float((int(time[0]) * 60) + (negative * float(time[1])))
        case 3:
            res = float((int(time[0]) * 3600) + (negative * (int(time[1]) * 60)) + (negative * float(time[2])))
        case _:
            raise ValueError("Failed to match any cases.")
    return round(res, 2)


async def poll_job_until_complete(api: APIClient, job_id: uuid.UUID) -> JobStatus | None:
    """Poll a job from the API every 100 ms with exponential backoff up to 20 seconds.

    Args:
        api: APIClient.
        job_id (uuid.UUID): The ID of the job to monitor.

    Returns:
        JobStatus | None: The final job status if completed in time, otherwise None.
    """
    interval = 0.1  # start at 100 ms
    max_duration = 20.0  # seconds
    start_time = time.monotonic()

    in_progress = {"queued", "processing"}

    while True:
        job = await api.get_job(job_id)

        if job.status not in in_progress:
            return job

        if time.monotonic() - start_time >= max_duration:
            return job

        await asyncio.sleep(interval)
        interval = min(interval * 2, 5.0)
