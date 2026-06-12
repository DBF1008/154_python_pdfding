from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from users.models import Profile


class TestPdfDingLocaleMiddlewareUnauthenticated(TestCase):
    @override_settings(LANGUAGE_CODE='de')
    def test_unauthenticated(self):
        response = self.client.get(reverse('pdf_overview'))

        assert response.wsgi_request.LANGUAGE_CODE == 'de'


class TestPdfDingLocaleMiddlewareAuthenticated(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username=self.username, password=self.password, email='a@a.com')
        self.client.login(username=self.username, password=self.password)

    @override_settings(LANGUAGE_CODE='de')
    def test_auto(self):
        self.user.profile.language = Profile.LanguageChoice.AUTO
        self.user.profile.save()
        response = self.client.get(reverse('pdf_overview'))

        assert response.wsgi_request.LANGUAGE_CODE == 'de'

    @override_settings(LANGUAGE_CODE='de')
    def test_not_auto(self):
        self.user.profile.language = Profile.LanguageChoice.ENGLISH
        self.user.profile.save()
        response = self.client.get(reverse('pdf_overview'))

        assert response.wsgi_request.LANGUAGE_CODE == 'en'


class TestWorkspaceCollectionSanitizeMiddleware(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username=self.username, password=self.password, email='a@a.com')
        self.client.login(username=self.username, password=self.password)

    def test_middleware_fixes_stale_workspace(self):
        """Middleware sanitizes stale workspace/collection state before view renders."""

        profile = self.user.profile
        profile.current_workspace_id = '00000000-0000-0000-0000-000000000001'
        profile.current_collection_id = '00000000-0000-0000-0000-000000000002'
        profile.save()

        response = self.client.get(reverse('pdf_overview'))

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.current_workspace_id, str(self.user.id))
        self.assertEqual(profile.current_collection_id, 'all')

    def test_middleware_skips_unauthenticated(self):
        """Middleware does nothing for anonymous users."""

        self.client.logout()

        response = self.client.get(reverse('account_login'))

        self.assertIn(response.status_code, [200, 302])

    def test_middleware_happy_path_no_extra_writes(self):
        """On valid state, middleware doesn't modify profile."""

        profile = self.user.profile
        original_ws_id = profile.current_workspace_id
        original_coll_id = profile.current_collection_id

        response = self.client.get(reverse('pdf_overview'))

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.current_workspace_id, original_ws_id)
        self.assertEqual(profile.current_collection_id, original_coll_id)
