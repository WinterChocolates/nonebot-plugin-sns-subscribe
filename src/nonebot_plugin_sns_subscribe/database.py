"""SQLite 仓储层：所有表都带 ``platform`` 维度，B 站与抖音共用一套订阅。"""

from __future__ import annotations

import json
import time
from typing import Any
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import aiosqlite

from .paths import DATABASE_PATH, ensure_data_dirs
from .rules import validate_regex, normalize_atall_switches
from .models import Platform, AtAllKind, Subscription, AuthorProfile

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS groups (
    group_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS authors (
    platform TEXT NOT NULL,
    author_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (platform, author_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    platform TEXT NOT NULL,
    group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    PRIMARY KEY (platform, group_id, author_id)
);

CREATE TABLE IF NOT EXISTS filters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    pattern TEXT NOT NULL,
    UNIQUE (group_id, pattern)
);

CREATE TABLE IF NOT EXISTS atall (
    platform TEXT NOT NULL,
    group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    author_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (platform, group_id, author_id, kind)
);

CREATE TABLE IF NOT EXISTS overrides (
    platform TEXT NOT NULL,
    group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    template_name TEXT,
    font_name TEXT,
    color TEXT,
    gradient_color TEXT,
    PRIMARY KEY (platform, group_id, kind)
);

CREATE TABLE IF NOT EXISTS states (
    platform TEXT NOT NULL,
    namespace TEXT NOT NULL,
    state_key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (platform, namespace, state_key)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_receipts (
    platform TEXT NOT NULL,
    namespace TEXT NOT NULL,
    author_id TEXT NOT NULL,
    content_key TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    delivered_at INTEGER NOT NULL,
    PRIMARY KEY (platform, namespace, author_id, content_key, group_id)
);

CREATE TABLE IF NOT EXISTS delivery_claims (
    platform TEXT NOT NULL,
    namespace TEXT NOT NULL,
    author_id TEXT NOT NULL,
    content_key TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    claimed_at INTEGER NOT NULL,
    PRIMARY KEY (platform, namespace, author_id, content_key, group_id)
);
"""

DELIVERY_CLAIM_TTL = 300


class Repository:
    """插件唯一的数据库入口。"""

    def __init__(self, path: Any = DATABASE_PATH) -> None:
        self.path = path

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        ensure_data_dirs()
        async with self._connect() as db:
            await db.executescript(SCHEMA)
            await db.commit()

    # ------------------------------------------------------------------ groups
    async def upsert_group(
        self, group_id: int, name: str = "", avatar: str = ""
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO groups(group_id, name, avatar, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    name=CASE WHEN excluded.name='' THEN groups.name
                              ELSE excluded.name END,
                    avatar=CASE WHEN excluded.avatar='' THEN groups.avatar
                                ELSE excluded.avatar END""",
                (group_id, name, avatar, int(time.time())),
            )
            await db.commit()

    # ----------------------------------------------------------------- authors
    async def upsert_author(self, profile: AuthorProfile) -> None:
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO authors(
                    platform, author_id, name, avatar, url, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, author_id) DO UPDATE SET
                    name=excluded.name,
                    avatar=excluded.avatar,
                    url=excluded.url,
                    updated_at=excluded.updated_at""",
                (
                    profile.platform.value,
                    profile.author_id,
                    profile.name,
                    profile.avatar,
                    profile.url,
                    int(time.time()),
                ),
            )
            await db.commit()

    async def get_author(
        self, platform: Platform, author_id: str
    ) -> dict[str, Any] | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM authors WHERE platform=? AND author_id=?",
                    (platform.value, author_id),
                )
            ).fetchone()
        return dict(row) if row else None

    # ----------------------------------------------------------- subscriptions
    async def add_subscription(
        self,
        profile: AuthorProfile,
        group_id: int,
        group_name: str = "",
    ) -> bool:
        await self.upsert_group(
            group_id,
            group_name,
            f"https://p.qlogo.cn/gh/{group_id}/{group_id}/100",
        )
        await self.upsert_author(profile)
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO subscriptions(
                    platform, group_id, author_id, author_name, avatar, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    profile.platform.value,
                    group_id,
                    profile.author_id,
                    profile.name,
                    profile.avatar,
                    int(time.time()),
                ),
            )
            if cursor.rowcount > 0:
                await db.execute(
                    """UPDATE subscriptions SET author_name=?, avatar=?
                    WHERE platform=? AND group_id=? AND author_id=?""",
                    (
                        profile.name,
                        profile.avatar,
                        profile.platform.value,
                        group_id,
                        profile.author_id,
                    ),
                )
            await db.commit()
            return cursor.rowcount > 0

    async def refresh_subscription_profile(self, profile: AuthorProfile) -> None:
        await self.upsert_author(profile)
        async with self._connect() as db:
            await db.execute(
                """UPDATE subscriptions SET author_name=?, avatar=?
                WHERE platform=? AND author_id=?""",
                (
                    profile.name,
                    profile.avatar,
                    profile.platform.value,
                    profile.author_id,
                ),
            )
            await db.commit()

    async def remove_subscription(
        self, platform: Platform, group_id: int, author_id: str
    ) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                """DELETE FROM subscriptions
                WHERE platform=? AND group_id=? AND author_id=?""",
                (platform.value, group_id, author_id),
            )
            await db.execute(
                """DELETE FROM atall
                WHERE platform=? AND group_id=? AND author_id=?""",
                (platform.value, group_id, author_id),
            )
            await self._clear_orphan_state(db, platform, author_id)
            await db.commit()
            return cursor.rowcount > 0

    async def clear_group(self, group_id: int) -> int:
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    "SELECT platform, author_id FROM subscriptions WHERE group_id=?",
                    (group_id,),
                )
            ).fetchall()
            cursor = await db.execute(
                "DELETE FROM subscriptions WHERE group_id=?", (group_id,)
            )
            await db.execute("DELETE FROM atall WHERE group_id=?", (group_id,))
            await db.execute("DELETE FROM overrides WHERE group_id=?", (group_id,))
            for row in rows:
                await self._clear_orphan_state(
                    db, Platform(str(row["platform"])), str(row["author_id"])
                )
            await db.commit()
            return cursor.rowcount

    async def clear_all(self) -> int:
        async with self._connect() as db:
            cursor = await db.execute("DELETE FROM subscriptions")
            await db.execute("DELETE FROM atall")
            await db.execute("DELETE FROM overrides")
            await db.execute("DELETE FROM states")
            await db.execute("DELETE FROM delivery_receipts")
            await db.execute("DELETE FROM delivery_claims")
            await db.commit()
            return cursor.rowcount

    @staticmethod
    async def _clear_orphan_state(
        db: aiosqlite.Connection, platform: Platform, author_id: str
    ) -> None:
        remaining = await (
            await db.execute(
                """SELECT 1 FROM subscriptions
                WHERE platform=? AND author_id=? LIMIT 1""",
                (platform.value, author_id),
            )
        ).fetchone()
        if remaining is not None:
            return
        await db.execute(
            "DELETE FROM states WHERE platform=? AND state_key=?",
            (platform.value, author_id),
        )
        await db.execute(
            """DELETE FROM delivery_receipts
            WHERE platform=? AND author_id=?""",
            (platform.value, author_id),
        )
        await db.execute(
            """DELETE FROM delivery_claims
            WHERE platform=? AND author_id=?""",
            (platform.value, author_id),
        )

    async def list_subscriptions(
        self, group_id: int | None = None, platform: Platform | None = None
    ) -> list[Subscription]:
        query = """SELECT s.platform, s.group_id, s.author_id, s.author_name, s.avatar,
                   g.name AS group_name, g.avatar AS group_avatar
                   FROM subscriptions s JOIN groups g USING(group_id)"""
        clauses: list[str] = []
        params: list[Any] = []
        if group_id is not None:
            clauses.append("s.group_id=?")
            params.append(group_id)
        if platform is not None:
            clauses.append("s.platform=?")
            params.append(platform.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY s.platform, s.group_id, s.author_id"
        async with self._connect() as db:
            rows = await (await db.execute(query, tuple(params))).fetchall()
        return [self._row_to_subscription(row) for row in rows]

    @staticmethod
    def _row_to_subscription(row: aiosqlite.Row) -> Subscription:
        return Subscription(
            platform=Platform(str(row["platform"])),
            group_id=int(row["group_id"]),
            author_id=str(row["author_id"]),
            author_name=str(row["author_name"]),
            avatar=str(row["avatar"]),
            group_name=str(row["group_name"]),
            group_avatar=str(row["group_avatar"]),
        )

    async def list_tracked(self, platform: Platform) -> list[Subscription]:
        """去重后的作者列表（用于轮询）。"""

        subscriptions = await self.list_subscriptions(platform=platform)
        seen: set[str] = set()
        unique: list[Subscription] = []
        for item in subscriptions:
            if item.author_id in seen:
                continue
            seen.add(item.author_id)
            unique.append(item)
        return unique

    async def groups_for(
        self, platform: Platform, author_id: str
    ) -> list[Subscription]:
        rows = await self.list_subscriptions(platform=platform)
        return [item for item in rows if item.author_id == author_id]

    # ----------------------------------------------------------------- filters
    async def add_filter(self, group_id: int, pattern: str) -> bool:
        validate_regex(pattern)
        await self.upsert_group(group_id)
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO filters(group_id, pattern) VALUES (?, ?)",
                (group_id, pattern),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_filters(self, group_id: int) -> list[tuple[int, str]]:
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    "SELECT id, pattern FROM filters WHERE group_id=? ORDER BY id",
                    (group_id,),
                )
            ).fetchall()
        return [(int(row["id"]), str(row["pattern"])) for row in rows]

    async def remove_filter(self, group_id: int, selector: str) -> bool:
        async with self._connect() as db:
            if selector.isdigit():
                rows = await (
                    await db.execute(
                        "SELECT id FROM filters WHERE group_id=? ORDER BY id",
                        (group_id,),
                    )
                ).fetchall()
                index = int(selector) - 1
                if index < 0 or index >= len(rows):
                    return False
                cursor = await db.execute(
                    "DELETE FROM filters WHERE group_id=? AND id=?",
                    (group_id, int(rows[index]["id"])),
                )
            else:
                cursor = await db.execute(
                    "DELETE FROM filters WHERE group_id=? AND pattern=?",
                    (group_id, selector),
                )
            await db.commit()
            return cursor.rowcount > 0

    async def remove_filter_exact(self, group_id: int, pattern: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM filters WHERE group_id=? AND pattern=?",
                (group_id, pattern),
            )
            await db.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------- atall
    async def set_atall(
        self,
        platform: Platform,
        group_id: int,
        author_id: str,
        kind: AtAllKind,
        *,
        enabled: bool,
    ) -> set[str]:
        await self.upsert_group(group_id)
        current = await self.get_atall(platform, group_id, author_id)
        updated = normalize_atall_switches(current, kind, enabled=enabled)
        async with self._connect() as db:
            await db.execute(
                """DELETE FROM atall
                WHERE platform=? AND group_id=? AND author_id=?""",
                (platform.value, group_id, author_id),
            )
            await db.executemany(
                """INSERT INTO atall(platform, group_id, author_id, kind)
                VALUES (?, ?, ?, ?)""",
                [
                    (platform.value, group_id, author_id, item)
                    for item in sorted(updated)
                ],
            )
            await db.commit()
        return updated

    async def get_atall(
        self, platform: Platform, group_id: int, author_id: str
    ) -> set[str]:
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    """SELECT kind FROM atall
                    WHERE platform=? AND group_id=? AND author_id=?""",
                    (platform.value, group_id, author_id),
                )
            ).fetchall()
        return {str(row[0]) for row in rows}

    async def list_atall(self, group_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    """SELECT platform, author_id, kind FROM atall
                    WHERE group_id=? ORDER BY platform, author_id, kind""",
                    (group_id,),
                )
            ).fetchall()
        return [dict(row) for row in rows]

    # --------------------------------------------------------------- overrides
    async def set_override(
        self,
        platform: Platform,
        group_id: int,
        kind: str,
        template_name: str | None = None,
        font_name: str | None = None,
        color: str | None = None,
        gradient_color: str | None = None,
    ) -> None:
        await self.upsert_group(group_id)
        async with self._connect() as db:
            if not any((template_name, font_name, color, gradient_color)):
                await db.execute(
                    """DELETE FROM overrides
                    WHERE platform=? AND group_id=? AND kind=?""",
                    (platform.value, group_id, kind),
                )
            else:
                await db.execute(
                    """INSERT INTO overrides(
                        platform, group_id, kind, template_name, font_name,
                        color, gradient_color
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform, group_id, kind) DO UPDATE SET
                        template_name=excluded.template_name,
                        font_name=excluded.font_name,
                        color=excluded.color,
                        gradient_color=excluded.gradient_color""",
                    (
                        platform.value,
                        group_id,
                        kind,
                        template_name,
                        font_name,
                        color,
                        gradient_color,
                    ),
                )
            await db.commit()

    async def resolve_override(
        self, platform: Platform, group_id: int, kind: str
    ) -> tuple[str | None, str | None, str | None, str | None]:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    """SELECT template_name, font_name, color, gradient_color
                    FROM overrides
                    WHERE platform=? AND group_id=? AND kind=?""",
                    (platform.value, group_id, kind),
                )
            ).fetchone()
        if not row:
            return None, None, None, None
        return (
            row["template_name"],
            row["font_name"],
            row["color"],
            row["gradient_color"],
        )

    async def list_overrides(self, group_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    """SELECT platform, kind, template_name, font_name, color,
                    gradient_color FROM overrides
                    WHERE group_id=? ORDER BY platform, kind""",
                    (group_id,),
                )
            ).fetchall()
        return [dict(row) for row in rows]

    async def clear_template_references(
        self, platform: Platform, kind: str, name: str
    ) -> None:
        """删除模板文件时同时解除渲染方案中的引用。"""

        async with self._connect() as db:
            await db.execute(
                """UPDATE overrides SET template_name=NULL
                WHERE platform=? AND kind=? AND template_name=?""",
                (platform.value, kind, name),
            )
            await db.execute(
                """DELETE FROM overrides
                WHERE platform=? AND kind=? AND template_name IS NULL
                  AND font_name IS NULL AND color IS NULL
                  AND gradient_color IS NULL""",
                (platform.value, kind),
            )
            await db.commit()

    async def clear_font_references(self, name: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE overrides SET font_name=NULL WHERE font_name=?", (name,)
            )
            await db.execute(
                """DELETE FROM overrides
                WHERE template_name IS NULL AND font_name IS NULL
                  AND color IS NULL AND gradient_color IS NULL"""
            )
            await db.commit()

    # ------------------------------------------------------------------ states
    async def get_state(
        self, platform: Platform, namespace: str, state_key: str
    ) -> str | None:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    """SELECT value FROM states
                    WHERE platform=? AND namespace=? AND state_key=?""",
                    (platform.value, namespace, state_key),
                )
            ).fetchone()
        return str(row[0]) if row else None

    async def set_state(
        self, platform: Platform, namespace: str, state_key: str, value: str
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO states(platform, namespace, state_key, value, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, namespace, state_key) DO UPDATE SET
                    value=excluded.value, updated_at=excluded.updated_at""",
                (platform.value, namespace, state_key, value, int(time.time())),
            )
            await db.commit()

    async def get_json_state(
        self, platform: Platform, namespace: str, state_key: str
    ) -> Any | None:
        raw = await self.get_state(platform, namespace, state_key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set_json_state(
        self, platform: Platform, namespace: str, state_key: str, value: Any
    ) -> None:
        await self.set_state(
            platform,
            namespace,
            state_key,
            json.dumps(value, ensure_ascii=False),
        )

    # ---------------------------------------------------------------- settings
    async def get_setting(self, key: str, default: str = "") -> str:
        async with self._connect() as db:
            row = await (
                await db.execute("SELECT value FROM settings WHERE key=?", (key,))
            ).fetchone()
        return str(row[0]) if row else default

    async def set_setting(self, key: str, value: str) -> None:
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, int(time.time())),
            )
            await db.commit()

    # --------------------------------------------------------------- deliveries
    async def has_delivery_receipt(
        self,
        platform: Platform,
        namespace: str,
        author_id: str,
        content_key: str,
        group_id: int,
    ) -> bool:
        async with self._connect() as db:
            row = await (
                await db.execute(
                    """SELECT 1 FROM delivery_receipts
                    WHERE platform=? AND namespace=? AND author_id=?
                      AND content_key=? AND group_id=?""",
                    (platform.value, namespace, author_id, content_key, group_id),
                )
            ).fetchone()
        return row is not None

    async def set_delivery_receipt(
        self,
        platform: Platform,
        namespace: str,
        author_id: str,
        content_key: str,
        group_id: int,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO delivery_receipts(
                    platform, namespace, author_id, content_key, group_id, delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, namespace, author_id, content_key, group_id)
                DO UPDATE SET delivered_at=excluded.delivered_at""",
                (
                    platform.value,
                    namespace,
                    author_id,
                    content_key,
                    group_id,
                    int(time.time()),
                ),
            )
            await db.commit()

    async def claim_delivery(
        self,
        platform: Platform,
        namespace: str,
        author_id: str,
        content_key: str,
        group_id: int,
    ) -> bool:
        now = int(time.time())
        async with self._connect() as db:
            await db.execute(
                """DELETE FROM delivery_claims
                WHERE platform=? AND namespace=? AND author_id=? AND content_key=?
                  AND group_id=? AND claimed_at<?""",
                (
                    platform.value,
                    namespace,
                    author_id,
                    content_key,
                    group_id,
                    now - DELIVERY_CLAIM_TTL,
                ),
            )
            cursor = await db.execute(
                """INSERT OR IGNORE INTO delivery_claims(
                    platform, namespace, author_id, content_key, group_id, claimed_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    platform.value,
                    namespace,
                    author_id,
                    content_key,
                    group_id,
                    now,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def release_delivery_claim(
        self,
        platform: Platform,
        namespace: str,
        author_id: str,
        content_key: str,
        group_id: int,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """DELETE FROM delivery_claims
                WHERE platform=? AND namespace=? AND author_id=?
                  AND content_key=? AND group_id=?""",
                (platform.value, namespace, author_id, content_key, group_id),
            )
            await db.commit()

    async def prune_delivery_receipts(
        self, platform: Platform, namespace: str, author_id: str, keep_content_key: str
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """DELETE FROM delivery_receipts
                WHERE platform=? AND namespace=? AND author_id=? AND content_key<>?""",
                (platform.value, namespace, author_id, keep_content_key),
            )
            await db.commit()

    # ----------------------------------------------------------------- exports
    async def export_snapshot(self) -> dict[str, Any]:
        subscriptions = await self.list_subscriptions()
        groups: dict[int, dict[str, Any]] = {}
        async with self._connect() as db:
            rows = await (
                await db.execute(
                    """SELECT g.group_id, g.name, g.avatar FROM groups g
                    WHERE EXISTS(SELECT 1 FROM subscriptions s
                                 WHERE s.group_id=g.group_id)
                       OR EXISTS(SELECT 1 FROM filters f WHERE f.group_id=g.group_id)
                       OR EXISTS(SELECT 1 FROM overrides o WHERE o.group_id=g.group_id)
                       OR EXISTS(SELECT 1 FROM atall a WHERE a.group_id=g.group_id)
                    ORDER BY g.group_id"""
                )
            ).fetchall()
        for row in rows:
            groups[int(row["group_id"])] = {
                "group_id": int(row["group_id"]),
                "name": str(row["name"]),
                "avatar": str(row["avatar"]),
                "subscriptions": [],
            }
        for item in subscriptions:
            group = groups.setdefault(
                item.group_id,
                {
                    "group_id": item.group_id,
                    "name": item.group_name,
                    "avatar": item.group_avatar,
                    "subscriptions": [],
                },
            )
            group["subscriptions"].append(
                {
                    "platform": item.platform.value,
                    "author_id": item.author_id,
                    "author_name": item.author_name,
                    "avatar": item.avatar,
                }
            )
        for group_id, group in groups.items():
            group["filters"] = [
                pattern for _, pattern in await self.list_filters(group_id)
            ]
            atall: dict[str, set[str]] = {}
            for row in await self.list_atall(group_id):
                key = f"{row['platform']}:{row['author_id']}"
                atall.setdefault(key, set()).add(str(row["kind"]))
            group["atall"] = {key: sorted(value) for key, value in atall.items()}
            group["overrides"] = await self.list_overrides(group_id)
        return {"groups": list(groups.values())}

    async def settings_map(self) -> dict[str, str]:
        async with self._connect() as db:
            rows = await (
                await db.execute("SELECT key, value FROM settings")
            ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}


repository = Repository()
