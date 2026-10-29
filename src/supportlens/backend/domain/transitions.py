"""Status vocabulary and explicit transitions for future application services."""
from .enums import TicketStatus

ALLOWED_TRANSITIONS = {
    TicketStatus.OPEN: frozenset({TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED}),
    TicketStatus.IN_PROGRESS: frozenset({TicketStatus.RESOLVED}),
    TicketStatus.RESOLVED: frozenset({TicketStatus.REOPENED}),
    TicketStatus.REOPENED: frozenset({TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED}),
}


def validate_transition(current: TicketStatus, target: TicketStatus) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise ValueError("Unsupported ticket status transition.")
