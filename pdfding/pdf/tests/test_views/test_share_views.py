from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.http.response import Http404
from django.test import Client, TestCase
from django.urls import reverse
from pdf.forms import (
    SharedDeletionDateForm,
    SharedDescriptionForm,
    SharedExpirationDateForm,
    SharedMaxViewsForm,
    SharedNameForm,
    SharedPasswordForm,
    ShareForm,
    ViewSharedPasswordForm,
)
from pdf.models.pdf_models import Pdf
from pdf.models.shared_pdf_models import SharedPdf
from pdf.services.workspace_services import create_workspace
from pdf.views.share_views import (
    AddSharedPdfMixin,
    BaseSharedPdfPublicView,
    EditSharedPdfMixin,
    OverviewMixin,
    PdfPublicMixin,
    SharedPdfMixin,
)

from pdfding.pdf.views import share_views


def set_up(self):
    self.client = Client()
    self.user = User.objects.create_user(username=self.username, password=self.password, email='a@a.com')
    self.pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name='pdf')


class TestAddSharedPdfMixin(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.user = None
        self.pdf = None
        set_up(self)
        self.client.login(username=self.username, password=self.password)

    @patch('pdf.views.share_views.AddSharedPdfMixin.form')
    def test_get_context_get(self, mock_share_form):
        # we need to create a request so get_pdf can access the user profile
        response = self.client.get(reverse('pdf_overview'))

        shared_pdf_mixin = AddSharedPdfMixin()
        generated_context = shared_pdf_mixin.get_context_get(response.wsgi_request, self.pdf.id)

        self.assertEqual(generated_context['pdf_name'], self.pdf.name)
        mock_share_form.assert_called_once_with(profile=self.user.profile)
        self.assertIsInstance(generated_context['form'], MagicMock)

    @patch('pdf.views.share_views.get_future_datetime', return_value=datetime.now(timezone.utc))
    @patch('pdf.views.share_views.AddSharedPdfMixin.add_qr_code')
    def test_obj_save(self, mock_add_qr_code, mock_get_future_datetime):
        # do a dummy request so we can get a request object
        response = self.client.get(reverse('pdf_overview'))
        form = ShareForm(
            data={'name': 'some_shared_pdf', 'expiration_input': '0d1h1m', 'deletion_input': '0d2h2m'},
            profile=self.user.profile,
        )

        AddSharedPdfMixin.obj_save(form, response.wsgi_request, self.pdf.id)
        shared_pdf = self.user.profile.current_shared_pdfs.get(name='some_shared_pdf')

        self.assertEqual(shared_pdf.pdf, self.pdf)
        mock_get_future_datetime.assert_any_call('0d1h1m')
        mock_get_future_datetime.assert_any_call('0d2h2m')
        mock_add_qr_code.assert_called_with(shared_pdf, response.wsgi_request)

    @patch('pdf.views.share_views.AddSharedPdfMixin.generate_qr_code', return_value=BytesIO())
    def test_add_qr_code(self, mock_generate_qr_code):
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share')
        # we need to create a request so get_pdf can access the user profile
        response = self.client.get(reverse('pdf_overview'))

        AddSharedPdfMixin.add_qr_code(shared_pdf, response.wsgi_request)

        mock_generate_qr_code.assert_called_with(f'http://testserver/pdf/shared/{shared_pdf.id}')

    def test_set_access_dates(self):
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share')

        AddSharedPdfMixin.set_access_dates(shared_pdf, '1d0h22m', '1d0h22m')

        # get pdf again so changes are reflected
        shared_pdf = self.user.profile.current_shared_pdfs.get(name='share')

        for generated_result in [shared_pdf.expiration_date, shared_pdf.deletion_date]:
            expected_result = datetime.now(timezone.utc) + timedelta(days=1, hours=0, minutes=22)

            self.assertTrue((generated_result - expected_result).total_seconds() < 0.1)


class TestOverviewMixin(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.user = None
        self.pdf = None
        set_up(self)

        # create some pdfs
        for i in range(1, 4):
            SharedPdf.objects.create(pdf=self.pdf, name=f'shared_{i}')

        deletion_date = datetime.now(timezone.utc) - timedelta(minutes=5)
        SharedPdf.objects.create(pdf=self.pdf, name='shared_deleted', deletion_date=deletion_date)

    def test_filter_objects(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(f'{reverse('shared_pdf_overview')}?q=pdf_2+%23tag_2')

        # make sure only current shared pdfs are userd
        other_ws = create_workspace('other_ws', creator=self.user)
        other_ws_pdf = Pdf.objects.create(collection=other_ws.collections[0], name='other_ws_pdf')
        SharedPdf.objects.create(pdf=other_ws_pdf, name='other_share')

        filtered_shares = OverviewMixin.filter_objects(response.wsgi_request)
        shared_names = [shared.name for shared in filtered_shares]

        self.assertEqual(shared_names, ['shared_1', 'shared_2', 'shared_3'])

    def test_get_extra_context(self):
        self.client.login(username=self.username, password=self.password)
        response = self.client.get(reverse('shared_pdf_overview'))

        generated_extra_context = share_views.OverviewMixin.get_extra_context(response.wsgi_request)
        expected_extra_context = {
            'page': 'shared_pdf_overview',
            'current_collection_id': str(self.user.id),
            'current_collection_name': 'Default',
            'current_workspace_id': str(self.user.id),
        }

        self.assertEqual(generated_extra_context, expected_extra_context)


class TestSharedPdfMixin(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.user = None
        self.pdf = None
        set_up(self)

    def test_get_object(self):
        self.client.login(username=self.username, password=self.password)
        # we need to create a request so get_pdf can access the user profile
        response = self.client.get(reverse('pdf_overview'))

        # make sure we can access shared pdf of non active
        other_ws = create_workspace('other_ws', creator=self.user)
        other_ws_pdf = Pdf.objects.create(collection=other_ws.collections[0], name='other_ws_pdf')
        other_shared_pdf = SharedPdf.objects.create(pdf=other_ws_pdf, name='other_share')

        self.assertNotEqual(other_ws, self.user.profile.current_workspace)
        self.assertEqual(other_shared_pdf, SharedPdfMixin.get_object(response.wsgi_request, other_shared_pdf.id))


class TestEditSharedPdfMixin(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.user = None
        self.pdf = None
        set_up(self)

    def test_get_edit_form_get(self):
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share', description='some_description', max_views=4)

        edit_pdf_mixin_object = EditSharedPdfMixin()

        for field, form_class, field_value in zip(
            ['name', 'description', 'max_views', 'password', 'expiration_date', 'deletion_date'],
            [
                SharedNameForm,
                SharedDescriptionForm,
                SharedMaxViewsForm,
                SharedPasswordForm,
                SharedExpirationDateForm,
                SharedDeletionDateForm,
            ],
            ['share', 'some_description', 4, '', '', ''],
        ):
            form = edit_pdf_mixin_object.get_edit_form_get(field, shared_pdf)
            self.assertIsInstance(form, form_class)
            self.assertEqual(form.initial, {field: field_value})

    def test_process_field_changed_field(self):
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share')

        EditSharedPdfMixin.process_field('expiration_date', shared_pdf, None, {'expiration_input': '1d0h22m'})
        EditSharedPdfMixin.process_field('deletion_date', shared_pdf, None, {'deletion_input': '1d0h22m'})
        adjusted_shared_pdf = self.user.profile.current_shared_pdfs.get(name='share')

        for generated_result in [adjusted_shared_pdf.expiration_date, adjusted_shared_pdf.deletion_date]:
            expected_result = datetime.now(timezone.utc) + timedelta(days=1, hours=0, minutes=22)

            self.assertTrue((generated_result - expected_result).total_seconds() < 0.1)

    def test_process_field_unchanged_field(self):
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share')

        EditSharedPdfMixin.process_field('other', shared_pdf, None, {})
        adjusted_shared_pdf = self.user.profile.current_shared_pdfs.get(name='share')

        self.assertEqual(shared_pdf, adjusted_shared_pdf)

    def test_process_field_name(self):
        self.client.login(username=self.username, password=self.password)
        # do a dummy request so we can get a request object
        response = self.client.get(reverse('pdf_overview'))
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share')

        EditSharedPdfMixin.process_field('name', shared_pdf, response.wsgi_request, {'name': 'new name '})
        adjusted_shared_pdf = self.user.profile.current_shared_pdfs.get(id=shared_pdf.id)

        # also make sure space was stripped
        self.assertEqual(adjusted_shared_pdf.name, 'new name')

    def test_process_field_name_existing(self):
        self.client.login(username=self.username, password=self.password)
        # do a dummy request so we can get a request object
        response = self.client.get(reverse('pdf_overview'))
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share')
        shared_pdf_2 = SharedPdf.objects.create(pdf=self.pdf, name='shared_2')
        request = response.wsgi_request

        EditSharedPdfMixin.process_field('name', shared_pdf, request, {'name': shared_pdf_2.name})

        messages = get_messages(request)

        self.assertEqual(len(messages), 1)
        self.assertEqual(list(messages)[0].message, 'This name is already used by another shared PDF!')
        changed_shared_pdf = SharedPdf.objects.get(id=shared_pdf.id)
        self.assertEqual(changed_shared_pdf.name, 'share')


class TestPdfPublicMixin(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.user = None
        self.pdf = None
        set_up(self)

    @patch('pdf.services.shared_pdf_services.check_shared_access_allowed', return_value=True)
    def test_get_object(self, mock_check):
        # get dummy request
        response = self.client.get(reverse('pdf_overview'))
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share')

        self.assertEqual(shared_pdf.pdf, PdfPublicMixin.get_object(response.wsgi_request, shared_pdf.id))

    @patch('pdf.services.shared_pdf_services.check_shared_access_allowed', return_value=False)
    def test_get_object_404(self, mock_check):
        # get dummy request
        response = self.client.get(reverse('pdf_overview'))
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share')

        with pytest.raises(Http404, match='Access to shared pdf not allowed!'):
            PdfPublicMixin.get_object(response.wsgi_request, shared_pdf.id)


class TestBaseSharedPdfPublicView(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.user = None
        self.pdf = None
        set_up(self)

    def test_get_object(self):
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='share')

        self.assertEqual(shared_pdf, BaseSharedPdfPublicView.get_shared_pdf_public(None, shared_pdf.id))


class TestViewSharedPdf(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.user = None
        self.pdf = None
        set_up(self)
        self.shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='shared_pdf')

    @patch('pdf.views.share_views.check_shared_access_allowed', return_value=False)
    def test_view_get_active_no_active_session(self, mock_check):
        # test without http referer
        response = self.client.get(reverse('view_shared_pdf', kwargs={'identifier': self.shared_pdf.id}))

        self.assertTemplateUsed(response, 'view_shared_info.html')
        self.assertEqual(response.context['shared_pdf'], self.shared_pdf)
        self.assertEqual(response.context['host'], 'testserver')
        self.assertEqual(response.context['form'], ViewSharedPasswordForm)

    @patch('pdf.views.share_views.get_viewer_theme_and_color')
    @patch('pdf.views.share_views.check_shared_access_allowed', return_value=True)
    def test_view_get_active_active_session(self, mock_check, mock_get_viewer_theme_and_color):
        # test without http referer

        mock_get_viewer_theme_and_color.return_value = 'dark', '4 4 4'
        self.shared_pdf.pdf.revision = 2
        self.shared_pdf.pdf.save()
        self.assertEqual(self.shared_pdf.views, 0)

        response = self.client.get(reverse('view_shared_pdf', kwargs={'identifier': self.shared_pdf.id}))
        self.assertEqual(response.context['shared_pdf_id'], self.shared_pdf.id)
        self.assertEqual(response.context['current_page'], 1)
        self.assertEqual(response.context['revision'], 2)
        self.assertEqual(response.context['theme_color'], '4 4 4')
        self.assertEqual(response.context['theme'], 'dark')
        self.assertEqual(response.context['tab_title'], 'PdfDing')
        self.assertEqual(response.context['user_view_bool'], False)
        self.assertTemplateUsed(response, 'viewer.html')

        # rendering the viewer for an already granted session must not consume a view; views are only counted
        # once, when a session is granted access in the POST handler.
        shared_pdf = SharedPdf.objects.get(pk=self.shared_pdf.id)
        self.assertEqual(shared_pdf.views, 0)

    def test_view_get_inactive(self):
        inactive_shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='inactive_shared_pdf', views=2, max_views=1)
        response = self.client.get(reverse('view_shared_pdf', kwargs={'identifier': inactive_shared_pdf.id}))

        self.assertTemplateUsed(response, 'view_shared_inactive.html')

    def test_view_post_active_no_password(self):
        unprotected_shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='unprotected_shared_pdf')
        assert unprotected_shared_pdf.sessions.count() == 0

        response = self.client.post(reverse('view_shared_pdf', kwargs={'identifier': unprotected_shared_pdf.id}))

        assert unprotected_shared_pdf.sessions.count() == 1
        self.assertRedirects(response, reverse('view_shared_pdf', kwargs={'identifier': unprotected_shared_pdf.id}))

    def test_view_post_active_wrong_password(self):
        protected_shared_pdf = SharedPdf.objects.create(
            pdf=self.pdf, name='protected_shared_pdf', password=make_password('some_pw')
        )

        response = self.client.post(
            reverse('view_shared_pdf', kwargs={'identifier': protected_shared_pdf.id}), data={'password_input': 'wrong'}
        )

        self.assertIsInstance(response.context['form'], ViewSharedPasswordForm)
        self.assertTemplateUsed(response, 'view_shared_info.html')

    def test_view_post_active_correct_password(self):
        protected_shared_pdf = SharedPdf.objects.create(
            pdf=self.pdf, name='protected_shared_pdf', password=make_password('some_pw')
        )
        assert protected_shared_pdf.sessions.count() == 0

        response = self.client.post(
            reverse('view_shared_pdf', kwargs={'identifier': protected_shared_pdf.id}),
            data={'password_input': 'some_pw'},
        )

        assert protected_shared_pdf.sessions.count() == 1
        self.assertRedirects(response, reverse('view_shared_pdf', kwargs={'identifier': protected_shared_pdf.id}))

    def test_view_post_inactive(self):
        inactive_shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='inactive_shared_pdf', views=2, max_views=1)
        response = self.client.post(reverse('view_shared_pdf', kwargs={'identifier': inactive_shared_pdf.id}))

        self.assertTemplateUsed(response, 'view_shared_inactive.html')

    def test_view_post_deleted(self):
        deleted_shared_pdf = SharedPdf.objects.create(
            pdf=self.pdf, name='inactive_shared_pdf', deletion_date=(datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        response = self.client.post(reverse('view_shared_pdf', kwargs={'identifier': deleted_shared_pdf.id}))

        self.assertTemplateUsed(response, 'view_shared_inactive.html')

    def test_view_max_views_one_allows_complete_first_view(self):
        # regression: with max views set to one, entering the viewer used to consume the only view, so afterwards
        # loading the actual pdf file and refreshing the page were denied. The first viewing session must be able
        # to read the file completely and refresh without being locked out.
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='limited_shared_pdf', max_views=1)
        url = reverse('view_shared_pdf', kwargs={'identifier': shared_pdf.id})

        # granting access (no password) counts as the single view
        self.client.post(url)
        shared_pdf.refresh_from_db()
        self.assertEqual(shared_pdf.views, 1)
        self.assertEqual(shared_pdf.sessions.count(), 1)

        # the granted session still sees the viewer (not the inactive page) ...
        response = self.client.get(url)
        self.assertTemplateUsed(response, 'viewer.html')
        # ... and is still allowed to load the actual pdf file
        self.assertEqual(PdfPublicMixin.get_object(response.wsgi_request, shared_pdf.id), self.pdf)

        # refreshing the viewer keeps working and does not consume additional views
        response = self.client.get(url)
        self.assertTemplateUsed(response, 'viewer.html')
        self.assertEqual(PdfPublicMixin.get_object(response.wsgi_request, shared_pdf.id), self.pdf)

        shared_pdf.refresh_from_db()
        self.assertEqual(shared_pdf.views, 1)

    def test_view_max_views_one_blocks_new_session(self):
        # regression: the max views limit must still block a *new* viewer once it has been reached.
        shared_pdf = SharedPdf.objects.create(pdf=self.pdf, name='limited_shared_pdf', max_views=1)
        url = reverse('view_shared_pdf', kwargs={'identifier': shared_pdf.id})

        # the first session uses up the single view
        self.client.post(url)

        # a different session is not allowed in anymore, neither via GET nor POST
        other_client = Client()
        self.assertTemplateUsed(other_client.get(url), 'view_shared_inactive.html')
        self.assertTemplateUsed(other_client.post(url), 'view_shared_inactive.html')

        shared_pdf.refresh_from_db()
        self.assertEqual(shared_pdf.views, 1)

    def test_view_password_share_consistent_for_session(self):
        # regression: a password protected, max-views limited share must behave consistently for the same session.
        # Once the password is accepted the session can load the file and refresh, and re-submitting the form does
        # not consume another view.
        shared_pdf = SharedPdf.objects.create(
            pdf=self.pdf, name='protected_limited', password=make_password('some_pw'), max_views=1
        )
        url = reverse('view_shared_pdf', kwargs={'identifier': shared_pdf.id})

        # a wrong password keeps the session out and does not consume a view
        response = self.client.post(url, data={'password_input': 'wrong'})
        self.assertTemplateUsed(response, 'view_shared_info.html')
        shared_pdf.refresh_from_db()
        self.assertEqual(shared_pdf.views, 0)
        self.assertEqual(shared_pdf.sessions.count(), 0)

        # the correct password grants access and counts a single view
        self.client.post(url, data={'password_input': 'some_pw'})
        shared_pdf.refresh_from_db()
        self.assertEqual(shared_pdf.views, 1)
        self.assertEqual(shared_pdf.sessions.count(), 1)

        # the same session can now view and load the file even though the view limit is reached
        response = self.client.get(url)
        self.assertTemplateUsed(response, 'viewer.html')
        self.assertEqual(PdfPublicMixin.get_object(response.wsgi_request, shared_pdf.id), self.pdf)

        # re-submitting the form for the already granted session just redirects and does not consume another view
        response = self.client.post(url)
        self.assertRedirects(response, url)
        shared_pdf.refresh_from_db()
        self.assertEqual(shared_pdf.views, 1)
