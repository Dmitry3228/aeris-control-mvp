from enum import StrEnum


COMMAND_TIMEOUT_SECONDS = 10


class CommandKind(StrEnum):
    START = "START"
    STOP = "STOP"
    SETPOINT = "SETPOINT"


class CommandStatus(StrEnum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    TIMED_OUT = "TIMED_OUT"


class CommandEvent(StrEnum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    REJECT = "REJECT"
    TIMEOUT = "TIMEOUT"


def transition_command(current: CommandStatus, event: CommandEvent) -> CommandStatus:
    if current is not CommandStatus.PENDING:
        return current
    transitions = {
        CommandEvent.ACKNOWLEDGE: CommandStatus.ACKNOWLEDGED,
        CommandEvent.REJECT: CommandStatus.REJECTED,
        CommandEvent.TIMEOUT: CommandStatus.TIMED_OUT,
    }
    return transitions[event]
