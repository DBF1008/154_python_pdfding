import filecmp
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

import pdf.services.pdf_services as service
from core.settings import MEDIA_ROOT
from django.contrib.auth.models import User
from django.core.files import File
from django.db.models.functions import Lower
from django.http.response import Http404
from django.test import TestCase
from django.urls import reverse
from pdf.models.pdf_models import Pdf, PdfComment, PdfHighlight
from pdf.services.pdf_services import parse_pdf_date
from PIL import Image
from pypdfium2 import PdfDocument
from users.service import get_demo_pdf


class TestPdfProcessingServices(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='username', password='password', email='a@a.com')

    def test_create_pdf(self):
        pdf_name = 'some_pdf'
        pdf_file = get_demo_pdf()
        tag_string = 'some tags'
        description = 'some description'
        file_directory = 'some/dir'
        collection = self.user.profile.current_collection

        pdf = service.PdfProcessingServices.create_pdf(
            name=pdf_name,
            collection=collection,
            pdf_file=pdf_file,
            tag_string=tag_string,
            description=description,
            file_directory=file_directory,
        )

        self.assertEqual(pdf.name, pdf_name)
        self.assertEqual(pdf.collection, collection)
        self.assertEqual(pdf.description, description)
        self.assertEqual(pdf.file_directory, file_directory)
        self.assertEqual(pdf.notes, '')
        self.assertEqual(pdf.number_of_pages, 5)
        self.assertTrue(pdf.preview)
        self.assertTrue(pdf.thumbnail)
        self.assertEqual(pdf.pdfcomment_set.count(), 2)
        self.assertEqual(pdf.pdfhighlight_set.count(), 2)

        for tag, expected_tag_name in zip(pdf.tags.all().order_by('name'), tag_string.split(' ')):
            self.assertEqual(tag.name, expected_tag_name)

    @mock.patch('pdf.services.pdf_services.PdfProcessingServices.set_thumbnail_and_preview')
    def test_set_process_with_pypdfium_no_images(self, mock_set_thumbnail_and_preview):
        pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name='pdf_1')
        self.assertEqual(pdf.number_of_pages, -1)

        dummy_path = Path(__file__).parents[1] / 'data' / 'dummy.pdf'
        with dummy_path.open(mode="rb") as f:
            pdf.file = File(f, name=dummy_path.name)
            pdf.save()

        service.PdfProcessingServices.process_with_pypdfium(pdf, False)

        pdf = self.user.profile.current_pdfs.get(name=pdf.name)
        self.assertEqual(pdf.number_of_pages, 2)
        mock_set_thumbnail_and_preview.assert_not_called()

    @mock.patch('pdf.services.pdf_services.PdfProcessingServices.set_thumbnail_and_preview')
    def test_set_process_with_pypdfium_with_images(self, mock_set_thumbnail_and_preview):
        pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name='pdf_1')
        self.assertEqual(pdf.number_of_pages, -1)

        dummy_path = Path(__file__).parents[1] / 'data' / 'dummy.pdf'
        with dummy_path.open(mode="rb") as f:
            pdf.file = File(f, name=dummy_path.name)
            pdf.save()

        mock_set_thumbnail_and_preview.return_value = pdf

        service.PdfProcessingServices.process_with_pypdfium(pdf)

        pdf = self.user.profile.current_pdfs.get(name=pdf.name)
        self.assertEqual(pdf.number_of_pages, 2)
        mock_set_thumbnail_and_preview.assert_called_once()

    def test_set_process_with_pypdfium_exception(self):
        pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name='pdf_1')

        file_mock = mock.MagicMock(spec=File, name='FileMock')
        file_mock.name = 'test1.pdf'
        pdf.file = file_mock
        pdf.save()

        service.PdfProcessingServices.process_with_pypdfium(pdf, False)
        pdf = self.user.profile.current_pdfs.get(name=pdf.name)
        self.assertEqual(pdf.number_of_pages, -1)

    def test_set_thumbnail_and_preview(self):
        dummy_path = Path(__file__).parents[1] / 'data' / 'dummy.pdf'
        pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name='pdf')
        with dummy_path.open(mode="rb") as f:
            pdf.file = File(f, name=dummy_path.name)
            pdf.save()

        pdf_document = PdfDocument(pdf.file.path, autoclose=True)

        pdf = service.PdfProcessingServices.set_thumbnail_and_preview(pdf, pdf_document, 120, 2, 400)
        thumbnail_pil_image = Image.open(pdf.thumbnail.file)
        preview_pil_image = Image.open(pdf.preview.file)

        self.assertEqual(thumbnail_pil_image.size, (120, 60))
        self.assertEqual(preview_pil_image.width, 400)

        pdf_document.close()

    def test_set_thumbnail_and_preview_exception(self):
        pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name='pdf')

        # check that exception is caught and thumbnail stays unset
        pdf = service.PdfProcessingServices.set_thumbnail_and_preview(pdf, None, 120, 2, 150)

        self.assertFalse(pdf.thumbnail)

    def test_get_pdf_info_list(self):
        dummy_path = Path(__file__).parents[1] / 'data' / 'dummy.pdf'

        for i in range(3):
            pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name=f'pdf_{i}')
            with dummy_path.open(mode="rb") as f:
                pdf.file = File(f, name=dummy_path.name)
                pdf.save()

        generated_info_list = service.get_pdf_info_list(self.user.profile.current_workspace)
        expected_info_list = [(f'pdf_{i}', 8885) for i in range(3)]

        self.assertEqual(generated_info_list, expected_info_list)

    def test_set_highlights_and_comments(self):
        creation_date = datetime.strptime('20250311081649-+00:00', '%Y%m%d%H%M%S-%z')

        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection, name='pdf_with_annotations', file=get_demo_pdf()
        )

        comment_1 = PdfComment.objects.create(text='demo comment page 2', page=2, creation_date=creation_date, pdf=pdf)
        comment_2 = PdfComment.objects.create(text='last page', page=5, creation_date=creation_date, pdf=pdf)
        highlight_1 = PdfHighlight.objects.create(
            text='Massa ullamcorper aenean molestie laoreet aenean sed laoreet. '
            'Ante non cursus proin mauris dictumst magnis',
            page=3,
            creation_date=creation_date,
            pdf=pdf,
        )
        highlight_2 = PdfHighlight.objects.create(
            text='Semper curabitur est maecenas orci dis accumsan sem dictum commodo?',
            page=2,
            creation_date=creation_date,
            pdf=pdf,
        )

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        for generated_comment, expected_comment in zip(
            pdf.pdfcomment_set.all().order_by(Lower('text')), [comment_1, comment_2]
        ):
            self.assertEqual(generated_comment.text, expected_comment.text)
            self.assertEqual(generated_comment.creation_date, expected_comment.creation_date)
            self.assertEqual(generated_comment.page, expected_comment.page)
            # if id is different it comments were as expected recreated
            self.assertNotEqual(generated_comment.id, expected_comment.id)

        for generated_highlight, expected_comment_highlight in zip(
            pdf.pdfhighlight_set.all().order_by(Lower('text')), [highlight_1, highlight_2]
        ):
            self.assertEqual(generated_highlight.text, expected_comment_highlight.text)
            self.assertEqual(generated_highlight.creation_date, expected_comment_highlight.creation_date)
            self.assertEqual(generated_highlight.page, expected_comment_highlight.page)
            # if id is different it highlights were as expected recreated
            self.assertNotEqual(generated_highlight.id, expected_comment_highlight.id)

    def test_set_highlights_and_comments_exception(self):
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection, name='pdf_with_annotations', file='dummy_file'
        )

        # check that exception is caught and thumbnail stays unset
        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        self.assertFalse(pdf.pdfcomment_set.count())
        self.assertFalse(pdf.pdfhighlight_set.count())

    @mock.patch('pdf.services.pdf_services.PdfProcessingServices.export_annotations_to_json')
    def test_export_annotations(self, mock_export_annotation_to_json):
        pdf_1 = Pdf.objects.create(collection=self.user.profile.current_collection, name='pdf_1')
        pdf_2 = Pdf.objects.create(collection=self.user.profile.current_collection, name='pdf_2')

        comment_1 = PdfComment.objects.create(text='c1', page=1, creation_date=pdf_1.creation_date, pdf=pdf_1)
        comment_2 = PdfComment.objects.create(text='c2', page=2, creation_date=pdf_2.creation_date, pdf=pdf_2)
        highlight_1 = PdfHighlight.objects.create(text='h1', page=1, creation_date=pdf_1.creation_date, pdf=pdf_1)
        highlight_2 = PdfHighlight.objects.create(text='h2', page=2, creation_date=pdf_2.creation_date, pdf=pdf_2)

        # kind = comments and all pdfs
        service.PdfProcessingServices.export_annotations(profile=self.user.profile, kind='comments')
        comment_arg, id_arg = mock_export_annotation_to_json.call_args.args
        self.assertEqual(id_arg, str(self.user.id))
        for actual_comment, expected_comment in zip(comment_arg.order_by('text'), [comment_1, comment_2]):
            self.assertEqual(actual_comment, expected_comment)

        # kind = highlights and all pdfs
        service.PdfProcessingServices.export_annotations(profile=self.user.profile, kind='highlights')
        highlight_arg, id_arg = mock_export_annotation_to_json.call_args.args
        self.assertEqual(id_arg, str(self.user.id))
        for actual_highlight, expected_highlight in zip(highlight_arg.order_by('text'), [highlight_1, highlight_2]):
            self.assertEqual(actual_highlight, expected_highlight)

        # kind = comments and single pdfs
        service.PdfProcessingServices.export_annotations(profile=self.user.profile, kind='comments', pdf=pdf_1)
        comment_arg, id_arg = mock_export_annotation_to_json.call_args.args
        self.assertEqual(id_arg, str(self.user.id))
        for actual_comment, expected_comment in zip(comment_arg.order_by('text'), [comment_1]):
            self.assertEqual(actual_comment, expected_comment)

        # kind = highlights and single pdfs
        service.PdfProcessingServices.export_annotations(profile=self.user.profile, kind='highlights', pdf=pdf_1)
        highlight_arg, id_arg = mock_export_annotation_to_json.call_args.args
        self.assertEqual(id_arg, str(self.user.id))
        for actual_highlight, expected_highlight in zip(highlight_arg.order_by('text'), [highlight_1]):
            self.assertEqual(actual_highlight, expected_highlight)

    @mock.patch(
        'pdf.services.pdf_services.PdfProcessingServices.get_annotation_export_path',
        return_value=Path(__file__).parents[1] / 'data' / 'tmp_export.json',
    )
    def test_export_annotation_to_json(self, mock_export_annotation_to_json):
        creation_date = datetime.strptime('2025-03-17 20:26:48+00:00', '%Y-%m-%d %H:%M:%S%z')

        pdf_1 = Pdf.objects.create(collection=self.user.profile.current_collection, name='some_pdf')
        pdf_2 = Pdf.objects.create(collection=self.user.profile.current_collection, name='another_pdf')

        PdfComment.objects.create(text='c1', page=1, creation_date=creation_date, pdf=pdf_1)
        PdfComment.objects.create(text='c2', page=2, creation_date=creation_date, pdf=pdf_2)
        PdfComment.objects.create(text='another c', page=0, creation_date=creation_date, pdf=pdf_2)

        export_path = service.PdfProcessingServices.get_annotation_export_path(str(self.user.id))
        service.PdfProcessingServices.export_annotations_to_json(
            PdfComment.objects.all(), self.user.profile.current_workspace.id
        )

        self.assertTrue(
            filecmp.cmp(export_path, Path(__file__).parents[1] / 'data' / 'dummy_export.json', shallow=False)
        )

        export_path.unlink()

    @mock.patch('pdf.services.pdf_services.delete_empty_dirs_after_rename_or_delete')
    @mock.patch('pdf.services.pdf_services.get_file_path')
    @mock.patch('pdf.services.pdf_services.copy')
    def test_process_renaming_pdf(self, mock_copy, mock_get_file_path, mock_delete_empty_dirs_after_rename_or_delete):
        current_pdf_name = 'some_pdf'
        current_file_name = f'{self.user.id}/default/pdf/{current_pdf_name}.pdf'
        current_path = MEDIA_ROOT / current_file_name
        current_path.touch()

        new_pdf_name = 'changed'
        new_file_name = f'{self.user.id}/default/pdf/child_dir/{new_pdf_name}.pdf'
        new_path = MEDIA_ROOT / new_file_name
        mock_get_file_path.return_value = new_file_name

        pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name=current_pdf_name)
        pdf.file = current_file_name
        pdf.save()

        # set new name as it will be saved by process_renaming_pdf
        pdf.name = new_pdf_name

        # check that the name is still not changed, as saving it is part of process_renaming_pdf
        unchanged_pdf = Pdf.objects.get(id=pdf.id)
        self.assertEqual(current_pdf_name, unchanged_pdf.name)

        new_path_parent = new_path.parent
        # dir should not exist, instead it should be created during the rename_pdf
        self.assertFalse(new_path_parent.exists())

        service.PdfProcessingServices.process_renaming_pdf(pdf)
        changed_pdf = Pdf.objects.get(id=pdf.id)

        # check that the file was copied, the original file was deleted, empty dirs were deleted,
        # name and file name were adjusted
        mock_get_file_path.assert_called_once_with(pdf, None)
        self.assertEqual(new_pdf_name, changed_pdf.name)
        self.assertTrue(new_path_parent.exists())
        self.assertEqual(changed_pdf.file.name, new_file_name)
        mock_copy.assert_called_once_with(current_path, new_path)
        self.assertFalse(current_path.exists())
        mock_delete_empty_dirs_after_rename_or_delete.assert_called_once_with(
            current_file_name, changed_pdf.workspace.id, changed_pdf.collection.name
        )

        # cleanup
        new_path_parent.rmdir()

    @mock.patch('pdf.services.pdf_services.copy')
    @mock.patch('pdf.services.pdf_services.delete_empty_dirs_after_rename_or_delete')
    @mock.patch('pdf.services.pdf_services.get_file_path')
    def test_process_renaming_pdf_unchanged_file_path(
        self, mock_get_file_path, mock_delete_empty_dirs_after_rename_or_delete, mock_copy
    ):
        original_pdf_name = 'some_pdf'
        new_pdf_name = 'changed'
        file_name = 'subdir/some.pdf'
        mock_get_file_path.return_value = file_name

        pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name=original_pdf_name)
        pdf.file = file_name
        pdf.save()

        pdf.name = new_pdf_name

        # check that the name is still not changed
        unchanged_pdf = Pdf.objects.get(id=pdf.id)
        self.assertEqual(original_pdf_name, unchanged_pdf.name)

        service.PdfProcessingServices.process_renaming_pdf(pdf)

        # check that the new name was saved
        changed_pdf = Pdf.objects.get(id=pdf.id)
        self.assertEqual(new_pdf_name, changed_pdf.name)

        mock_get_file_path.assert_called_once_with(pdf, None)

        # check that blocks are not called as the file path did not change
        mock_copy.assert_not_called()
        mock_delete_empty_dirs_after_rename_or_delete.assert_not_called()


class TestParsePdfDate(TestCase):
    """Regression tests for the parse_pdf_date helper."""

    def setUp(self):
        self.fallback = datetime(2000, 1, 1, tzinfo=timezone.utc)

    def test_standard_date_no_timezone(self):
        """D:YYYYMMDDHHmmSS without timezone should parse as UTC."""
        annotation = {"/CreationDate": "D:20250311081649"}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result.year, 2025)
        self.assertEqual(result.month, 3)
        self.assertEqual(result.day, 11)
        self.assertEqual(result.hour, 8)
        self.assertEqual(result.minute, 16)
        self.assertEqual(result.second, 49)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_date_with_z_suffix(self):
        """D:YYYYMMDDHHmmSSZ should parse as UTC."""
        annotation = {"/CreationDate": "D:20250311081649Z"}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result.year, 2025)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_date_with_positive_timezone(self):
        """D:YYYYMMDDHHmmSS+HH'mm' should parse with correct offset."""
        annotation = {"/CreationDate": "D:20250311081649+05'30'"}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result.year, 2025)
        self.assertEqual(result.month, 3)
        self.assertEqual(result.day, 11)
        expected_tz = timezone(timedelta(hours=5, minutes=30))
        self.assertEqual(result.tzinfo, expected_tz)

    def test_date_with_negative_timezone(self):
        """D:YYYYMMDDHHmmSS-HH'mm' should parse with correct negative offset."""
        annotation = {"/CreationDate": "D:20250311081649-08'00'"}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result.year, 2025)
        expected_tz = timezone(timedelta(hours=-8))
        self.assertEqual(result.tzinfo, expected_tz)

    def test_date_without_d_prefix(self):
        """A date string without D: prefix should still parse."""
        annotation = {"/CreationDate": "20250311081649"}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result.year, 2025)
        self.assertEqual(result.month, 3)

    def test_date_missing_trailing_quote(self):
        """D:YYYYMMDDHHmmSS+HH'mm (missing trailing quote) should still parse."""
        annotation = {"/CreationDate": "D:20250311081649+05'30"}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result.year, 2025)
        expected_tz = timezone(timedelta(hours=5, minutes=30))
        self.assertEqual(result.tzinfo, expected_tz)

    def test_missing_creation_date_returns_fallback(self):
        """Missing /CreationDate should return the fallback date."""
        annotation = {}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result, self.fallback)

    def test_missing_creation_date_uses_m_field(self):
        """When /CreationDate is missing, /M should be used as fallback."""
        annotation = {"/M": "D:20250601120000"}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result.year, 2025)
        self.assertEqual(result.month, 6)
        self.assertEqual(result.day, 1)

    def test_invalid_date_string_returns_fallback(self):
        """A completely invalid date string should return the fallback."""
        annotation = {"/CreationDate": "not-a-date"}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result, self.fallback)

    def test_empty_string_returns_fallback(self):
        """An empty /CreationDate string should return the fallback."""
        annotation = {"/CreationDate": ""}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result, self.fallback)

    def test_none_value_returns_fallback(self):
        """A None /CreationDate value should return the fallback."""
        annotation = {"/CreationDate": None}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result, self.fallback)

    def test_non_string_value_returns_fallback(self):
        """A non-string /CreationDate value should return the fallback."""
        annotation = {"/CreationDate": 12345}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result, self.fallback)

    def test_invalid_month_returns_fallback(self):
        """A date with an invalid month (13) should return the fallback."""
        annotation = {"/CreationDate": "D:20251311081649"}
        result = parse_pdf_date(annotation, self.fallback)

        self.assertEqual(result, self.fallback)


class TestSetHighlightsAndCommentsResilience(TestCase):
    """Regression tests ensuring annotation extraction is resilient to bad data."""

    def setUp(self):
        self.user = User.objects.create_user(username='username', password='password', email='a@a.com')

    def _make_annotation_mock(self, subtype, creation_date=None, contents=None, quad_points=None):
        """Create a mock annotation object as returned by pypdf."""
        obj = mock.MagicMock()
        obj_dict = {"/Subtype": subtype}
        if creation_date is not None:
            obj_dict["/CreationDate"] = creation_date
        if contents is not None:
            obj_dict["/Contents"] = contents
        if quad_points is not None:
            obj_dict["/QuadPoints"] = quad_points

        obj.get_object.return_value = obj_dict

        # make dict-like access work for annotation_object.get(key) and "key" in annotation_object
        obj_dict_get = {k: v for k, v in obj_dict.items()}

        class FakeAnnotationObject(dict):
            def get(self, key, default=None):
                return obj_dict_get.get(key, default)

        fake_obj = FakeAnnotationObject(obj_dict_get)
        obj.get_object.return_value = fake_obj

        return obj

    def _make_page_mock(self, annotations):
        """Create a mock pypdf page with the given annotations."""
        page = mock.MagicMock()
        page.__contains__ = mock.MagicMock(return_value=bool(annotations))
        page.__getitem__ = mock.MagicMock(return_value=annotations)
        return page

    @mock.patch('pdf.services.pdf_services.PdfDocument')
    @mock.patch('pdf.services.pdf_services.PdfReader')
    def test_missing_creation_date_uses_fallback(self, mock_reader_class, mock_document_class):
        """Annotations without /CreationDate should use the pdf creation_date as fallback."""
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='test_pdf',
            file='dummy.pdf',
        )

        # Create a FreeText annotation without /CreationDate
        comment_annot = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date=None,
            contents="A comment without date",
        )

        mock_page = self._make_page_mock([comment_annot])
        mock_reader = mock.MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_class.return_value = mock_reader

        mock_pdfium_doc = mock.MagicMock()
        mock_document_class.return_value = mock_pdfium_doc

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        comments = list(pdf.pdfcomment_set.all())
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].text, "A comment without date")
        self.assertEqual(comments[0].page, 1)
        # creation_date should be the fallback (pdf.creation_date)
        self.assertEqual(comments[0].creation_date, pdf.creation_date)

    @mock.patch('pdf.services.pdf_services.PdfDocument')
    @mock.patch('pdf.services.pdf_services.PdfReader')
    def test_invalid_date_does_not_skip_other_annotations(self, mock_reader_class, mock_document_class):
        """An annotation with an invalid date should not prevent other annotations from being stored."""
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='test_pdf',
            file='dummy.pdf',
        )

        # First annotation: FreeText with invalid date
        bad_annot = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="totally-invalid-date",
            contents="Bad date comment",
        )
        # Second annotation: FreeText with valid date
        good_annot = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="D:20250311081649",
            contents="Good date comment",
        )

        mock_page = self._make_page_mock([bad_annot, good_annot])
        mock_reader = mock.MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_class.return_value = mock_reader

        mock_pdfium_doc = mock.MagicMock()
        mock_document_class.return_value = mock_pdfium_doc

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        comments = list(pdf.pdfcomment_set.all().order_by('text'))
        # Both annotations should be stored despite the first having an invalid date
        self.assertEqual(len(comments), 2)
        texts = [c.text for c in comments]
        self.assertIn("Bad date comment", texts)
        self.assertIn("Good date comment", texts)

    @mock.patch('pdf.services.pdf_services.PdfDocument')
    @mock.patch('pdf.services.pdf_services.PdfReader')
    def test_bad_annotation_on_page_does_not_skip_rest_of_page(self, mock_reader_class, mock_document_class):
        """A failing annotation should not prevent subsequent annotations on the same page."""
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='test_pdf',
            file='dummy.pdf',
        )

        # First annotation: will raise during get_object()
        bad_annot = mock.MagicMock()
        bad_annot.get_object.side_effect = RuntimeError("corrupt annotation")

        # Second annotation: valid FreeText
        good_annot = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="D:20250311081649",
            contents="Valid comment after bad one",
        )

        mock_page = self._make_page_mock([bad_annot, good_annot])
        mock_reader = mock.MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_class.return_value = mock_reader

        mock_pdfium_doc = mock.MagicMock()
        mock_document_class.return_value = mock_pdfium_doc

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        comments = list(pdf.pdfcomment_set.all())
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].text, "Valid comment after bad one")

    @mock.patch('pdf.services.pdf_services.PdfDocument')
    @mock.patch('pdf.services.pdf_services.PdfReader')
    def test_empty_contents_skipped(self, mock_reader_class, mock_document_class):
        """FreeText annotations with empty /Contents should be skipped."""
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='test_pdf',
            file='dummy.pdf',
        )

        empty_annot = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="D:20250311081649",
            contents="",
        )
        valid_annot = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="D:20250311081649",
            contents="Valid comment",
        )

        mock_page = self._make_page_mock([empty_annot, valid_annot])
        mock_reader = mock.MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_class.return_value = mock_reader

        mock_pdfium_doc = mock.MagicMock()
        mock_document_class.return_value = mock_pdfium_doc

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        comments = list(pdf.pdfcomment_set.all())
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].text, "Valid comment")

    @mock.patch('pdf.services.pdf_services.PdfDocument')
    @mock.patch('pdf.services.pdf_services.PdfReader')
    def test_unknown_subtype_skipped(self, mock_reader_class, mock_document_class):
        """Annotations with unknown /Subtype should be skipped without error."""
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='test_pdf',
            file='dummy.pdf',
        )

        link_annot = self._make_annotation_mock(
            subtype="/Link",
            creation_date="D:20250311081649",
        )
        valid_annot = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="D:20250311081649",
            contents="Valid comment",
        )

        mock_page = self._make_page_mock([link_annot, valid_annot])
        mock_reader = mock.MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_class.return_value = mock_reader

        mock_pdfium_doc = mock.MagicMock()
        mock_document_class.return_value = mock_pdfium_doc

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        comments = list(pdf.pdfcomment_set.all())
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].text, "Valid comment")

    @mock.patch('pdf.services.pdf_services.PdfDocument')
    @mock.patch('pdf.services.pdf_services.PdfReader')
    def test_stability_across_multiple_calls(self, mock_reader_class, mock_document_class):
        """Page numbers, text, and dates should remain stable across multiple calls."""
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='test_pdf',
            file='dummy.pdf',
        )

        comment_annot = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="D:20250311081649",
            contents="Stable comment",
        )

        mock_page = self._make_page_mock([comment_annot])
        mock_reader = mock.MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_class.return_value = mock_reader

        mock_pdfium_doc = mock.MagicMock()
        mock_document_class.return_value = mock_pdfium_doc

        # First call
        service.PdfProcessingServices.set_highlights_and_comments(pdf)
        first_comments = list(pdf.pdfcomment_set.all())
        self.assertEqual(len(first_comments), 1)
        first_text = first_comments[0].text
        first_page = first_comments[0].page
        first_date = first_comments[0].creation_date

        # Second call
        service.PdfProcessingServices.set_highlights_and_comments(pdf)
        second_comments = list(pdf.pdfcomment_set.all())
        self.assertEqual(len(second_comments), 1)
        self.assertEqual(second_comments[0].text, first_text)
        self.assertEqual(second_comments[0].page, first_page)
        self.assertEqual(second_comments[0].creation_date, first_date)

        # Third call
        service.PdfProcessingServices.set_highlights_and_comments(pdf)
        third_comments = list(pdf.pdfcomment_set.all())
        self.assertEqual(len(third_comments), 1)
        self.assertEqual(third_comments[0].text, first_text)
        self.assertEqual(third_comments[0].page, first_page)
        self.assertEqual(third_comments[0].creation_date, first_date)

    @mock.patch('pdf.services.pdf_services.PdfDocument')
    @mock.patch('pdf.services.pdf_services.PdfReader')
    def test_mixed_highlights_and_comments_with_bad_dates(self, mock_reader_class, mock_document_class):
        """Mixed highlights and comments with various date issues should all be extracted."""
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='test_pdf',
            file='dummy.pdf',
        )

        # Comment with no date
        comment_no_date = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date=None,
            contents="Comment no date",
        )
        # Comment with valid date
        comment_valid = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="D:20250311081649",
            contents="Comment valid date",
        )
        # Comment with invalid date
        comment_bad_date = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="garbage",
            contents="Comment bad date",
        )

        mock_page = self._make_page_mock([comment_no_date, comment_valid, comment_bad_date])
        mock_reader = mock.MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_class.return_value = mock_reader

        mock_pdfium_doc = mock.MagicMock()
        mock_document_class.return_value = mock_pdfium_doc

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        comments = list(pdf.pdfcomment_set.all().order_by('text'))
        self.assertEqual(len(comments), 3)
        texts = [c.text for c in comments]
        self.assertIn("Comment no date", texts)
        self.assertIn("Comment valid date", texts)
        self.assertIn("Comment bad date", texts)

        # All should have page=1
        for c in comments:
            self.assertEqual(c.page, 1)

    @mock.patch('pdf.services.pdf_services.PdfDocument')
    @mock.patch('pdf.services.pdf_services.PdfReader')
    def test_date_with_timezone_offset_preserved(self, mock_reader_class, mock_document_class):
        """Annotations with timezone offsets should have the offset preserved (converted to UTC by Django)."""
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='test_pdf',
            file='dummy.pdf',
        )

        # Comment with +05:30 timezone
        comment_annot = self._make_annotation_mock(
            subtype="/FreeText",
            creation_date="D:20250311081649+05'30'",
            contents="Timezone comment",
        )

        mock_page = self._make_page_mock([comment_annot])
        mock_reader = mock.MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_class.return_value = mock_reader

        mock_pdfium_doc = mock.MagicMock()
        mock_document_class.return_value = mock_pdfium_doc

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        comments = list(pdf.pdfcomment_set.all())
        self.assertEqual(len(comments), 1)

        # Django stores datetimes in UTC, so 08:16:49+05:30 becomes 02:46:49 UTC
        expected_utc = datetime(2025, 3, 11, 2, 46, 49, tzinfo=timezone.utc)
        self.assertEqual(comments[0].creation_date, expected_utc)


class TestOtherServices(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='username', password='password', email='a@a.com')

    @staticmethod
    @service.check_object_access_allowed
    def get_object(pdf_id: str, user: User):
        user_profile = user.profile
        pdf = user_profile.current_pdfs.get(id=pdf_id)

        return pdf

    def test_check_object_access_allowed_existing(self):
        pdf = Pdf.objects.create(collection=self.user.profile.current_collection, name='pdf')

        self.assertEqual(pdf, self.get_object(pdf.id, self.user))

    def test_check_object_access_allowed_validation(self):
        with self.assertRaises(Http404):
            self.get_object('12345', self.user)

    def test_check_object_access_allowed_does_not_exist(self):
        with self.assertRaises(Http404):
            self.get_object(str(uuid4()), self.user)

    def test_create_name_from_file_no_suffix(self):
        file_mock = mock.MagicMock(spec=File, name='FileMock')
        file_mock.name = 'some_name'

        generated_name = service.create_name_from_file(file_mock)
        self.assertEqual(generated_name, 'some_name')

    def test_create_name_from_file_different_suffix(self):
        file_mock = mock.MagicMock(spec=File, name='FileMock')
        file_mock.name = 'some.name'

        generated_name = service.create_name_from_file(file_mock)
        self.assertEqual(generated_name, 'some.name')

    def test_create_name_from_file_pdf_suffix(self):
        file_mock = mock.MagicMock(spec=File, name='FileMock')
        file_mock.name = 'some.name.PdF'

        generated_name = service.create_name_from_file(file_mock)
        self.assertEqual(generated_name, 'some.name')

    @mock.patch('pdf.services.pdf_services.create_name_from_file', return_value='existing_name')
    @mock.patch('pdf.services.pdf_services.uuid4', return_value='123456789')
    def test_create_unique_name_from_file_existing_name(self, mock_uuid4, mock_create_name_from_file):
        user = User.objects.create_user(username='user', password='12345', email='a@a.com')
        Pdf.objects.create(collection=user.profile.current_collection, name='existing_name')
        file_mock = mock.MagicMock(spec=File, name='FileMock')
        file_mock.name = 'existing_name.pdf'

        generated_name = service.create_unique_name_from_file(file_mock, user.profile)
        self.assertEqual(generated_name, 'existing_name_12345678')
        mock_create_name_from_file.assert_called_once_with(file_mock)

    @mock.patch('pdf.services.pdf_services.create_name_from_file', return_value='not_existing_name')
    def test_create_unique_name_from_file_not_existing_name(self, mock_create_name_from_file):
        user = User.objects.create_user(username='user', password='12345', email='a@a.com')
        file_mock = mock.MagicMock(spec=File, name='FileMock')
        file_mock.name = 'not_existing_name.pdf'

        generated_name = service.create_unique_name_from_file(file_mock, user.profile)
        self.assertEqual(generated_name, 'not_existing_name')
        mock_create_name_from_file.assert_called_once_with(file_mock)

    def test_adjust_referer_for_tag_view_no_replace(self):
        # url of searched for #other
        url = f'{reverse("pdf_overview")}?search=searching&tags=tag1+tag2'
        adjusted_url = service.TagServices.adjust_referer_for_tag_view(url, 'tag', 'other_tag')

        self.assertEqual(url, adjusted_url)

    def test_adjust_referer_for_tag_view_no_query(self):
        url = reverse('pdf_overview')

        adjusted_url = service.TagServices.adjust_referer_for_tag_view(url, 'tag', 'other_tag')

        self.assertEqual(url, adjusted_url)

    def test_adjust_referer_for_tag_view_space(self):
        # url of searched for #other
        url = f'{reverse("pdf_overview")}?search=searching&tags=tag1+tag2'

        adjusted_url = service.TagServices.adjust_referer_for_tag_view(url, 'tag1', '')
        expected_url = f'{reverse("pdf_overview")}?search=searching&tags=tag2'

        self.assertEqual(expected_url, adjusted_url)

    def test_adjust_referer_for_tag_view_space_remove(self):
        # url of searched for #other
        url = f'{reverse("pdf_overview")}?search=searching&tags=other'

        adjusted_url = service.TagServices.adjust_referer_for_tag_view(url, 'other', '')
        expected_url = f'{reverse("pdf_overview")}?search=searching'

        self.assertEqual(expected_url, adjusted_url)

    def test_adjust_referer_for_tag_view_word(self):
        # url of searched for #other
        url = f'{reverse("pdf_overview")}?tags=other'

        adjusted_url = service.TagServices.adjust_referer_for_tag_view(url, 'other', 'another')
        expected_url = f'{reverse("pdf_overview")}?tags=another'

        self.assertEqual(expected_url, adjusted_url)
