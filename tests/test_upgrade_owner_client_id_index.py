import asyncio, tempfile, unittest, aiosqlite
from src.core.database import Database

class UpgradeIndexTests(unittest.TestCase):
    def test_existing_db_missing_owner_client_id_upgrades_without_crash(self):
        async def run():
            tmp = tempfile.TemporaryDirectory()
            path = f"{tmp.name}/flow.db"
            db = Database(db_path=path)
            # 1) Build the full current schema.
            await db.init_db()
            # 2) Simulate an OLD database: remove the newer column + its index.
            async with aiosqlite.connect(path) as c:
                await c.execute("DROP INDEX IF EXISTS idx_tokens_owner_client_id")
                await c.execute("ALTER TABLE tokens DROP COLUMN owner_client_id")
                await c.commit()
                cols = [r[1] async for r in await c.execute("PRAGMA table_info(tokens)")]
                assert "owner_client_id" not in cols, "fixture should lack the column"
            # 3) Replicate main.py existing-db startup order (fix under test).
            await db.check_and_migrate_db({})
            await db.init_db()
            # 4) Column + index restored, no crash.
            async with aiosqlite.connect(path) as c:
                cols = [r[1] async for r in await c.execute("PRAGMA table_info(tokens)")]
                self.assertIn("owner_client_id", cols)
                idx = [r[0] async for r in await c.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name='idx_tokens_owner_client_id'")]
                self.assertEqual(idx, ["idx_tokens_owner_client_id"])
            tmp.cleanup()
        asyncio.run(run())

    def test_old_order_crashes_on_missing_column(self):
        # Guard: init_db BEFORE migrate must fail on an old-schema DB, proving the
        # main.py reordering is what prevents the production crash.
        async def run():
            tmp = tempfile.TemporaryDirectory()
            path = f"{tmp.name}/flow.db"
            db = Database(db_path=path)
            await db.init_db()
            async with aiosqlite.connect(path) as c:
                await c.execute("DROP INDEX IF EXISTS idx_tokens_owner_client_id")
                await c.execute("ALTER TABLE tokens DROP COLUMN owner_client_id")
                await c.commit()
            with self.assertRaises(Exception):
                await db.init_db()  # old order recreates index before column exists
            tmp.cleanup()
        asyncio.run(run())

if __name__ == "__main__":
    unittest.main()
