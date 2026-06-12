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


class WorkspaceCollectionSanitizeMiddleware:
    """Ensure authenticated user's profile has valid workspace/collection state."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            request.user.profile.sanitize_state()

        return self.get_response(request)
