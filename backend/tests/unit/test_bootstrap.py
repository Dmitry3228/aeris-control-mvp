from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.bootstrap import FLEET, initialize_database
from app.models import Device


def test_database_bootstrap_is_idempotent_and_reconciles_fleet_metadata() -> None:
    engine = create_engine("sqlite://")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    initialize_database(sessions)
    with sessions.begin() as session:
        device = session.get(Device, "AHU-204")
        assert device is not None
        device.name = "stale name"

    initialize_database(sessions)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Device)) == len(FLEET)
        device = session.get(Device, "AHU-204")
        assert device is not None
        assert device.name == "North production air handler"
        assert device.location == "North production"

    engine.dispose()
