from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from pdf.models.pdf_models import Pdf
from pdf.models.tag_models import Tag
from pdf.services.workspace_services import create_collection, create_workspace


class BulkActionTestCase(TestCase):
    username = 'user'
    password = '12345'

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username=self.username, password=self.password, email='a@a.com')
        self.client.login(username=self.username, password=self.password)

        collection = self.user.profile.current_collection

        self.pdf_1 = Pdf.objects.create(name='pdf_1', collection=collection)
        self.pdf_2 = Pdf.objects.create(name='pdf_2', collection=collection)
        self.pdf_3 = Pdf.objects.create(name='pdf_3', collection=collection)

    def test_empty_list(self):
        response = self.client.post(
            reverse('bulk_actions'),
            data={'selected_bulk_action': 'archive', 'bulk_selected_pdfs': ''},
        )

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

    def test_not_existing_pdf(self):
        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'archive',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), '123']),
            },
        )

        assert response.status_code == 404

    def test_archive(self):
        for pdf in [self.pdf_1, self.pdf_2, self.pdf_3]:
            assert not pdf.archived

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'archive',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(self.pdf_2.id)]),
            },
        )

        for pdf, expected_result in zip([self.pdf_1, self.pdf_2, self.pdf_3], [True, True, False]):
            # we need to update the pdf
            changed_pdf = Pdf.objects.get(id=pdf.id)
            assert changed_pdf.archived == expected_result

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

    def test_delete(self):
        assert Pdf.objects.filter(id=self.pdf_1.id).exists()
        assert Pdf.objects.filter(id=self.pdf_2.id).exists()

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'delete',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(self.pdf_2.id)]),
                'delete_confirmation': 'yes',
            },
        )

        assert not Pdf.objects.filter(id=self.pdf_1.id).exists()
        assert not Pdf.objects.filter(id=self.pdf_2.id).exists()

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

    def test_delete_no_confirmation(self):
        assert Pdf.objects.filter(id=self.pdf_1.id).exists()
        assert Pdf.objects.filter(id=self.pdf_2.id).exists()

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'delete',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(self.pdf_2.id)]),
                'delete_confirmation': 'no',
            },
        )

        assert Pdf.objects.filter(id=self.pdf_1.id).exists()
        assert Pdf.objects.filter(id=self.pdf_2.id).exists()

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

    @mock.patch('pdf.views.pdf_bulk_action_views.change_collection_of_pdf')
    def test_set_collection(self, mock_change_collection):
        for pdf in [self.pdf_1, self.pdf_2, self.pdf_3]:
            assert pdf.collection == self.user.profile.current_collection

        other_collection = create_collection(workspace=self.user.profile.current_workspace, collection_name='other')

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'set_collection',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(self.pdf_2.id)]),
                'collection_id': other_collection.id,
            },
        )

        assert mock_change_collection.call_count == 2
        mock_change_collection.assert_has_calls(
            [
                mock.call(self.pdf_1, other_collection.id),
                mock.call(self.pdf_2, other_collection.id),
            ],
            any_order=True,
        )

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

    @mock.patch('pdf.views.pdf_bulk_action_views.check_if_collection_part_of_workspace', return_value=False)
    def test_set_collection_wrong_workspace(self, mock_check_if_collection_part_of_workspace):
        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'set_collection',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(self.pdf_2.id)]),
                'collection_id': '123',
            },
        )

        assert response.status_code == 404

    def test_set_tags(self):
        tag_pdf_1 = Tag.objects.create(name='tag', workspace=self.user.profile.current_workspace)
        self.pdf_1.tags.set([tag_pdf_1])

        assert self.pdf_1.tags.count() == 1
        assert not self.pdf_2.tags.count()
        assert not self.pdf_3.tags.count()

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'set_tags',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(self.pdf_2.id)]),
                'tag_string': 'one two',
            },
        )

        assert self.pdf_1.tags.count() == 2
        assert self.pdf_2.tags.count() == 2
        assert not self.pdf_3.tags.count()

        for pdf in [self.pdf_1, self.pdf_2]:
            for tag, tag_name in zip(pdf.tags.order_by('name'), ['one', 'two']):
                assert tag.name == tag_name
                assert tag.workspace == self.user.profile.current_workspace

        assert not Tag.objects.filter(id=tag_pdf_1.id).exists()

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

    def test_set_tags_wrong_char(self):
        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'set_tags',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(self.pdf_2.id)]),
                'tag_string': 'one% two',
            },
            follow=True,
        )

        message = list(response.context['messages'])[0]

        self.assertEqual(message.message, 'Only letters, numbers, "/", "-" and "_" are valid characters!')
        self.assertEqual(message.tags, 'warning')

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

    def test_star(self):
        for pdf in [self.pdf_1, self.pdf_2, self.pdf_3]:
            assert not pdf.starred

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'star',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(self.pdf_2.id)]),
            },
        )

        for pdf, expected_result in zip([self.pdf_1, self.pdf_2, self.pdf_3], [True, True, False]):
            # we need to update the pdf
            changed_pdf = Pdf.objects.get(id=pdf.id)
            assert changed_pdf.starred == expected_result

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

    def test_set_collection_cross_workspace_rejected(self):
        """Regression: set_collection must reject PDFs that do not belong to the current workspace."""

        # Create a second workspace and a PDF that lives in it
        other_workspace = create_workspace('other_ws', self.user)
        other_collection = other_workspace.collections.first()
        foreign_pdf = Pdf.objects.create(name='foreign_pdf', collection=other_collection)

        # Current workspace still has the original collection; pick a target collection inside it
        target_collection = create_collection(
            workspace=self.user.profile.current_workspace, collection_name='target'
        )

        # Mixing a local PDF with a foreign one must be rejected
        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'set_collection',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(foreign_pdf.id)]),
                'collection_id': target_collection.id,
            },
        )

        assert response.status_code == 404

        # The local PDF must NOT have been moved
        self.pdf_1.refresh_from_db()
        assert self.pdf_1.collection == self.user.profile.current_collection

        # The foreign PDF must NOT have been moved either
        foreign_pdf.refresh_from_db()
        assert foreign_pdf.collection == other_collection

    def test_set_collection_only_foreign_pdf_rejected(self):
        """Regression: set_collection must reject a request that contains only foreign PDFs."""

        other_workspace = create_workspace('other_ws', self.user)
        other_collection = other_workspace.collections.first()
        foreign_pdf = Pdf.objects.create(name='foreign_pdf', collection=other_collection)

        target_collection = create_collection(
            workspace=self.user.profile.current_workspace, collection_name='target'
        )

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'set_collection',
                'bulk_selected_pdfs': str(foreign_pdf.id),
                'collection_id': target_collection.id,
            },
        )

        assert response.status_code == 404

        foreign_pdf.refresh_from_db()
        assert foreign_pdf.collection == other_collection

    def test_set_tags_cross_workspace_rejected(self):
        """Regression: set_tags must reject PDFs that do not belong to the current workspace."""

        other_workspace = create_workspace('other_ws', self.user)
        other_collection = other_workspace.collections.first()
        foreign_pdf = Pdf.objects.create(name='foreign_pdf', collection=other_collection)

        original_local_tags = list(self.pdf_1.tags.all())
        original_foreign_tags = list(foreign_pdf.tags.all())

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'set_tags',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(foreign_pdf.id)]),
                'tag_string': 'one two',
            },
        )

        assert response.status_code == 404

        # Neither PDF should have had its tags changed
        self.pdf_1.refresh_from_db()
        foreign_pdf.refresh_from_db()
        assert list(self.pdf_1.tags.all()) == original_local_tags
        assert list(foreign_pdf.tags.all()) == original_foreign_tags

    def test_set_tags_only_foreign_pdf_rejected(self):
        """Regression: set_tags must reject a request that contains only foreign PDFs."""

        other_workspace = create_workspace('other_ws', self.user)
        other_collection = other_workspace.collections.first()
        foreign_pdf = Pdf.objects.create(name='foreign_pdf', collection=other_collection)

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'set_tags',
                'bulk_selected_pdfs': str(foreign_pdf.id),
                'tag_string': 'one two',
            },
        )

        assert response.status_code == 404
        assert foreign_pdf.tags.count() == 0

    def test_archive_cross_workspace_still_works(self):
        """Archive must continue to work even if the selection spans multiple workspaces."""

        other_workspace = create_workspace('other_ws', self.user)
        other_collection = other_workspace.collections.first()
        foreign_pdf = Pdf.objects.create(name='foreign_pdf', collection=other_collection)

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'archive',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(foreign_pdf.id)]),
            },
        )

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

        self.pdf_1.refresh_from_db()
        foreign_pdf.refresh_from_db()
        assert self.pdf_1.archived is True
        assert foreign_pdf.archived is True

    def test_delete_cross_workspace_still_works(self):
        """Delete must continue to work even if the selection spans multiple workspaces."""

        other_workspace = create_workspace('other_ws', self.user)
        other_collection = other_workspace.collections.first()
        foreign_pdf = Pdf.objects.create(name='foreign_pdf', collection=other_collection)

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'delete',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(foreign_pdf.id)]),
                'delete_confirmation': 'yes',
            },
        )

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)
        assert not Pdf.objects.filter(id=self.pdf_1.id).exists()
        assert not Pdf.objects.filter(id=foreign_pdf.id).exists()

    def test_star_cross_workspace_still_works(self):
        """Star must continue to work even if the selection spans multiple workspaces."""

        other_workspace = create_workspace('other_ws', self.user)
        other_collection = other_workspace.collections.first()
        foreign_pdf = Pdf.objects.create(name='foreign_pdf', collection=other_collection)

        response = self.client.post(
            reverse('bulk_actions'),
            data={
                'selected_bulk_action': 'star',
                'bulk_selected_pdfs': ','.join([str(self.pdf_1.id), str(foreign_pdf.id)]),
            },
        )

        self.assertRedirects(response, reverse('pdf_overview'), status_code=302)

        self.pdf_1.refresh_from_db()
        foreign_pdf.refresh_from_db()
        assert self.pdf_1.starred is True
        assert foreign_pdf.starred is True
