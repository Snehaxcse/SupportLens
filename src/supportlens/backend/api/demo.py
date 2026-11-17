"""Curated demo inputs. The hosted profile does not persist arbitrary text."""
from ..domain.enums import UserRole
from .errors import APIError

TICKETS = (
    ("Fictional playback interruption", "The fictional audio player pauses partway through a sample track."),
    ("Fictional display problem", "The fictional sample library shows an empty list after switching screens."),
)
MESSAGES = (
    "In this fictional scenario, the sample track pauses again.",
    "The fictional playback guide lists the demo steps for reviewing the playback trace.",
    "Demo note: The fictional sample issue is ready for agent review.",
)
ARTICLE_TITLE = "Fictional playback trace guide"
ARTICLE_BODIES = (
    "The fictional audio player displays a sample playback trace. The sample trace records when a track pauses.",
    "The fictional player shows a trace for demo tracks. Review the playback trace to locate the sample pause.",
)
ARTICLE_SOURCE = "SupportLens fictional demo fixtures"
ARTICLE_RIGHTS = "Original SupportLens synthetic demonstration text."


class SyntheticDemo:
    def __init__(self, settings):
        self.roles = {settings.demo_customer_uid: UserRole.CUSTOMER, settings.demo_agent_uid: UserRole.AGENT}

    def authorize_identity(self, identity):
        if identity.uid not in self.roles or identity.email is None:
            raise APIError(403, "demo_access_required", "Use a provisioned fictional demo account.")

    def authorize_account(self, user, identity):
        if user.role != self.roles[identity.uid] or not user.email.endswith("@example.invalid"):
            raise APIError(403, "demo_access_required", "Use a provisioned fictional demo account.")

    @staticmethod
    def require(condition):
        if not condition:
            raise APIError(422, "demo_fixture_required", "Use the fictional content from the demo guide.")

    def ticket(self, payload):
        self.require((payload.subject, payload.description) in TICKETS)

    def message(self, payload):
        self.require(payload.body in MESSAGES)

    def article(self, payload):
        self.require(payload.title == ARTICLE_TITLE and payload.body in ARTICLE_BODIES
                     and payload.source_type == "SYNTHETIC_TEST" and payload.source_url is None
                     and payload.source_name == ARTICLE_SOURCE and payload.rights_reference == ARTICLE_RIGHTS)

    def draft_review(self, payload):
        self.require(payload.text is None or payload.text in MESSAGES)


def validate_demo_input(request, operation, payload):
    demo = request.app.state.runtime.demo
    if demo is not None:
        getattr(demo, operation)(payload)
