from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from django.contrib.sessions.models import Session
from django.urls import reverse
from pdf.models.shared_pdf_models import SharedPdf

# allowed values for the combinable status filters of the shared PDF overview
EXPIRATION_FILTER_VALUES = ('active', 'expired')
PASSWORD_FILTER_VALUES = ('yes', 'no')


def check_shared_access_allowed_by_identifier(identifier: str, session: Session):
    """Check if access to shared pdf is allowed based on session."""

    shared_pdf = SharedPdf.objects.get(pk=identifier)

    return check_shared_access_allowed(shared_pdf, session)


def check_shared_access_allowed(shared_pdf: SharedPdf, session: Session):
    """Check if access to shared pdf is allowed based on session."""

    if shared_pdf.inactive or shared_pdf.deleted:
        return False

    if (
        session
        and (session.get_expiry_date() - datetime.now(timezone.utc)).total_seconds() > 0
        and shared_pdf.sessions.filter(session_key=session.session_key).count()
    ):
        return True
    else:
        return False


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


def construct_shared_query_overview_url(
    referer_url: str,
    search_query: str | None,
    expiration_query: str | None,
    password_query: str | None,
) -> str:
    """
    Construct the shared PDF overview url after performing a search or changing a status filter.

    Search and the status filters (expiration + password) can be combined freely. Only the parameters present in the
    current request are changed; the others are kept from the referer so the filters stack. A parameter is reset by
    providing it with an empty (or otherwise invalid) value, e.g. "search=" or "expiration=".
    """

    parsed_referer_url = urlparse(referer_url)
    query_parameters = parse_qs(parsed_referer_url.query)

    # search is free text; None keeps the existing search, an empty string resets it
    if search_query is not None:
        search = search_query.strip().replace(' ', '+')
        if search:
            query_parameters['search'] = [search]
        else:
            query_parameters.pop('search', None)

    _apply_status_filter(query_parameters, 'expiration', expiration_query, EXPIRATION_FILTER_VALUES)
    _apply_status_filter(query_parameters, 'password', password_query, PASSWORD_FILTER_VALUES)

    query_string = '&'.join(
        f'{key}={"+".join(query)}' for key, query in query_parameters.items() if query not in [[], ['']]
    )

    overview_url = reverse('shared_pdf_overview')

    if query_string:
        overview_url = f'{overview_url}?{query_string}'

    return overview_url


def _apply_status_filter(query_parameters: dict, key: str, value: str | None, allowed_values: tuple) -> None:
    """
    Apply a single status filter to the query parameters. None keeps the existing value, a value from allowed_values
    sets it and any other value (e.g. an empty string) resets the filter.
    """

    if value is None:
        return

    if value in allowed_values:
        query_parameters[key] = [value]
    else:
        query_parameters.pop(key, None)
