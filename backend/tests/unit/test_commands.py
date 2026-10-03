from app.domain.commands import COMMAND_TIMEOUT_SECONDS, CommandEvent, CommandStatus, transition_command


def test_command_timeout_transition() -> None:
    status = transition_command(CommandStatus.PENDING, CommandEvent.TIMEOUT)

    assert COMMAND_TIMEOUT_SECONDS == 10
    assert status is CommandStatus.TIMED_OUT
