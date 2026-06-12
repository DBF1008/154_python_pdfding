from django.core.exceptions import ObjectDoesNotExist
from django.middleware.locale import LocaleMiddleware


class PdfDingLocaleMiddleware(LocaleMiddleware):
    """Local middleware for setting the language code via the profile model."""

    def process_request(self, request):
        # unauthenticated users and auto mode should use the default django way
        # for determening the language_code
        if request.user.is_anonymous or request.user.profile.language_code == 'auto':
            # set language code via default django way
            # https://docs.djangoproject.com/en/6.0/topics/i18n/translation/#how-django-discovers-language-preference
            super().process_request(request)
        else:
            request.LANGUAGE_CODE = request.user.profile.language_code


class CurrentSelectionMiddleware:
    """
    Repair a profile's current workspace/collection selection before each request is handled.

    This is the single chokepoint that keeps the overview, the sidebar and the upload form reading a
    consistent state: it resets invalid selections (e.g. a workspace or collection deleted by another
    member of a shared workspace, or a residual collection id that no longer belongs to the current
    workspace) to safe values via Profile.normalize_current_state.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)

        if user is not None and not user.is_anonymous:
            try:
                request.user.profile.normalize_current_state()
            except ObjectDoesNotExist:  # pragma: no cover # no profile yet -> nothing to repair
                pass

        return self.get_response(request)
