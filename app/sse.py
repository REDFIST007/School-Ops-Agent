import asyncio
import json
from collections import defaultdict
from typing import Any, AsyncIterator, Dict, Set


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)

    async def subscribe(self, school_id: str) -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers[school_id].add(queue)
        try:
            yield f"data: {json.dumps({'type': 'connected', 'school_id': school_id})}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self._subscribers[school_id].discard(queue)

    def publish_sync(self, school_id: str, event_type: str, data: Dict[str, Any]) -> None:
        payload = {"type": event_type, "school_id": school_id, "data": data}
        queues = list(self._subscribers.get(school_id, set()))
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


broker = EventBroker()
