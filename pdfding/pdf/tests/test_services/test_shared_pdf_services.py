from datetime import datetime, timedelta, timezone
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.urls import reverse
from pdf.models.pdf_models import Pdf
from pdf.models.shared_pdf_models import SharedPdf
from pdf.services.shared_pdf_services import (
    check_shared_access_allowed,
    check_shared_access_allowed_by_identifier,
    construct_shared_query_overview_url,
    get_future_datetime,
)


class TestSharedPdfServices(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='username', password='password', email='a@a.com')

    def test_check_shared_access_allowed(self):
        # get dummy request
        response = self.client.get(reverse('pdf_overview'))
        request = response.wsgi_request

        # create dummy session
        request.session.create()
        pdf = Pdf.objects.create(name='bla', collection_id=self.user.id)
        shared_pdf = SharedPdf.objects.create(pdf=pdf, name='share')

        assert not check_shared_access_allowed(shared_pdf, request.session)

        shared_pdf.sessions.add(Session.objects.get(session_key=request.session.session_key))
        assert check_shared_access_allowed(shared_pdf, request.session)

        request.session.set_expiry(-1)
        assert not check_shared_access_allowed(shared_pdf, request.session)

    def test_check_shared_access_allowed_inactive(self):
        # get dummy request
        response = self.client.get(reverse('pdf_overview'))
        request = response.wsgi_request

        # create dummy session
        request.session.create()
        pdf = Pdf.objects.create(name='bla', collection_id=self.user.id)

        inactive_shared_pdf = SharedPdf.objects.create(
            pdf=pdf, name='inactive_shared_pdf', expiration_date=(datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        inactive_shared_pdf.sessions.add(Session.objects.get(session_key=request.session.session_key))

        assert not check_shared_access_allowed(inactive_shared_pdf, request.session)

    def test_check_shared_access_allowed_deleted(self):
        # get dummy request
        response = self.client.get(reverse('pdf_overview'))
        request = response.wsgi_request

        # create dummy session
        request.session.create()
        pdf = Pdf.objects.create(name='bla', collection_id=self.user.id)

        deleted_shared_pdf = SharedPdf.objects.create(
            pdf=pdf, name='inactive_shared_pdf', deletion_date=(datetime.now(timezone.utc) - timedelta(minutes=5))
        )
        deleted_shared_pdf.sessions.add(Session.objects.get(session_key=request.session.session_key))

        assert not check_shared_access_allowed(deleted_shared_pdf, request.session)

    @mock.patch('pdf.services.shared_pdf_services.check_shared_access_allowed')
    def test_check_shared_access_allowed_by_identifier(self, mock_check):
        # get dummy request
        response = self.client.get(reverse('pdf_overview'))
        request = response.wsgi_request

        # create dummy session
        request.session.create()
        pdf = Pdf.objects.create(name='bla', collection_id=self.user.id)
        shared_pdf = SharedPdf.objects.create(pdf=pdf, name='share')

        check_shared_access_allowed_by_identifier(shared_pdf.id, request.session)
        mock_check.assert_called_once_with(shared_pdf, request.session)

    def test_get_future_datetime(self):
        expected_result = datetime.now(timezone.utc) + timedelta(days=1, hours=0, minutes=22)
        generated_result = get_future_datetime('1d0h22m')

        self.assertTrue((generated_result - expected_result).total_seconds() < 0.1)

    def test_get_future_datetime_empty(self):
        self.assertEqual(get_future_datetime(''), None)


class TestConstructSharedQueryOverviewUrl(TestCase):
    def setUp(self):
        self.overview_url = reverse('shared_pdf_overview')

    def test_no_changes_empty_referer(self):
        generated_url = construct_shared_query_overview_url(self.overview_url, None, None, None)

        self.assertEqual(generated_url, self.overview_url)

    def test_set_search(self):
        # multiple words are joined with '+'
        generated_url = construct_shared_query_overview_url(self.overview_url, 'foo bar', None, None)

        self.assertEqual(generated_url, f'{self.overview_url}?search=foo+bar')

    def test_set_expiration(self):
        generated_url = construct_shared_query_overview_url(self.overview_url, None, 'active', None)

        self.assertEqual(generated_url, f'{self.overview_url}?expiration=active')

    def test_set_password(self):
        generated_url = construct_shared_query_overview_url(self.overview_url, None, None, 'yes')

        self.assertEqual(generated_url, f'{self.overview_url}?password=yes')

    def test_filters_are_combinable(self):
        # a status filter is added without dropping the existing search
        referer_url = f'{self.overview_url}?search=foo'
        generated_url = construct_shared_query_overview_url(referer_url, None, 'expired', None)

        self.assertEqual(generated_url, f'{self.overview_url}?search=foo&expiration=expired')

    def test_filters_stack(self):
        # a third filter is added on top of an already combined search + expiration filter
        referer_url = f'{self.overview_url}?search=foo&expiration=expired'
        generated_url = construct_shared_query_overview_url(referer_url, None, None, 'yes')

        self.assertEqual(generated_url, f'{self.overview_url}?search=foo&expiration=expired&password=yes')

    def test_none_keeps_existing_filters(self):
        referer_url = f'{self.overview_url}?search=foo&expiration=active&password=yes'
        generated_url = construct_shared_query_overview_url(referer_url, None, None, None)

        self.assertEqual(generated_url, referer_url)

    def test_empty_search_resets_search_only(self):
        referer_url = f'{self.overview_url}?search=foo&password=no'
        generated_url = construct_shared_query_overview_url(referer_url, '', None, None)

        self.assertEqual(generated_url, f'{self.overview_url}?password=no')

    def test_empty_expiration_resets_expiration_only(self):
        referer_url = f'{self.overview_url}?expiration=active&password=yes'
        generated_url = construct_shared_query_overview_url(referer_url, None, '', None)

        self.assertEqual(generated_url, f'{self.overview_url}?password=yes')

    def test_invalid_status_value_resets_filter(self):
        referer_url = f'{self.overview_url}?expiration=active'
        generated_url = construct_shared_query_overview_url(referer_url, None, 'garbage', None)

        self.assertEqual(generated_url, self.overview_url)
