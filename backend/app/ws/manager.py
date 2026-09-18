from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    """In-process WebSocket registry.

    room_access_token -> session_id -> set[WebSocket]
    Broadcasting iterates locally-held sockets. For multi-instance deployments,
    swap this for a Redis pub/sub distribution (see plan §11.1) — the public
    interface below is the seam where that would plug in.
    """

    def __init__(self) -> None:
        self._rooms: dict[str, dict[int, set[WebSocket]]] = defaultdict(
            lambda: defaultdict(set)
        )

    async def connect(self, room_access_token: str, session_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self._rooms[room_access_token][session_id].add(ws)

    def disconnect(self, room_access_token: str, session_id: int, ws: WebSocket) -> None:
        sockets = self._rooms[room_access_token].get(session_id)
        if sockets is not None:
            sockets.discard(ws)
            if not sockets:
                del self._rooms[room_access_token][session_id]
        if not self._rooms[room_access_token]:
            del self._rooms[room_access_token]

    def session_connection_count(self, room_access_token: str, session_id: int) -> int:
        return len(self._rooms[room_access_token].get(session_id, set()))

    def is_session_connected(self, room_access_token: str, session_id: int) -> bool:
        return bool(self._rooms[room_access_token].get(session_id))

    async def broadcast(
        self,
        room_access_token: str,
        event: dict[str, Any],
        exclude_session_ids: set[int] | None = None,
    ) -> None:
        exclude = exclude_session_ids or set()
        for session_id, sockets in list(self._rooms[room_access_token].items()):
            if session_id in exclude:
                continue
            for ws in list(sockets):
                try:
                    await ws.send_json(event)
                except Exception:
                    # Sockets in a broken state are cleaned up on disconnect.
                    continue

    def active_sockets_count(self, room_access_token: str) -> int:
        return sum(len(s) for s in self._rooms[room_access_token].values())


manager = ConnectionManager()