from datetime import datetime, timedelta, timezone

from django.contrib.sessions.models import Session
from pdf.models.shared_pdf_models import SharedPdf


def check_shared_access_allowed_by_identifier(identifier: str, session: Session):
    """Check if access to shared pdf is allowed based on session."""

    shared_pdf = SharedPdf.objects.get(pk=identifier)

    return check_shared_access_allowed(shared_pdf, session)


def check_shared_access_allowed(shared_pdf: SharedPdf, session: Session):
    """
    Check if access to a shared pdf is allowed for the given session.

    A session is granted access once it has been added to the shared pdf's sessions (after passing a possible
    password check). The max views limit only controls how many sessions can be granted access in the first place;
    it does not cut off a session that is already viewing the file. This way the pdf file can be loaded and the page
    refreshed during a view without the view counting itself out immediately. Expiration and deletion always revoke
    access, even for a session that was granted access before.
    """

    if shared_pdf.deleted or shared_pdf.expired:
        return False

    return check_session_granted(shared_pdf, session)


def check_session_granted(shared_pdf: SharedPdf, session: Session) -> bool:
    """Check if the given session has been granted access to the shared pdf and is still valid (not expired)."""

    return bool(
        session
        and session.session_key
        and (session.get_expiry_date() - datetime.now(timezone.utc)).total_seconds() > 0
        and shared_pdf.sessions.filter(session_key=session.session_key).count()
    )


def get_future_datetime(time_input: str) -> datetime | None:
    """
    Gets a datetime in the future from now based on the input. Input is in the format _d_h_m, e.g. 1d0h22m.
    If input is an empty string returns None.
    """

    if not time_input:
        return None

    split_by_d = time_input.split('d')
    split_by_d_and_h = split_by_d[1].split('h')
    split_by_d_and_h_and_m = split_by_d_and_h[1].split('m')

    days = int(split_by_d[0])
    hours = int(split_by_d_and_h[0])
    minutes = int(split_by_d_and_h_and_m[0])

    now = datetime.now(timezone.utc)
    future_date = now + timedelta(days=days, hours=hours, minutes=minutes)

    return future_date
