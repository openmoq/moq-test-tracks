# Copyright (c) OpenMOQ contributors.
# This source code is licensed under the Apache 2.0 license found in the
# LICENSE file in the root directory of this source tree.

# A MoQT subscriber that records how each track arrives and how it ends.

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field

from aiomoqt.client import MOQTClient
from aiomoqt.utils.url import parse_relay_url

NORMAL, END_OF_GROUP, END_OF_TRACK = 0, 3, 4


@dataclass
class Track:
    name: str
    reply: str = ""
    groups: Counter = field(default_factory=Counter)  # normal objects per group
    datagrams: int = 0  # normal objects that came as datagrams
    first_payload: bytes = b""
    end_of_groups: int = 0
    end_of_track: tuple[int, int] | None = None  # (group, object)
    done_code: int | None = None
    after_done: int = 0

    @property
    def objects(self) -> int:
        return sum(self.groups.values())

    def complete_groups(self) -> list[int]:
        """Object counts of the groups seen whole: neither the first, which the
        subscription may join mid-way, nor the last, which ends the track."""
        keys = sorted(self.groups)
        return [self.groups[g] for g in keys[1:-1]]


async def subscribe(
    url: str, namespace: str, names: list[str], *, draft: int, timeout: float, linger: float = 2.0
) -> dict[str, Track]:
    """Subscribe to each track, then wait for PUBLISH_DONE on all of them
    (or `timeout`), and `linger` more for objects that follow it."""
    r = parse_relay_url(url)
    client = MOQTClient(r.host, r.port, path=r.path or "", use_quic=r.use_quic,
                        supported_drafts=[draft])  # fmt: skip
    tracks = {n: Track(n) for n in names}
    finished = asyncio.Event()

    def on_object(t: Track):
        def cb(msg, size, recv_us, group_id, subgroup_id):
            group = group_id if group_id is not None else getattr(msg, "group_id", None)
            status = int(getattr(msg, "status", NORMAL) or NORMAL)
            if t.done_code is not None:
                t.after_done += 1
            if status == NORMAL:
                t.groups[group] += 1
                t.datagrams += subgroup_id is None
                if not t.first_payload:
                    t.first_payload = bytes(getattr(msg, "payload", b""))
            elif status == END_OF_GROUP:
                t.end_of_groups += 1
            elif status == END_OF_TRACK:
                t.end_of_track = (group, msg.object_id)

        return cb

    def on_done(t: Track):
        def cb(msg):
            t.done_code = int(msg.status_code)
            if all(x.done_code is not None for x in tracks.values()):
                finished.set()

        return cb

    async with client.connect() as session:
        await session.client_session_init()
        for t in tracks.values():
            reply = await session.subscribe(namespace=namespace, track_name=t.name,
                                            wait_response=True, on_object=on_object(t),
                                            on_done=on_done(t))  # fmt: skip
            t.reply = type(reply).__name__
        try:
            await asyncio.wait_for(finished.wait(), timeout)
        except TimeoutError:
            pass
        await asyncio.sleep(linger)
        session.close()
    return tracks
