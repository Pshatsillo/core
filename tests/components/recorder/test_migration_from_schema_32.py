"""The tests for the recorder filter matching the EntityFilter component."""

import datetime
import importlib
import sys
import threading
from typing import Any
from unittest.mock import patch
import uuid

from freezegun import freeze_time
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import Index

from homeassistant.components import recorder
from homeassistant.components.recorder import (
    Recorder,
    core,
    db_schema,
    migration,
    statistics,
)
from homeassistant.components.recorder.db_schema import (
    Events,
    EventTypes,
    MigrationChanges,
    States,
    StatesMeta,
)
from homeassistant.components.recorder.models import process_timestamp
from homeassistant.components.recorder.queries import (
    get_migration_changes,
    select_event_type_ids,
)
from homeassistant.components.recorder.util import (
    execute_stmt_lambda_element,
    get_index_by_name,
    session_scope,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import bytes_to_ulid, ulid_at_time, ulid_to_bytes

from .common import (
    async_attach_db_engine,
    async_recorder_block_till_done,
    async_wait_recording_done,
)
from .conftest import instrument_migration

from tests.common import async_test_home_assistant
from tests.typing import RecorderInstanceContextManager

CREATE_ENGINE_TARGET = "homeassistant.components.recorder.core.create_engine"
SCHEMA_MODULE_32 = "tests.components.recorder.db_schema_32"


@pytest.fixture
async def mock_recorder_before_hass(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Set up recorder."""


async def _async_wait_migration_done(hass: HomeAssistant) -> None:
    """Wait for the migration to be done."""
    await recorder.get_instance(hass).async_block_till_done()
    await async_recorder_block_till_done(hass)


def _get_migration_id(hass: HomeAssistant) -> dict[str, int]:
    with session_scope(hass=hass, read_only=True) as session:
        return dict(execute_stmt_lambda_element(session, get_migration_changes()))


def _create_engine_test(*args, **kwargs):
    """Test version of create_engine that initializes with old schema.

    This simulates an existing db with the old schema.
    """
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]
    engine = create_engine(*args, **kwargs)
    old_db_schema.Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            recorder.db_schema.StatisticsRuns(start=statistics.get_start_time())
        )
        session.add(
            recorder.db_schema.SchemaChanges(
                schema_version=old_db_schema.SCHEMA_VERSION
            )
        )
        session.commit()
    return engine


@pytest.fixture
def db_schema_32():
    """Fixture to initialize the db with the old schema."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration, "non_live_data_migration_needed", return_value=False),
        patch.object(core, "StatesMeta", old_db_schema.StatesMeta),
        patch.object(core, "EventTypes", old_db_schema.EventTypes),
        patch.object(core, "EventData", old_db_schema.EventData),
        patch.object(core, "States", old_db_schema.States),
        patch.object(core, "Events", old_db_schema.Events),
        patch.object(core, "StateAttributes", old_db_schema.StateAttributes),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        yield


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("enable_migrate_event_context_ids", [True])
@pytest.mark.parametrize("indices_to_drop", [[], [("events", "ix_events_context_id")]])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_migrate_events_context_ids(
    async_test_recorder: RecorderInstanceContextManager,
    indices_to_drop: list[tuple[str, str]],
) -> None:
    """Test we can migrate old uuid context ids and ulid context ids to binary format."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    test_uuid = uuid.uuid4()
    uuid_hex = test_uuid.hex
    uuid_bin = test_uuid.bytes

    def _insert_events():
        with session_scope(hass=hass) as session:
            session.add_all(
                (
                    old_db_schema.Events(
                        event_type="old_uuid_context_id_event",
                        event_data=None,
                        origin_idx=0,
                        time_fired=None,
                        time_fired_ts=1877721632.452529,
                        context_id=uuid_hex,
                        context_id_bin=None,
                        context_user_id=None,
                        context_user_id_bin=None,
                        context_parent_id=None,
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.Events(
                        event_type="empty_context_id_event",
                        event_data=None,
                        origin_idx=0,
                        time_fired=None,
                        time_fired_ts=1877721632.552529,
                        context_id=None,
                        context_id_bin=None,
                        context_user_id=None,
                        context_user_id_bin=None,
                        context_parent_id=None,
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.Events(
                        event_type="ulid_context_id_event",
                        event_data=None,
                        origin_idx=0,
                        time_fired=None,
                        time_fired_ts=1877721632.552529,
                        context_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                        context_id_bin=None,
                        context_user_id="9400facee45711eaa9308bfd3d19e474",
                        context_user_id_bin=None,
                        context_parent_id="01ARZ3NDEKTSV4RRFFQ69G5FA2",
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.Events(
                        event_type="invalid_context_id_event",
                        event_data=None,
                        origin_idx=0,
                        time_fired=None,
                        time_fired_ts=1877721632.552529,
                        context_id="invalid",
                        context_id_bin=None,
                        context_user_id=None,
                        context_user_id_bin=None,
                        context_parent_id=None,
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.Events(
                        event_type="garbage_context_id_event",
                        event_data=None,
                        origin_idx=0,
                        time_fired=None,
                        time_fired_ts=1277721632.552529,
                        context_id="adapt_lgt:b'5Cf*':interval:b'0R'",
                        context_id_bin=None,
                        context_user_id=None,
                        context_user_id_bin=None,
                        context_parent_id=None,
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.Events(
                        event_type="event_with_garbage_context_id_no_time_fired_ts",
                        event_data=None,
                        origin_idx=0,
                        time_fired=None,
                        time_fired_ts=None,
                        context_id="adapt_lgt:b'5Cf*':interval:b'0R'",
                        context_id_bin=None,
                        context_user_id=None,
                        context_user_id_bin=None,
                        context_parent_id=None,
                        context_parent_id_bin=None,
                    ),
                )
            )

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration.EventsContextIDMigration, "migrate_data"),
        patch.object(migration.EventIDPostMigration, "migrate_data"),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            await instance.async_add_executor_job(_insert_events)

            await async_wait_recording_done(hass)
            now = dt_util.utcnow()
            expected_ulid_fallback_start = ulid_to_bytes(ulid_at_time(now.timestamp()))[
                0:6
            ]
            await _async_wait_migration_done(hass)

            # Remove index
            instance.recorder_and_worker_thread_ids.add(threading.get_ident())
            for table, index in indices_to_drop:
                with session_scope(hass=hass) as session:
                    assert get_index_by_name(session, table, index) is not None
                migration._drop_index(instance.get_session, table, index)

            await hass.async_stop()
            await hass.async_block_till_done()

    def _object_as_dict(obj):
        return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}

    def _fetch_migrated_events():
        with session_scope(hass=hass) as session:
            events = (
                session.query(Events)
                .filter(
                    Events.event_type.in_(
                        [
                            "old_uuid_context_id_event",
                            "empty_context_id_event",
                            "ulid_context_id_event",
                            "invalid_context_id_event",
                            "garbage_context_id_event",
                            "event_with_garbage_context_id_no_time_fired_ts",
                        ]
                    )
                )
                .all()
            )
            assert len(events) == 6
            return {event.event_type: _object_as_dict(event) for event in events}

    # Run again with new schema, let migration run
    async with async_test_home_assistant() as hass:
        with (
            freeze_time(now),
            instrument_migration(hass) as instrumented_migration,
            patch(
                "sqlalchemy.schema.Index.create", autospec=True, wraps=Index.create
            ) as wrapped_idx_create,
            patch.object(migration.EventIDPostMigration, "migrate_data"),
        ):
            async with async_test_recorder(
                hass, wait_recorder=False, wait_recorder_setup=False
            ) as instance:
                # Check the context ID migrator is considered non-live
                assert recorder.util.async_migration_is_live(hass) is False
                instrumented_migration.migration_stall.set()
                instance.recorder_and_worker_thread_ids.add(threading.get_ident())

                await hass.async_block_till_done()
                await async_wait_recording_done(hass)
                await async_wait_recording_done(hass)

                events_by_type = await instance.async_add_executor_job(
                    _fetch_migrated_events
                )

                migration_changes = await instance.async_add_executor_job(
                    _get_migration_id, hass
                )

                # Check the index which will be removed by the migrator no longer exists
                with session_scope(hass=hass) as session:
                    assert (
                        get_index_by_name(session, "events", "ix_events_context_id")
                        is None
                    )

                await hass.async_stop()
                await hass.async_block_till_done()

    # Check the index we removed was recreated
    index_names = [call[1][0].name for call in wrapped_idx_create.mock_calls]
    assert index_names == [index for _, index in indices_to_drop]

    old_uuid_context_id_event = events_by_type["old_uuid_context_id_event"]
    assert old_uuid_context_id_event["context_id"] is None
    assert old_uuid_context_id_event["context_user_id"] is None
    assert old_uuid_context_id_event["context_parent_id"] is None
    assert old_uuid_context_id_event["context_id_bin"] == uuid_bin
    assert old_uuid_context_id_event["context_user_id_bin"] is None
    assert old_uuid_context_id_event["context_parent_id_bin"] is None

    empty_context_id_event = events_by_type["empty_context_id_event"]
    assert empty_context_id_event["context_id"] is None
    assert empty_context_id_event["context_user_id"] is None
    assert empty_context_id_event["context_parent_id"] is None
    assert empty_context_id_event["context_id_bin"].startswith(
        b"\x01\xb50\xeeO("
    )  # 6 bytes of timestamp + random
    assert empty_context_id_event["context_user_id_bin"] is None
    assert empty_context_id_event["context_parent_id_bin"] is None

    ulid_context_id_event = events_by_type["ulid_context_id_event"]
    assert ulid_context_id_event["context_id"] is None
    assert ulid_context_id_event["context_user_id"] is None
    assert ulid_context_id_event["context_parent_id"] is None
    assert (
        bytes_to_ulid(ulid_context_id_event["context_id_bin"])
        == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    )
    assert (
        ulid_context_id_event["context_user_id_bin"]
        == b"\x94\x00\xfa\xce\xe4W\x11\xea\xa90\x8b\xfd=\x19\xe4t"
    )
    assert (
        bytes_to_ulid(ulid_context_id_event["context_parent_id_bin"])
        == "01ARZ3NDEKTSV4RRFFQ69G5FA2"
    )

    invalid_context_id_event = events_by_type["invalid_context_id_event"]
    assert invalid_context_id_event["context_id"] is None
    assert invalid_context_id_event["context_user_id"] is None
    assert invalid_context_id_event["context_parent_id"] is None
    assert invalid_context_id_event["context_id_bin"].startswith(
        b"\x01\xb50\xeeO("
    )  # 6 bytes of timestamp + random
    assert invalid_context_id_event["context_user_id_bin"] is None
    assert invalid_context_id_event["context_parent_id_bin"] is None

    garbage_context_id_event = events_by_type["garbage_context_id_event"]
    assert garbage_context_id_event["context_id"] is None
    assert garbage_context_id_event["context_user_id"] is None
    assert garbage_context_id_event["context_parent_id"] is None
    assert garbage_context_id_event["context_id_bin"].startswith(
        b"\x01)~$\xdf("
    )  # 6 bytes of timestamp + random
    assert garbage_context_id_event["context_user_id_bin"] is None
    assert garbage_context_id_event["context_parent_id_bin"] is None

    event_with_garbage_context_id_no_time_fired_ts = events_by_type[
        "event_with_garbage_context_id_no_time_fired_ts"
    ]
    assert event_with_garbage_context_id_no_time_fired_ts["context_id"] is None
    assert event_with_garbage_context_id_no_time_fired_ts["context_user_id"] is None
    assert event_with_garbage_context_id_no_time_fired_ts["context_parent_id"] is None
    assert event_with_garbage_context_id_no_time_fired_ts["context_id_bin"].startswith(
        expected_ulid_fallback_start
    )  # 6 bytes of timestamp + random
    assert event_with_garbage_context_id_no_time_fired_ts["context_user_id_bin"] is None
    assert (
        event_with_garbage_context_id_no_time_fired_ts["context_parent_id_bin"] is None
    )

    assert (
        migration_changes[migration.EventsContextIDMigration.migration_id]
        == migration.EventsContextIDMigration.migration_version
    )


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("enable_migrate_event_context_ids", [True])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_finish_migrate_events_context_ids(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Test we re migrate old uuid context ids and ulid context ids to binary format.

    Before PR https://github.com/home-assistant/core/pull/125214, the migrator would
    mark the migration as done before ensuring unused indices were dropped. This
    test makes sure we drop the unused indices.
    """
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    def _insert_migration():
        with session_scope(hass=hass) as session:
            session.merge(
                MigrationChanges(
                    migration_id=migration.EventsContextIDMigration.migration_id,
                    version=1,
                )
            )

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration.EventsContextIDMigration, "migrate_data"),
        patch.object(
            migration.EventIDPostMigration,
            "needs_migrate_impl",
            return_value=migration.DataMigrationStatus(
                needs_migrate=False, migration_done=True
            ),
        ),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            instance.recorder_and_worker_thread_ids.add(threading.get_ident())

            await hass.async_block_till_done()
            await async_wait_recording_done(hass)

            # Check the index which will be removed by the migrator exists
            with session_scope(hass=hass) as session:
                assert get_index_by_name(session, "events", "ix_events_context_id")

            await hass.async_stop()
            await hass.async_block_till_done()

    # Run once with new schema, fake migration did not complete
    with (
        patch.object(migration.EventsContextIDMigration, "migrate_data"),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            instance.recorder_and_worker_thread_ids.add(threading.get_ident())

            await hass.async_block_till_done()
            await async_wait_recording_done(hass)
            await async_wait_recording_done(hass)

            # Fake migration ran with old version
            await instance.async_add_executor_job(_insert_migration)
            await async_wait_recording_done(hass)

            # Check the index which will be removed by the migrator exists
            with session_scope(hass=hass) as session:
                assert get_index_by_name(session, "events", "ix_events_context_id")

            await hass.async_stop()
            await hass.async_block_till_done()

    # Run again with new schema, let migration complete
    async with (
        async_test_home_assistant() as hass,
        async_test_recorder(hass) as instance,
    ):
        instance.recorder_and_worker_thread_ids.add(threading.get_ident())

        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
        await async_wait_recording_done(hass)

        migration_changes = await instance.async_add_executor_job(
            _get_migration_id, hass
        )
        # Check migration ran again
        assert (
            migration_changes[migration.EventsContextIDMigration.migration_id]
            == migration.EventsContextIDMigration.migration_version
        )

        # Check the index which will be removed by the migrator no longer exists
        with session_scope(hass=hass) as session:
            assert get_index_by_name(session, "events", "ix_events_context_id") is None

        await hass.async_stop()
        await hass.async_block_till_done()


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("enable_migrate_state_context_ids", [True])
@pytest.mark.parametrize("indices_to_drop", [[], [("states", "ix_states_context_id")]])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_migrate_states_context_ids(
    async_test_recorder: RecorderInstanceContextManager,
    indices_to_drop: list[tuple[str, str]],
) -> None:
    """Test we can migrate old uuid context ids and ulid context ids to binary format."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    test_uuid = uuid.uuid4()
    uuid_hex = test_uuid.hex
    uuid_bin = test_uuid.bytes

    def _insert_states():
        with session_scope(hass=hass) as session:
            session.add_all(
                (
                    old_db_schema.States(
                        entity_id="state.old_uuid_context_id",
                        last_updated_ts=1477721632.452529,
                        context_id=uuid_hex,
                        context_id_bin=None,
                        context_user_id=None,
                        context_user_id_bin=None,
                        context_parent_id=None,
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.States(
                        entity_id="state.empty_context_id",
                        last_updated_ts=1477721632.552529,
                        context_id=None,
                        context_id_bin=None,
                        context_user_id=None,
                        context_user_id_bin=None,
                        context_parent_id=None,
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.States(
                        entity_id="state.ulid_context_id",
                        last_updated_ts=1477721632.552529,
                        context_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
                        context_id_bin=None,
                        context_user_id="9400facee45711eaa9308bfd3d19e474",
                        context_user_id_bin=None,
                        context_parent_id="01ARZ3NDEKTSV4RRFFQ69G5FA2",
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.States(
                        entity_id="state.invalid_context_id",
                        last_updated_ts=1477721632.552529,
                        context_id="invalid",
                        context_id_bin=None,
                        context_user_id=None,
                        context_user_id_bin=None,
                        context_parent_id=None,
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.States(
                        entity_id="state.garbage_context_id",
                        last_updated_ts=1477721632.552529,
                        context_id="adapt_lgt:b'5Cf*':interval:b'0R'",
                        context_id_bin=None,
                        context_user_id=None,
                        context_user_id_bin=None,
                        context_parent_id=None,
                        context_parent_id_bin=None,
                    ),
                    old_db_schema.States(
                        entity_id="state.human_readable_uuid_context_id",
                        last_updated_ts=1477721632.552529,
                        context_id="0ae29799-ee4e-4f45-8116-f582d7d3ee65",
                        context_id_bin=None,
                        context_user_id="0ae29799-ee4e-4f45-8116-f582d7d3ee65",
                        context_user_id_bin=None,
                        context_parent_id="0ae29799-ee4e-4f45-8116-f582d7d3ee65",
                        context_parent_id_bin=None,
                    ),
                )
            )

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration.StatesContextIDMigration, "migrate_data"),
        patch.object(migration.EventIDPostMigration, "migrate_data"),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            await instance.async_add_executor_job(_insert_states)

            await async_wait_recording_done(hass)
            await _async_wait_migration_done(hass)

            # Remove index
            instance.recorder_and_worker_thread_ids.add(threading.get_ident())
            for table, index in indices_to_drop:
                with session_scope(hass=hass) as session:
                    assert get_index_by_name(session, table, index) is not None
                migration._drop_index(instance.get_session, table, index)

            await hass.async_stop()
            await hass.async_block_till_done()

    def _object_as_dict(obj):
        return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}

    def _fetch_migrated_states():
        with session_scope(hass=hass) as session:
            events = (
                session.query(States)
                .filter(
                    States.entity_id.in_(
                        [
                            "state.old_uuid_context_id",
                            "state.empty_context_id",
                            "state.ulid_context_id",
                            "state.invalid_context_id",
                            "state.garbage_context_id",
                            "state.human_readable_uuid_context_id",
                        ]
                    )
                )
                .all()
            )
            assert len(events) == 6
            return {state.entity_id: _object_as_dict(state) for state in events}

    # Run again with new schema, let migration run
    async with async_test_home_assistant() as hass:
        with (
            instrument_migration(hass) as instrumented_migration,
            patch(
                "sqlalchemy.schema.Index.create", autospec=True, wraps=Index.create
            ) as wrapped_idx_create,
            patch.object(migration.EventIDPostMigration, "migrate_data"),
        ):
            async with async_test_recorder(
                hass, wait_recorder=False, wait_recorder_setup=False
            ) as instance:
                # Check the context ID migrator is considered non-live
                assert recorder.util.async_migration_is_live(hass) is False
                instrumented_migration.migration_stall.set()
                instance.recorder_and_worker_thread_ids.add(threading.get_ident())

                await hass.async_block_till_done()
                await async_wait_recording_done(hass)
                await async_wait_recording_done(hass)

                states_by_entity_id = await instance.async_add_executor_job(
                    _fetch_migrated_states
                )

                migration_changes = await instance.async_add_executor_job(
                    _get_migration_id, hass
                )

                # Check the index which will be removed by the migrator no longer exists
                with session_scope(hass=hass) as session:
                    assert (
                        get_index_by_name(session, "states", "ix_states_context_id")
                        is None
                    )

                await hass.async_stop()
                await hass.async_block_till_done()

    # Check the index we removed was recreated
    index_names = [call[1][0].name for call in wrapped_idx_create.mock_calls]
    assert index_names == [index for _, index in indices_to_drop]

    old_uuid_context_id = states_by_entity_id["state.old_uuid_context_id"]
    assert old_uuid_context_id["context_id"] is None
    assert old_uuid_context_id["context_user_id"] is None
    assert old_uuid_context_id["context_parent_id"] is None
    assert old_uuid_context_id["context_id_bin"] == uuid_bin
    assert old_uuid_context_id["context_user_id_bin"] is None
    assert old_uuid_context_id["context_parent_id_bin"] is None

    empty_context_id = states_by_entity_id["state.empty_context_id"]
    assert empty_context_id["context_id"] is None
    assert empty_context_id["context_user_id"] is None
    assert empty_context_id["context_parent_id"] is None
    assert empty_context_id["context_id_bin"].startswith(
        b"\x01X\x0f\x12\xaf("
    )  # 6 bytes of timestamp + random
    assert empty_context_id["context_user_id_bin"] is None
    assert empty_context_id["context_parent_id_bin"] is None

    ulid_context_id = states_by_entity_id["state.ulid_context_id"]
    assert ulid_context_id["context_id"] is None
    assert ulid_context_id["context_user_id"] is None
    assert ulid_context_id["context_parent_id"] is None
    assert (
        bytes_to_ulid(ulid_context_id["context_id_bin"]) == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    )
    assert (
        ulid_context_id["context_user_id_bin"]
        == b"\x94\x00\xfa\xce\xe4W\x11\xea\xa90\x8b\xfd=\x19\xe4t"
    )
    assert (
        bytes_to_ulid(ulid_context_id["context_parent_id_bin"])
        == "01ARZ3NDEKTSV4RRFFQ69G5FA2"
    )

    invalid_context_id = states_by_entity_id["state.invalid_context_id"]
    assert invalid_context_id["context_id"] is None
    assert invalid_context_id["context_user_id"] is None
    assert invalid_context_id["context_parent_id"] is None
    assert invalid_context_id["context_id_bin"].startswith(
        b"\x01X\x0f\x12\xaf("
    )  # 6 bytes of timestamp + random
    assert invalid_context_id["context_user_id_bin"] is None
    assert invalid_context_id["context_parent_id_bin"] is None

    garbage_context_id = states_by_entity_id["state.garbage_context_id"]
    assert garbage_context_id["context_id"] is None
    assert garbage_context_id["context_user_id"] is None
    assert garbage_context_id["context_parent_id"] is None
    assert garbage_context_id["context_id_bin"].startswith(
        b"\x01X\x0f\x12\xaf("
    )  # 6 bytes of timestamp + random
    assert garbage_context_id["context_user_id_bin"] is None
    assert garbage_context_id["context_parent_id_bin"] is None

    human_readable_uuid_context_id = states_by_entity_id[
        "state.human_readable_uuid_context_id"
    ]
    assert human_readable_uuid_context_id["context_id"] is None
    assert human_readable_uuid_context_id["context_user_id"] is None
    assert human_readable_uuid_context_id["context_parent_id"] is None
    assert (
        human_readable_uuid_context_id["context_id_bin"]
        == b"\n\xe2\x97\x99\xeeNOE\x81\x16\xf5\x82\xd7\xd3\xeee"
    )
    assert (
        human_readable_uuid_context_id["context_user_id_bin"]
        == b"\n\xe2\x97\x99\xeeNOE\x81\x16\xf5\x82\xd7\xd3\xeee"
    )
    assert (
        human_readable_uuid_context_id["context_parent_id_bin"]
        == b"\n\xe2\x97\x99\xeeNOE\x81\x16\xf5\x82\xd7\xd3\xeee"
    )

    assert (
        migration_changes[migration.StatesContextIDMigration.migration_id]
        == migration.StatesContextIDMigration.migration_version
    )


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("enable_migrate_state_context_ids", [True])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_finish_migrate_states_context_ids(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Test we re migrate old uuid context ids and ulid context ids to binary format.

    Before PR https://github.com/home-assistant/core/pull/125214, the migrator would
    mark the migration as done before ensuring unused indices were dropped. This
    test makes sure we drop the unused indices.
    """
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    def _insert_migration():
        with session_scope(hass=hass) as session:
            session.merge(
                MigrationChanges(
                    migration_id=migration.StatesContextIDMigration.migration_id,
                    version=1,
                )
            )

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration.StatesContextIDMigration, "migrate_data"),
        patch.object(
            migration.EventIDPostMigration,
            "needs_migrate_impl",
            return_value=migration.DataMigrationStatus(
                needs_migrate=False, migration_done=True
            ),
        ),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            instance.recorder_and_worker_thread_ids.add(threading.get_ident())

            await hass.async_block_till_done()
            await async_wait_recording_done(hass)

            # Check the index which will be removed by the migrator exists
            with session_scope(hass=hass) as session:
                assert get_index_by_name(session, "states", "ix_states_context_id")

            await hass.async_stop()
            await hass.async_block_till_done()

    # Run once with new schema, fake migration did not complete
    with (
        patch.object(migration.StatesContextIDMigration, "migrate_data"),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            instance.recorder_and_worker_thread_ids.add(threading.get_ident())

            await hass.async_block_till_done()
            await async_wait_recording_done(hass)
            await async_wait_recording_done(hass)

            # Fake migration ran with old version
            await instance.async_add_executor_job(_insert_migration)
            await async_wait_recording_done(hass)

            # Check the index which will be removed by the migrator exists
            with session_scope(hass=hass) as session:
                assert get_index_by_name(session, "states", "ix_states_context_id")

            await hass.async_stop()
            await hass.async_block_till_done()

    # Run again with new schema, let migration complete
    async with (
        async_test_home_assistant() as hass,
        async_test_recorder(hass) as instance,
    ):
        instance.recorder_and_worker_thread_ids.add(threading.get_ident())

        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
        await async_wait_recording_done(hass)

        migration_changes = await instance.async_add_executor_job(
            _get_migration_id, hass
        )
        # Check migration ran again
        assert (
            migration_changes[migration.StatesContextIDMigration.migration_id]
            == migration.StatesContextIDMigration.migration_version
        )

        # Check the index which will be removed by the migrator no longer exists
        with session_scope(hass=hass) as session:
            assert get_index_by_name(session, "states", "ix_states_context_id") is None

        await hass.async_stop()
        await hass.async_block_till_done()


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("enable_migrate_event_type_ids", [True])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_migrate_event_type_ids(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Test we can migrate event_types to the EventTypes table."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    def _insert_events():
        with session_scope(hass=hass) as session:
            session.add_all(
                (
                    old_db_schema.Events(
                        event_type="event_type_one",
                        origin_idx=0,
                        time_fired_ts=1677721632.452529,
                    ),
                    old_db_schema.Events(
                        event_type="event_type_one",
                        origin_idx=0,
                        time_fired_ts=1677721632.552529,
                    ),
                    old_db_schema.Events(
                        event_type="event_type_two",
                        origin_idx=0,
                        time_fired_ts=1677721632.552529,
                    ),
                )
            )

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration.EventTypeIDMigration, "migrate_data"),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            await instance.async_add_executor_job(_insert_events)

            await async_wait_recording_done(hass)
            await _async_wait_migration_done(hass)

            await hass.async_stop()
            await hass.async_block_till_done()

    def _fetch_migrated_events():
        with session_scope(hass=hass, read_only=True) as session:
            events = (
                session.query(Events.event_id, Events.time_fired, EventTypes.event_type)
                .filter(
                    Events.event_type_id.in_(
                        select_event_type_ids(
                            (
                                "event_type_one",
                                "event_type_two",
                            )
                        )
                    )
                )
                .outerjoin(EventTypes, Events.event_type_id == EventTypes.event_type_id)
                .all()
            )
            assert len(events) == 3
            result = {}
            for event in events:
                result.setdefault(event.event_type, []).append(
                    {
                        "event_id": event.event_id,
                        "time_fired": event.time_fired,
                        "event_type": event.event_type,
                    }
                )
            return result

    def _get_many():
        with session_scope(hass=hass, read_only=True) as session:
            return instance.event_type_manager.get_many(
                ("event_type_one", "event_type_two"), session
            )

    # Run again with new schema, let migration run
    async with (
        async_test_home_assistant() as hass,
        async_test_recorder(hass) as instance,
    ):
        instance.recorder_and_worker_thread_ids.add(threading.get_ident())

        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
        await async_wait_recording_done(hass)

        events_by_type = await instance.async_add_executor_job(_fetch_migrated_events)
        mapped = await instance.async_add_executor_job(_get_many)
        migration_changes = await instance.async_add_executor_job(
            _get_migration_id, hass
        )

        await hass.async_stop()
        await hass.async_block_till_done()

    assert len(events_by_type["event_type_one"]) == 2
    assert len(events_by_type["event_type_two"]) == 1

    assert mapped["event_type_one"] is not None
    assert mapped["event_type_two"] is not None

    assert (
        migration_changes[migration.EventTypeIDMigration.migration_id]
        == migration.EventTypeIDMigration.migration_version
    )


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("enable_migrate_entity_ids", [True])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_migrate_entity_ids(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Test we can migrate entity_ids to the StatesMeta table."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    def _insert_states():
        with session_scope(hass=hass) as session:
            session.add_all(
                (
                    old_db_schema.States(
                        entity_id="sensor.one",
                        state="one_1",
                        last_updated_ts=1.452529,
                    ),
                    old_db_schema.States(
                        entity_id="sensor.two",
                        state="two_2",
                        last_updated_ts=2.252529,
                    ),
                    old_db_schema.States(
                        entity_id="sensor.two",
                        state="two_1",
                        last_updated_ts=3.152529,
                    ),
                )
            )

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration.EntityIDMigration, "migrate_data"),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            await instance.async_add_executor_job(_insert_states)

            await async_wait_recording_done(hass)
            await _async_wait_migration_done(hass)

            await hass.async_stop()
            await hass.async_block_till_done()

    def _fetch_migrated_states():
        with session_scope(hass=hass, read_only=True) as session:
            states = (
                session.query(
                    States.state,
                    States.metadata_id,
                    States.last_updated_ts,
                    StatesMeta.entity_id,
                )
                .outerjoin(StatesMeta, States.metadata_id == StatesMeta.metadata_id)
                .all()
            )
            assert len(states) == 3
            result = {}
            for state in states:
                result.setdefault(state.entity_id, []).append(
                    {
                        "state_id": state.entity_id,
                        "last_updated_ts": state.last_updated_ts,
                        "state": state.state,
                    }
                )
            return result

    # Run again with new schema, let migration run
    async with (
        async_test_home_assistant() as hass,
        async_test_recorder(hass) as instance,
    ):
        instance.recorder_and_worker_thread_ids.add(threading.get_ident())

        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
        await async_wait_recording_done(hass)

        states_by_entity_id = await instance.async_add_executor_job(
            _fetch_migrated_states
        )
        migration_changes = await instance.async_add_executor_job(
            _get_migration_id, hass
        )

        await hass.async_stop()
        await hass.async_block_till_done()

    assert len(states_by_entity_id["sensor.two"]) == 2
    assert len(states_by_entity_id["sensor.one"]) == 1

    assert (
        migration_changes[migration.EntityIDMigration.migration_id]
        == migration.EntityIDMigration.migration_version
    )


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("enable_migrate_entity_ids", [True])
@pytest.mark.parametrize(
    "indices_to_drop", [[], [("states", "ix_states_entity_id_last_updated_ts")]]
)
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_post_migrate_entity_ids(
    async_test_recorder: RecorderInstanceContextManager,
    indices_to_drop: list[tuple[str, str]],
) -> None:
    """Test we can migrate entity_ids to the StatesMeta table."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    def _insert_events():
        with session_scope(hass=hass) as session:
            session.add_all(
                (
                    old_db_schema.States(
                        entity_id="sensor.one",
                        state="one_1",
                        last_updated_ts=1.452529,
                    ),
                    old_db_schema.States(
                        entity_id="sensor.two",
                        state="two_2",
                        last_updated_ts=2.252529,
                    ),
                    old_db_schema.States(
                        entity_id="sensor.two",
                        state="two_1",
                        last_updated_ts=3.152529,
                    ),
                )
            )

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration.EntityIDMigration, "migrate_data"),
        patch.object(migration.EntityIDPostMigration, "migrate_data"),
        patch.object(migration.EventIDPostMigration, "migrate_data"),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            await instance.async_add_executor_job(_insert_events)

            await async_wait_recording_done(hass)
            await _async_wait_migration_done(hass)

            # Remove index
            instance.recorder_and_worker_thread_ids.add(threading.get_ident())
            for table, index in indices_to_drop:
                with session_scope(hass=hass) as session:
                    assert get_index_by_name(session, table, index) is not None
                migration._drop_index(instance.get_session, table, index)

            await hass.async_stop()
            await hass.async_block_till_done()

    def _fetch_migrated_states():
        with session_scope(hass=hass, read_only=True) as session:
            states = session.query(
                States.state,
                States.entity_id,
            ).all()
            assert len(states) == 3
            return {state.state: state.entity_id for state in states}

    # Run again with new schema, let migration run
    with (
        patch(
            "sqlalchemy.schema.Index.create", autospec=True, wraps=Index.create
        ) as wrapped_idx_create,
        patch.object(migration.EventIDPostMigration, "migrate_data"),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            instance.recorder_and_worker_thread_ids.add(threading.get_ident())

            await hass.async_block_till_done()
            await async_wait_recording_done(hass)

            states_by_state = await instance.async_add_executor_job(
                _fetch_migrated_states
            )

            # Check the index which will be removed by the migrator no longer exists
            with session_scope(hass=hass) as session:
                assert (
                    get_index_by_name(
                        session, "states", "ix_states_entity_id_last_updated_ts"
                    )
                    is None
                )

            await hass.async_stop()
            await hass.async_block_till_done()

    # Check the index we removed was recreated
    index_names = [call[1][0].name for call in wrapped_idx_create.mock_calls]
    assert index_names == [index for _, index in indices_to_drop]

    assert states_by_state["one_1"] is None
    assert states_by_state["two_2"] is None
    assert states_by_state["two_1"] is None


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("enable_migrate_entity_ids", [True])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_migrate_null_entity_ids(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Test we can migrate entity_ids to the StatesMeta table."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    def _insert_states():
        with session_scope(hass=hass) as session:
            session.add(
                old_db_schema.States(
                    entity_id="sensor.one",
                    state="one_1",
                    last_updated_ts=1.452529,
                ),
            )
            session.add_all(
                old_db_schema.States(
                    entity_id=None,
                    state="empty",
                    last_updated_ts=time + 1.452529,
                )
                for time in range(1000)
            )
            session.add(
                old_db_schema.States(
                    entity_id="sensor.one",
                    state="one_1",
                    last_updated_ts=2.452529,
                ),
            )

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration.EntityIDMigration, "migrate_data"),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            await instance.async_add_executor_job(_insert_states)

            await async_wait_recording_done(hass)
            await _async_wait_migration_done(hass)

            await hass.async_stop()
            await hass.async_block_till_done()

    def _fetch_migrated_states():
        with session_scope(hass=hass, read_only=True) as session:
            states = (
                session.query(
                    States.state,
                    States.metadata_id,
                    States.last_updated_ts,
                    StatesMeta.entity_id,
                )
                .outerjoin(StatesMeta, States.metadata_id == StatesMeta.metadata_id)
                .all()
            )
            assert len(states) == 1002
            result = {}
            for state in states:
                result.setdefault(state.entity_id, []).append(
                    {
                        "state_id": state.entity_id,
                        "last_updated_ts": state.last_updated_ts,
                        "state": state.state,
                    }
                )
            return result

    def _get_migration_id():
        with session_scope(hass=hass, read_only=True) as session:
            return dict(execute_stmt_lambda_element(session, get_migration_changes()))

    # Run again with new schema, let migration run
    async with (
        async_test_home_assistant() as hass,
        async_test_recorder(hass) as instance,
    ):
        instance.recorder_and_worker_thread_ids.add(threading.get_ident())

        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
        await async_wait_recording_done(hass)

        states_by_entity_id = await instance.async_add_executor_job(
            _fetch_migrated_states
        )
        migration_changes = await instance.async_add_executor_job(_get_migration_id)

        await hass.async_stop()
        await hass.async_block_till_done()

    assert len(states_by_entity_id[migration._EMPTY_ENTITY_ID]) == 1000
    assert len(states_by_entity_id["sensor.one"]) == 2

    assert (
        migration_changes[migration.EntityIDMigration.migration_id]
        == migration.EntityIDMigration.migration_version
    )


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.parametrize("enable_migrate_event_type_ids", [True])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_migrate_null_event_type_ids(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Test we can migrate event_types to the EventTypes table when the event_type is NULL."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    def _insert_events():
        with session_scope(hass=hass) as session:
            session.add(
                old_db_schema.Events(
                    event_type="event_type_one",
                    origin_idx=0,
                    time_fired_ts=1.452529,
                ),
            )
            session.add_all(
                old_db_schema.Events(
                    event_type=None,
                    origin_idx=0,
                    time_fired_ts=time + 1.452529,
                )
                for time in range(1000)
            )
            session.add(
                old_db_schema.Events(
                    event_type="event_type_one",
                    origin_idx=0,
                    time_fired_ts=2.452529,
                ),
            )

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration.EventTypeIDMigration, "migrate_data"),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            await instance.async_add_executor_job(_insert_events)

            await async_wait_recording_done(hass)
            await _async_wait_migration_done(hass)

            await hass.async_stop()
            await hass.async_block_till_done()

    def _fetch_migrated_events():
        with session_scope(hass=hass, read_only=True) as session:
            events = (
                session.query(Events.event_id, Events.time_fired, EventTypes.event_type)
                .filter(
                    Events.event_type_id.in_(
                        select_event_type_ids(
                            (
                                "event_type_one",
                                migration._EMPTY_EVENT_TYPE,
                            )
                        )
                    )
                )
                .outerjoin(EventTypes, Events.event_type_id == EventTypes.event_type_id)
                .all()
            )
            assert len(events) == 1002
            result = {}
            for event in events:
                result.setdefault(event.event_type, []).append(
                    {
                        "event_id": event.event_id,
                        "time_fired": event.time_fired,
                        "event_type": event.event_type,
                    }
                )
            return result

    def _get_migration_id():
        with session_scope(hass=hass, read_only=True) as session:
            return dict(execute_stmt_lambda_element(session, get_migration_changes()))

    # Run again with new schema, let migration run
    async with (
        async_test_home_assistant() as hass,
        async_test_recorder(hass) as instance,
    ):
        instance.recorder_and_worker_thread_ids.add(threading.get_ident())

        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
        await async_wait_recording_done(hass)

        events_by_type = await instance.async_add_executor_job(_fetch_migrated_events)
        migration_changes = await instance.async_add_executor_job(_get_migration_id)

        await hass.async_stop()
        await hass.async_block_till_done()

    assert len(events_by_type["event_type_one"]) == 2
    assert len(events_by_type[migration._EMPTY_EVENT_TYPE]) == 1000
    assert (
        migration_changes[migration.EventTypeIDMigration.migration_id]
        == migration.EventTypeIDMigration.migration_version
    )


@pytest.mark.usefixtures("db_schema_32")
async def test_stats_timestamp_conversion_is_reentrant(
    hass: HomeAssistant, recorder_mock: Recorder
) -> None:
    """Test stats migration is reentrant."""
    await async_wait_recording_done(hass)
    await async_attach_db_engine(hass)
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]
    now = dt_util.utcnow()
    one_year_ago = now - datetime.timedelta(days=365)
    six_months_ago = now - datetime.timedelta(days=180)
    one_month_ago = now - datetime.timedelta(days=30)

    def _do_migration():
        migration._migrate_statistics_columns_to_timestamp_removing_duplicates(
            hass, recorder_mock, recorder_mock.get_session, recorder_mock.engine
        )

    def _insert_fake_metadata():
        with session_scope(hass=hass) as session:
            session.add(
                old_db_schema.StatisticsMeta(
                    id=1000,
                    statistic_id="test",
                    source="test",
                    unit_of_measurement="test",
                    has_mean=True,
                    has_sum=True,
                    name="1",
                )
            )

    def _insert_pre_timestamp_stat(date_time: datetime.datetime) -> None:
        with session_scope(hass=hass) as session:
            session.add(
                old_db_schema.StatisticsShortTerm(
                    metadata_id=1000,
                    created=date_time,
                    created_ts=None,
                    start=date_time,
                    start_ts=None,
                    last_reset=date_time,
                    last_reset_ts=None,
                    state="1",
                )
            )

    def _insert_post_timestamp_stat(date_time: datetime.datetime) -> None:
        with session_scope(hass=hass) as session:
            session.add(
                db_schema.StatisticsShortTerm(
                    metadata_id=1000,
                    created=None,
                    created_ts=date_time.timestamp(),
                    start=None,
                    start_ts=date_time.timestamp(),
                    last_reset=None,
                    last_reset_ts=date_time.timestamp(),
                    state="1",
                )
            )

    def _get_all_short_term_stats() -> list[dict[str, Any]]:
        with session_scope(hass=hass) as session:
            results = [
                {
                    field.name: getattr(result, field.name)
                    for field in old_db_schema.StatisticsShortTerm.__table__.c
                }
                for result in (
                    session.query(old_db_schema.StatisticsShortTerm)
                    .where(old_db_schema.StatisticsShortTerm.metadata_id == 1000)
                    .all()
                )
            ]
            return sorted(results, key=lambda row: row["start_ts"])

    # Do not optimize this block, its intentionally written to interleave
    # with the migration
    await hass.async_add_executor_job(_insert_fake_metadata)
    await async_wait_recording_done(hass)
    await hass.async_add_executor_job(_insert_pre_timestamp_stat, one_year_ago)
    await async_wait_recording_done(hass)
    await hass.async_add_executor_job(_do_migration)
    await hass.async_add_executor_job(_insert_post_timestamp_stat, six_months_ago)
    await async_wait_recording_done(hass)
    await hass.async_add_executor_job(_do_migration)
    await hass.async_add_executor_job(_insert_pre_timestamp_stat, one_month_ago)
    await async_wait_recording_done(hass)
    await hass.async_add_executor_job(_do_migration)

    final_result = await hass.async_add_executor_job(_get_all_short_term_stats)
    # Normalize timestamps since each engine returns them differently
    for row in final_result:
        if row["created"] is not None:
            row["created"] = process_timestamp(row["created"]).replace(tzinfo=None)
        if row["start"] is not None:
            row["start"] = process_timestamp(row["start"]).replace(tzinfo=None)
        if row["last_reset"] is not None:
            row["last_reset"] = process_timestamp(row["last_reset"]).replace(
                tzinfo=None
            )

    assert final_result == [
        {
            "created": process_timestamp(one_year_ago).replace(tzinfo=None),
            "created_ts": one_year_ago.timestamp(),
            "id": 1,
            "last_reset": process_timestamp(one_year_ago).replace(tzinfo=None),
            "last_reset_ts": one_year_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": process_timestamp(one_year_ago).replace(tzinfo=None),
            "start_ts": one_year_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
        {
            "created": None,
            "created_ts": six_months_ago.timestamp(),
            "id": 2,
            "last_reset": None,
            "last_reset_ts": six_months_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": six_months_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
        {
            "created": process_timestamp(one_month_ago).replace(tzinfo=None),
            "created_ts": one_month_ago.timestamp(),
            "id": 3,
            "last_reset": process_timestamp(one_month_ago).replace(tzinfo=None),
            "last_reset_ts": one_month_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": process_timestamp(one_month_ago).replace(tzinfo=None),
            "start_ts": one_month_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
    ]


@pytest.mark.usefixtures("db_schema_32")
async def test_stats_timestamp_with_one_by_one(
    hass: HomeAssistant, recorder_mock: Recorder
) -> None:
    """Test stats migration with one by one."""
    await async_wait_recording_done(hass)
    await async_attach_db_engine(hass)
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]
    now = dt_util.utcnow()
    one_year_ago = now - datetime.timedelta(days=365)
    six_months_ago = now - datetime.timedelta(days=180)
    one_month_ago = now - datetime.timedelta(days=30)

    def _do_migration():
        with patch.object(
            migration,
            "_migrate_statistics_columns_to_timestamp",
            side_effect=IntegrityError("test", "test", "test"),
        ):
            migration._migrate_statistics_columns_to_timestamp_removing_duplicates(
                hass, recorder_mock, recorder_mock.get_session, recorder_mock.engine
            )

    def _insert_fake_metadata():
        with session_scope(hass=hass) as session:
            session.add(
                old_db_schema.StatisticsMeta(
                    id=1000,
                    statistic_id="test",
                    source="test",
                    unit_of_measurement="test",
                    has_mean=True,
                    has_sum=True,
                    name="1",
                )
            )

    def _insert_pre_timestamp_stat(date_time: datetime.datetime) -> None:
        with session_scope(hass=hass) as session:
            session.add_all(
                (
                    old_db_schema.StatisticsShortTerm(
                        metadata_id=1000,
                        created=date_time,
                        created_ts=None,
                        start=date_time,
                        start_ts=None,
                        last_reset=date_time,
                        last_reset_ts=None,
                        state="1",
                    ),
                    old_db_schema.Statistics(
                        metadata_id=1000,
                        created=date_time,
                        created_ts=None,
                        start=date_time,
                        start_ts=None,
                        last_reset=date_time,
                        last_reset_ts=None,
                        state="1",
                    ),
                )
            )

    def _insert_post_timestamp_stat(date_time: datetime.datetime) -> None:
        with session_scope(hass=hass) as session:
            session.add_all(
                (
                    db_schema.StatisticsShortTerm(
                        metadata_id=1000,
                        created=None,
                        created_ts=date_time.timestamp(),
                        start=None,
                        start_ts=date_time.timestamp(),
                        last_reset=None,
                        last_reset_ts=date_time.timestamp(),
                        state="1",
                    ),
                    db_schema.Statistics(
                        metadata_id=1000,
                        created=None,
                        created_ts=date_time.timestamp(),
                        start=None,
                        start_ts=date_time.timestamp(),
                        last_reset=None,
                        last_reset_ts=date_time.timestamp(),
                        state="1",
                    ),
                )
            )

    def _get_all_stats(table: old_db_schema.StatisticsBase) -> list[dict[str, Any]]:
        """Get all stats from a table."""
        with session_scope(hass=hass) as session:
            results = [
                {field.name: getattr(result, field.name) for field in table.__table__.c}
                for result in session.query(table)
                .where(table.metadata_id == 1000)
                .all()
            ]
            return sorted(results, key=lambda row: row["start_ts"])

    def _insert_and_do_migration():
        _insert_fake_metadata()
        _insert_pre_timestamp_stat(one_year_ago)
        _insert_post_timestamp_stat(six_months_ago)
        _insert_pre_timestamp_stat(one_month_ago)
        _do_migration()

    await hass.async_add_executor_job(_insert_and_do_migration)
    final_short_term_result = await hass.async_add_executor_job(
        _get_all_stats, old_db_schema.StatisticsShortTerm
    )
    final_short_term_result = sorted(
        final_short_term_result, key=lambda row: row["start_ts"]
    )

    assert final_short_term_result == [
        {
            "created": None,
            "created_ts": one_year_ago.timestamp(),
            "id": 1,
            "last_reset": None,
            "last_reset_ts": one_year_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": one_year_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
        {
            "created": None,
            "created_ts": six_months_ago.timestamp(),
            "id": 2,
            "last_reset": None,
            "last_reset_ts": six_months_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": six_months_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
        {
            "created": None,
            "created_ts": one_month_ago.timestamp(),
            "id": 3,
            "last_reset": None,
            "last_reset_ts": one_month_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": one_month_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
    ]

    final_result = await hass.async_add_executor_job(
        _get_all_stats, old_db_schema.Statistics
    )
    final_result = sorted(final_result, key=lambda row: row["start_ts"])

    assert final_result == [
        {
            "created": None,
            "created_ts": one_year_ago.timestamp(),
            "id": 1,
            "last_reset": None,
            "last_reset_ts": one_year_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": one_year_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
        {
            "created": None,
            "created_ts": six_months_ago.timestamp(),
            "id": 2,
            "last_reset": None,
            "last_reset_ts": six_months_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": six_months_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
        {
            "created": None,
            "created_ts": one_month_ago.timestamp(),
            "id": 3,
            "last_reset": None,
            "last_reset_ts": one_month_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": one_month_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
    ]


@pytest.mark.usefixtures("db_schema_32")
async def test_stats_timestamp_with_one_by_one_removes_duplicates(
    hass: HomeAssistant, recorder_mock: Recorder
) -> None:
    """Test stats migration with one by one removes duplicates."""
    await async_wait_recording_done(hass)
    await async_attach_db_engine(hass)
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]
    now = dt_util.utcnow()
    one_year_ago = now - datetime.timedelta(days=365)
    six_months_ago = now - datetime.timedelta(days=180)
    one_month_ago = now - datetime.timedelta(days=30)

    def _do_migration():
        with (
            patch.object(
                migration,
                "_migrate_statistics_columns_to_timestamp",
                side_effect=IntegrityError("test", "test", "test"),
            ),
            patch.object(
                migration,
                "migrate_single_statistics_row_to_timestamp",
                side_effect=IntegrityError("test", "test", "test"),
            ),
        ):
            migration._migrate_statistics_columns_to_timestamp_removing_duplicates(
                hass, recorder_mock, recorder_mock.get_session, recorder_mock.engine
            )

    def _insert_fake_metadata():
        with session_scope(hass=hass) as session:
            session.add(
                old_db_schema.StatisticsMeta(
                    id=1000,
                    statistic_id="test",
                    source="test",
                    unit_of_measurement="test",
                    has_mean=True,
                    has_sum=True,
                    name="1",
                )
            )

    def _insert_pre_timestamp_stat(date_time: datetime.datetime) -> None:
        with session_scope(hass=hass) as session:
            session.add_all(
                (
                    old_db_schema.StatisticsShortTerm(
                        metadata_id=1000,
                        created=date_time,
                        created_ts=None,
                        start=date_time,
                        start_ts=None,
                        last_reset=date_time,
                        last_reset_ts=None,
                        state="1",
                    ),
                    old_db_schema.Statistics(
                        metadata_id=1000,
                        created=date_time,
                        created_ts=None,
                        start=date_time,
                        start_ts=None,
                        last_reset=date_time,
                        last_reset_ts=None,
                        state="1",
                    ),
                )
            )

    def _insert_post_timestamp_stat(date_time: datetime.datetime) -> None:
        with session_scope(hass=hass) as session:
            session.add_all(
                (
                    db_schema.StatisticsShortTerm(
                        metadata_id=1000,
                        created=None,
                        created_ts=date_time.timestamp(),
                        start=None,
                        start_ts=date_time.timestamp(),
                        last_reset=None,
                        last_reset_ts=date_time.timestamp(),
                        state="1",
                    ),
                    db_schema.Statistics(
                        metadata_id=1000,
                        created=None,
                        created_ts=date_time.timestamp(),
                        start=None,
                        start_ts=date_time.timestamp(),
                        last_reset=None,
                        last_reset_ts=date_time.timestamp(),
                        state="1",
                    ),
                )
            )

    def _get_all_stats(table: old_db_schema.StatisticsBase) -> list[dict[str, Any]]:
        """Get all stats from a table."""
        with session_scope(hass=hass) as session:
            results = [
                {field.name: getattr(result, field.name) for field in table.__table__.c}
                for result in session.query(table)
                .where(table.metadata_id == 1000)
                .all()
            ]
            return sorted(results, key=lambda row: row["start_ts"])

    def _insert_and_do_migration():
        _insert_fake_metadata()
        _insert_pre_timestamp_stat(one_year_ago)
        _insert_post_timestamp_stat(six_months_ago)
        _insert_pre_timestamp_stat(one_month_ago)
        _do_migration()

    await hass.async_add_executor_job(_insert_and_do_migration)
    final_short_term_result = await hass.async_add_executor_job(
        _get_all_stats, old_db_schema.StatisticsShortTerm
    )
    final_short_term_result = sorted(
        final_short_term_result, key=lambda row: row["start_ts"]
    )

    assert final_short_term_result == [
        {
            "created": None,
            "created_ts": one_year_ago.timestamp(),
            "id": 1,
            "last_reset": None,
            "last_reset_ts": one_year_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": one_year_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
        {
            "created": None,
            "created_ts": six_months_ago.timestamp(),
            "id": 2,
            "last_reset": None,
            "last_reset_ts": six_months_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": six_months_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
        {
            "created": None,
            "created_ts": one_month_ago.timestamp(),
            "id": 3,
            "last_reset": None,
            "last_reset_ts": one_month_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": one_month_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
    ]

    # All the duplicates should have been removed but
    # the non-duplicates should still be there
    final_result = await hass.async_add_executor_job(
        _get_all_stats, old_db_schema.Statistics
    )
    assert final_result == [
        {
            "created": None,
            "created_ts": six_months_ago.timestamp(),
            "id": 2,
            "last_reset": None,
            "last_reset_ts": six_months_ago.timestamp(),
            "max": None,
            "mean": None,
            "mean_weight": None,
            "metadata_id": 1000,
            "min": None,
            "start": None,
            "start_ts": six_months_ago.timestamp(),
            "state": 1.0,
            "sum": None,
        },
    ]


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_stats_migrate_times(
    async_test_recorder: RecorderInstanceContextManager,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test we can migrate times in the statistics tables."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]
    now = dt_util.utcnow()
    now_timestamp = now.timestamp()

    statistics_kwargs = {
        "created": now,
        "mean": 0,
        "metadata_id": 1,
        "min": 0,
        "max": 0,
        "last_reset": now,
        "start": now,
        "state": 0,
        "sum": 0,
    }
    mock_metadata = old_db_schema.StatisticMetaData(
        has_mean=False,
        has_sum=False,
        name="Test",
        source="sensor",
        statistic_id="sensor.test",
        unit_of_measurement="cats",
    )
    number_of_migrations = 5

    def _get_index_names(table):
        with session_scope(hass=hass) as session:
            return inspect(session.connection()).get_indexes(table)

    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch.object(migration, "non_live_data_migration_needed", return_value=False),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            await hass.async_block_till_done()
            await async_wait_recording_done(hass)
            await async_wait_recording_done(hass)

            def _add_data():
                with session_scope(hass=hass) as session:
                    session.add(old_db_schema.StatisticsMeta.from_meta(mock_metadata))
                with session_scope(hass=hass) as session:
                    session.add(old_db_schema.Statistics(**statistics_kwargs))
                    session.add(old_db_schema.StatisticsShortTerm(**statistics_kwargs))

            await instance.async_add_executor_job(_add_data)
            await hass.async_block_till_done()
            await instance.async_block_till_done()

            statistics_indexes = await instance.async_add_executor_job(
                _get_index_names, "statistics"
            )
            statistics_short_term_indexes = await instance.async_add_executor_job(
                _get_index_names, "statistics_short_term"
            )
            statistics_index_names = {index["name"] for index in statistics_indexes}
            statistics_short_term_index_names = {
                index["name"] for index in statistics_short_term_indexes
            }

            await hass.async_stop()
            await hass.async_block_till_done()

    assert "ix_statistics_statistic_id_start" in statistics_index_names
    assert (
        "ix_statistics_short_term_statistic_id_start"
        in statistics_short_term_index_names
    )

    # Test that the times are migrated during migration from schema 32
    async with (
        async_test_home_assistant() as hass,
        async_test_recorder(hass) as instance,
    ):
        await hass.async_block_till_done()

        # We need to wait for all the migration tasks to complete
        # before we can check the database.
        for _ in range(number_of_migrations):
            await instance.async_block_till_done()
            await async_wait_recording_done(hass)

        def _get_test_data_from_db():
            with session_scope(hass=hass) as session:
                statistics_result = list(
                    session.query(recorder.db_schema.Statistics)
                    .join(
                        recorder.db_schema.StatisticsMeta,
                        recorder.db_schema.Statistics.metadata_id
                        == recorder.db_schema.StatisticsMeta.id,
                    )
                    .where(
                        recorder.db_schema.StatisticsMeta.statistic_id == "sensor.test"
                    )
                )
                statistics_short_term_result = list(
                    session.query(recorder.db_schema.StatisticsShortTerm)
                    .join(
                        recorder.db_schema.StatisticsMeta,
                        recorder.db_schema.StatisticsShortTerm.metadata_id
                        == recorder.db_schema.StatisticsMeta.id,
                    )
                    .where(
                        recorder.db_schema.StatisticsMeta.statistic_id == "sensor.test"
                    )
                )
                session.expunge_all()
                return statistics_result, statistics_short_term_result

        (
            statistics_result,
            statistics_short_term_result,
        ) = await instance.async_add_executor_job(_get_test_data_from_db)

        for results in (statistics_result, statistics_short_term_result):
            assert len(results) == 1
            assert results[0].created is None
            assert results[0].created_ts == now_timestamp
            assert results[0].last_reset is None
            assert results[0].last_reset_ts == now_timestamp
            assert results[0].start is None
            assert results[0].start_ts == now_timestamp

        statistics_indexes = await instance.async_add_executor_job(
            _get_index_names, "statistics"
        )
        statistics_short_term_indexes = await instance.async_add_executor_job(
            _get_index_names, "statistics_short_term"
        )
        statistics_index_names = {index["name"] for index in statistics_indexes}
        statistics_short_term_index_names = {
            index["name"] for index in statistics_short_term_indexes
        }

        assert "ix_statistics_statistic_id_start" not in statistics_index_names
        assert (
            "ix_statistics_short_term_statistic_id_start"
            not in statistics_short_term_index_names
        )

        await hass.async_stop()


@pytest.mark.parametrize("persistent_database", [True])
@pytest.mark.usefixtures("hass_storage")  # Prevent test hass from writing to storage
async def test_cleanup_unmigrated_state_timestamps(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Ensure schema 48 migration cleans up any unmigrated state timestamps."""
    importlib.import_module(SCHEMA_MODULE_32)
    old_db_schema = sys.modules[SCHEMA_MODULE_32]

    test_uuid = uuid.uuid4()
    uuid_hex = test_uuid.hex

    def _object_as_dict(obj):
        return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}

    def _insert_states():
        with session_scope(hass=hass) as session:
            state1 = old_db_schema.States(
                entity_id="state.test_state1",
                last_updated=datetime.datetime(
                    2016, 10, 28, 20, 13, 52, 452529, tzinfo=datetime.UTC
                ),
                last_updated_ts=None,
                last_changed=datetime.datetime(
                    2016, 10, 28, 20, 13, 52, 452529, tzinfo=datetime.UTC
                ),
                last_changed_ts=None,
                context_id=uuid_hex,
                context_id_bin=None,
                context_user_id=None,
                context_user_id_bin=None,
                context_parent_id=None,
                context_parent_id_bin=None,
            )
            state2 = old_db_schema.States(
                entity_id="state.test_state2",
                last_updated=datetime.datetime(
                    2016, 10, 28, 20, 13, 52, 552529, tzinfo=datetime.UTC
                ),
                last_updated_ts=None,
                last_changed=datetime.datetime(
                    2016, 10, 28, 20, 13, 52, 452529, tzinfo=datetime.UTC
                ),
                last_changed_ts=None,
                context_id=None,
                context_id_bin=None,
                context_user_id=None,
                context_user_id_bin=None,
                context_parent_id=None,
                context_parent_id_bin=None,
            )
            session.add_all((state1, state2))
            # There is a default of now() for last_updated_ts so make sure it's not set
            session.query(old_db_schema.States).update(
                {old_db_schema.States.last_updated_ts: None}
            )
            state3 = old_db_schema.States(
                entity_id="state.already_migrated",
                last_updated=None,
                last_updated_ts=1477685632.452529,
                last_changed=None,
                last_changed_ts=1477685632.452529,
                context_id=uuid_hex,
                context_id_bin=None,
                context_user_id=None,
                context_user_id_bin=None,
                context_parent_id=None,
                context_parent_id_bin=None,
            )
            session.add_all((state3,))

        with session_scope(hass=hass, read_only=True) as session:
            states = session.query(old_db_schema.States).all()
            assert len(states) == 3

    # Create database with old schema
    with (
        patch.object(recorder, "db_schema", old_db_schema),
        patch.object(migration, "SCHEMA_VERSION", old_db_schema.SCHEMA_VERSION),
        patch(CREATE_ENGINE_TARGET, new=_create_engine_test),
    ):
        async with (
            async_test_home_assistant() as hass,
            async_test_recorder(hass) as instance,
        ):
            await instance.async_add_executor_job(_insert_states)

            await async_wait_recording_done(hass)
            now = dt_util.utcnow()
            await _async_wait_migration_done(hass)
            await async_wait_recording_done(hass)

            await hass.async_stop()
            await hass.async_block_till_done()

    def _fetch_migrated_states():
        with session_scope(hass=hass) as session:
            states = session.query(States).all()
            assert len(states) == 3
            return {state.state_id: _object_as_dict(state) for state in states}

    # Run again with new schema, let migration run
    async with async_test_home_assistant() as hass:
        with (
            freeze_time(now),
            instrument_migration(hass) as instrumented_migration,
        ):
            async with async_test_recorder(
                hass, wait_recorder=False, wait_recorder_setup=False
            ) as instance:
                # Check the context ID migrator is considered non-live
                assert recorder.util.async_migration_is_live(hass) is False
                instrumented_migration.migration_stall.set()
                instance.recorder_and_worker_thread_ids.add(threading.get_ident())

                await hass.async_block_till_done()
                await async_wait_recording_done(hass)
                await async_wait_recording_done(hass)

                states_by_metadata_id = await instance.async_add_executor_job(
                    _fetch_migrated_states
                )

                await hass.async_stop()
                await hass.async_block_till_done()

    assert len(states_by_metadata_id) == 3
    for state in states_by_metadata_id.values():
        assert state["last_updated_ts"] is not None

    by_entity_id = {
        state["entity_id"]: state for state in states_by_metadata_id.values()
    }
    assert by_entity_id["state.test_state1"]["last_updated_ts"] == 1477685632.452529
    assert by_entity_id["state.test_state2"]["last_updated_ts"] == 1477685632.552529
    assert (
        by_entity_id["state.already_migrated"]["last_updated_ts"] == 1477685632.452529
    )
