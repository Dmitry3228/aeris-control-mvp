from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.migrations import apply_sqlite_migrations
from app.models import Device


@dataclass(frozen=True, slots=True)
class SeedDevice:
    id: str
    name: str
    location: str


FLEET = (
    SeedDevice("AHU-201", "Lobby air handler", "Main lobby"),
    SeedDevice("AHU-202", "West office air handler", "Office west"),
    SeedDevice("AHU-203", "East office air handler", "Office east"),
    SeedDevice("AHU-204", "North production air handler", "North production"),
    SeedDevice("AHU-205", "South production air handler", "South production"),
    SeedDevice("AHU-206", "Packaging air handler", "Packaging"),
    SeedDevice("AHU-207", "Warehouse air handler", "Warehouse"),
    SeedDevice("AHU-208", "Utilities air handler", "Utilities"),
    SeedDevice("AHU-209", "Laboratory air handler", "Laboratory"),
    SeedDevice("AHU-210", "Data center air handler", "Data center"),
    SeedDevice("AHU-211", "Cafeteria air handler", "Cafeteria"),
    SeedDevice("AHU-212", "Maintenance air handler", "Maintenance"),
)


def initialize_database(session_factory: sessionmaker[Session]) -> None:
    """Apply versioned schema migrations and idempotently reconcile the demo fleet."""
    apply_sqlite_migrations(session_factory)
    with session_factory.begin() as session:
        for seeded in FLEET:
            device = session.get(Device, seeded.id)
            if device is None:
                session.add(
                    Device(
                        id=seeded.id,
                        name=seeded.name,
                        equipment_type="AHU",
                        location=seeded.location,
                        enabled=True,
                    )
                )
                continue
            device.name = seeded.name
            device.equipment_type = "AHU"
            device.location = seeded.location
            device.enabled = True
