from allauth.mfa.models import Authenticator
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from pdf.models.collection_models import Collection
from pdf.models.pdf_models import Pdf
from pdf.models.shared_pdf_models import SharedPdf
from pdf.models.workspace_models import WorkspaceRoles, WorkspaceUser
from pdf.services.workspace_services import create_collection, create_workspace


class TestProfile(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username='user', password='12345', email='a@a.com')

    # override_settings is not working with this test as the models default value is not overwritten
    # therefore do not change the theme defined in dev.py
    @override_settings(DEFAULT_THEME='dark', DEFAULT_THEME_COLOR='Red')
    def test_default_theme(self):
        user = User.objects.create_user(username='bla', password='12345', email='bla@a.com')

        self.assertEqual(user.profile.dark_mode, 'Dark')
        self.assertEqual(user.profile.theme_color, 'Red')

    def test_dark_mode_str(self):
        self.user.profile.dark_mode = 'Dark'
        self.user.profile.save()

        self.assertEqual(self.user.profile.dark_mode_str, 'dark')

    def test_workspace_property(self):
        create_workspace('some_workspace', self.user)

        self.assertEqual(self.user.profile.workspaces.count(), 2)

        for workspace, expected_name in zip(
            self.user.profile.workspaces.order_by('name'), ['Personal', 'some_workspace']
        ):
            self.assertEqual(workspace.name, expected_name)

    def test_collection_property(self):
        self.assertEqual(self.user.profile.collections.count(), 1)

        workspace = create_workspace('some_workspace', self.user)
        Collection.objects.create(workspace=workspace, name='some_collection', default_collection=False)
        self.user.profile.current_workspace_id = workspace.id
        self.user.profile.save()

        self.assertEqual(self.user.profile.collections.count(), 2)

        for collection, expected_name in zip(
            self.user.profile.collections.order_by('name'), ['Default', 'some_collection']
        ):
            self.assertEqual(collection.name, expected_name)

    def test_all_pdfs_property(self):
        collection = self.user.profile.current_collection
        other_workspace = create_workspace('other_ws', self.user)
        other_collection = create_collection(other_workspace, 'other')

        pdf_1 = Pdf.objects.create(name='pdf_1', collection=collection)
        pdf_2 = Pdf.objects.create(name='pdf_2', collection=collection)
        pdf_3 = Pdf.objects.create(name='pdf_3', collection=other_collection)

        self.assertEqual(self.user.profile.all_pdfs.count(), 3)

        for pdf_a, pdf_b in zip(self.user.profile.all_pdfs.order_by('name'), [pdf_1, pdf_2, pdf_3]):
            self.assertEqual(pdf_a, pdf_b)

    def test_current_pdfs_property(self):
        ws = self.user.profile.current_workspace
        collection = self.user.profile.current_collection
        other_workspace = create_workspace('other_ws', self.user)
        other_collection = create_collection(ws, 'other')
        other_collection_other_ws = create_collection(other_workspace, 'other')

        pdf_1 = Pdf.objects.create(name='pdf_1', collection=collection)
        pdf_2 = Pdf.objects.create(name='pdf_2', collection=collection)
        Pdf.objects.create(name='pdf_3', collection=other_collection)
        Pdf.objects.create(name='pdf_4', collection=other_collection_other_ws)

        self.assertEqual(self.user.profile.current_pdfs.count(), 2)

        for pdf_a, pdf_b in zip(self.user.profile.current_pdfs.order_by('name'), [pdf_1, pdf_2]):
            self.assertEqual(pdf_a, pdf_b)

    def test_current_pdfs_property_all(self):
        ws = self.user.profile.current_workspace
        collection = self.user.profile.current_collection
        other_workspace = create_workspace('other_ws', self.user)
        other_collection = create_collection(ws, 'other')
        other_collection_other_ws = create_collection(other_workspace, 'other')

        self.user.profile.current_collection_id = 'all'
        self.user.profile.save()

        pdf_1 = Pdf.objects.create(name='pdf_1', collection=collection)
        pdf_2 = Pdf.objects.create(name='pdf_2', collection=other_collection)
        Pdf.objects.create(name='pdf_3', collection=other_collection_other_ws)

        self.assertEqual(self.user.profile.current_pdfs.count(), 2)

        for pdf_a, pdf_b in zip(self.user.profile.current_pdfs.order_by('name'), [pdf_1, pdf_2]):
            self.assertEqual(pdf_a, pdf_b)

    def test_all_shared_pdfs_property(self):
        collection = self.user.profile.current_collection
        other_workspace = create_workspace('other_ws', self.user)
        other_collection = create_collection(other_workspace, 'other')

        pdf_1 = Pdf.objects.create(name='pdf_1', collection=collection)
        pdf_2 = Pdf.objects.create(name='pdf_2', collection=other_collection)

        shared_pdf_1 = SharedPdf.objects.create(pdf=pdf_1, name='shared_pdf_1')
        shared_pdf_2 = SharedPdf.objects.create(pdf=pdf_2, name='shared_pdf_2')

        self.assertEqual(self.user.profile.all_shared_pdfs.count(), 2)

        for shared_pdf_a, shared_pdf_b in zip(
            self.user.profile.all_shared_pdfs.order_by('name'), [shared_pdf_1, shared_pdf_2]
        ):
            self.assertEqual(shared_pdf_a, shared_pdf_b)

    def test_shared_pdfs_property(self):
        collection = self.user.profile.current_collection
        other_workspace = create_workspace('other_ws', self.user)
        other_collection = create_collection(other_workspace, 'other')

        pdf_1 = Pdf.objects.create(name='pdf_1', collection=collection)
        pdf_2 = Pdf.objects.create(name='pdf_2', collection=other_collection)

        shared_pdf_1 = SharedPdf.objects.create(pdf=pdf_1, name='shared_pdf_1')
        SharedPdf.objects.create(pdf=pdf_2, name='shared_pdf_2')

        self.assertEqual(self.user.profile.current_shared_pdfs.count(), 1)

        for shared_pdf_a, shared_pdf_b in zip(self.user.profile.current_shared_pdfs.order_by('name'), [shared_pdf_1]):
            self.assertEqual(shared_pdf_a, shared_pdf_b)

    def test_current_workspace_property(self):
        self.assertEqual(self.user.profile.current_workspace.id, str(self.user.id))

        other_workspace = create_workspace('other_workspace', self.user)
        self.user.profile.current_workspace_id = other_workspace.id
        self.user.profile.save()

        self.assertEqual(self.user.profile.current_workspace, other_workspace)

    def test_current_collection_property(self):
        other_collection = create_collection(self.user.profile.current_workspace, 'other')
        self.user.profile.current_collection_id = other_collection.id
        self.user.profile.save()

        self.assertEqual(other_collection, self.user.profile.current_collection)

    def test_current_collection_name_property(self):
        other_collection = create_collection(self.user.profile.current_workspace, 'other')
        self.user.profile.current_collection_id = other_collection.id
        self.user.profile.save()

        self.assertEqual('other', self.user.profile.current_collection_name)

    def test_current_collection_name_all_property(self):
        self.user.profile.current_collection_id = 'all'
        self.user.profile.save()

        self.assertEqual('All', self.user.profile.current_collection_name)

    def test_mfa_activated(self):
        Authenticator.objects.create(user=self.user, type='totp', data={})

        assert self.user.profile.mfa_activated

    def test_mfa_deactivated(self):
        assert not self.user.profile.mfa_activated

    def test_has_access_to_workspace(self):
        profile = self.user.profile
        other_workspace = create_workspace('other_workspace', self.user)
        other_user = User.objects.create_user(username='other_user', password='12345', email='a@aa.com')

        self.assertTrue(profile.has_access_to_workspace(other_workspace.id))
        self.assertFalse(profile.has_access_to_workspace(other_user.profile.current_workspace_id))

    def test_sanitize_state_no_change_needed(self):
        """When state is valid, sanitize_state returns False and makes no DB writes."""

        profile = self.user.profile
        result = profile.sanitize_state()

        self.assertFalse(result)
        self.assertEqual(profile.current_workspace_id, str(self.user.id))

    def test_sanitize_state_stale_workspace(self):
        """When workspace doesn't exist, reset to personal workspace and 'all'."""

        profile = self.user.profile
        profile.current_workspace_id = '00000000-0000-0000-0000-000000000001'
        profile.current_collection_id = '00000000-0000-0000-0000-000000000002'
        profile.save()

        result = profile.sanitize_state()

        self.assertTrue(result)
        profile.refresh_from_db()
        self.assertEqual(profile.current_workspace_id, str(self.user.id))
        self.assertEqual(profile.current_collection_id, 'all')

    def test_sanitize_state_workspace_user_lost_access(self):
        """When user was removed from workspace, reset to personal workspace."""

        other_user = User.objects.create_user(username='other', password='12345', email='b@b.com')
        shared_ws = create_workspace('shared', other_user)
        WorkspaceUser.objects.create(workspace=shared_ws, user=self.user, role=WorkspaceRoles.MEMBER)

        profile = self.user.profile
        profile.current_workspace_id = shared_ws.id
        profile.current_collection_id = 'all'
        profile.save()

        # Remove the user from the workspace
        WorkspaceUser.objects.filter(workspace=shared_ws, user=self.user).delete()

        result = profile.sanitize_state()

        self.assertTrue(result)
        profile.refresh_from_db()
        self.assertEqual(profile.current_workspace_id, str(self.user.id))
        self.assertEqual(profile.current_collection_id, 'all')

    def test_sanitize_state_stale_collection(self):
        """When collection doesn't exist in current workspace, reset to 'all'."""

        workspace = create_workspace('test_ws', self.user)
        collection = create_collection(workspace, 'temp_collection')

        profile = self.user.profile
        profile.current_workspace_id = workspace.id
        profile.current_collection_id = collection.id
        profile.save()

        # Delete the collection
        collection.delete()

        result = profile.sanitize_state()

        self.assertTrue(result)
        profile.refresh_from_db()
        self.assertEqual(profile.current_workspace_id, workspace.id)
        self.assertEqual(profile.current_collection_id, 'all')

    def test_sanitize_state_collection_all_unchanged(self):
        """When collection_id is 'all', skip collection validation."""

        workspace = create_workspace('test_ws', self.user)

        profile = self.user.profile
        profile.current_workspace_id = workspace.id
        profile.current_collection_id = 'all'
        profile.save()

        result = profile.sanitize_state()

        self.assertFalse(result)

    def test_sanitize_state_collection_from_other_workspace(self):
        """When collection belongs to a different workspace, reset to 'all'."""

        ws1 = create_workspace('ws1', self.user)
        ws2 = create_workspace('ws2', self.user)
        collection_in_ws2 = create_collection(ws2, 'ws2_collection')

        profile = self.user.profile
        profile.current_workspace_id = ws1.id
        profile.current_collection_id = collection_in_ws2.id
        profile.save()

        result = profile.sanitize_state()

        self.assertTrue(result)
        profile.refresh_from_db()
        self.assertEqual(profile.current_workspace_id, ws1.id)
        self.assertEqual(profile.current_collection_id, 'all')
