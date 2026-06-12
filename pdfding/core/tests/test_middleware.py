from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from pdf.services.workspace_services import create_collection, create_workspace
from pdf.views import pdf_views
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


class TestCurrentSelectionMiddleware(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username=self.username, password=self.password, email='a@a.com')
        self.client.login(username=self.username, password=self.password)

    def test_repairs_stale_workspace(self):
        profile = self.user.profile
        profile.current_workspace_id = 'does-not-exist'
        profile.current_collection_id = 'does-not-exist'
        profile.save()

        response = self.client.get(reverse('pdf_overview'))

        self.assertEqual(response.status_code, 200)
        refreshed = User.objects.get(id=self.user.id).profile
        self.assertEqual(refreshed.current_workspace_id, str(self.user.id))
        self.assertEqual(refreshed.current_collection_id, 'all')

    def test_repairs_foreign_collection(self):
        profile = self.user.profile
        other_workspace = create_workspace('other_ws', self.user)
        foreign_collection = create_collection(other_workspace, 'foreign')

        # current workspace stays personal (valid); the collection points into another workspace
        profile.current_collection_id = foreign_collection.id
        profile.save()

        response = self.client.get(reverse('pdf_overview'))

        self.assertEqual(response.status_code, 200)
        refreshed = User.objects.get(id=self.user.id).profile
        self.assertEqual(refreshed.current_workspace_id, str(self.user.id))
        self.assertEqual(refreshed.current_collection_id, 'all')

        # the overview (and therefore the sidebar) now consistently reports the current collection as "All"
        extra_context = pdf_views.OverviewMixin.get_extra_context(response.wsgi_request)
        self.assertEqual(extra_context['current_collection_id'], 'all')
        self.assertEqual(extra_context['current_collection_name'], 'All')

    def test_valid_selection_untouched(self):
        profile = self.user.profile
        other_workspace = create_workspace('other_ws', self.user)
        other_collection = create_collection(other_workspace, 'other')
        profile.current_workspace_id = other_workspace.id
        profile.current_collection_id = other_collection.id
        profile.save()

        response = self.client.get(reverse('pdf_overview'))

        self.assertEqual(response.status_code, 200)
        refreshed = User.objects.get(id=self.user.id).profile
        self.assertEqual(refreshed.current_workspace_id, other_workspace.id)
        self.assertEqual(refreshed.current_collection_id, other_collection.id)

    def test_anonymous_request_does_not_error(self):
        self.client.logout()

        # an anonymous user has no profile to repair; the middleware must skip it and let the request
        # proceed to the login redirect instead of raising
        response = self.client.get(reverse('pdf_overview'))

        self.assertEqual(response.status_code, 302)
