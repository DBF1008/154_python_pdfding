import filecmp
from datetime import datetime, timezone
from io import BytesIO
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
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdfium2 import PdfDocument
from users.service import get_demo_pdf


def build_demo_pdf_with_modified_annotations(mutate_annotation, name='modified_demo.pdf') -> File:
    """
    Build a Django File from the demo pdf, applying ``mutate_annotation`` to every comment/highlight annotation.

    This is used to craft annotated pdfs whose annotations have missing or malformed timestamps (or other broken
    fields) while keeping the rest of the demo pdf - including the text the highlights point at - intact.
    """

    reader = PdfReader(BytesIO(get_demo_pdf().read()))
    writer = PdfWriter(clone_from=reader)

    for page in writer.pages:
        if '/Annots' not in page:
            continue
        for annotation in page['/Annots']:
            annotation_object = annotation.get_object()
            if annotation_object.get('/Subtype') in ('/FreeText', '/Highlight'):
                mutate_annotation(annotation_object)

    buffer = BytesIO()
    writer.write(buffer)
    buffer.seek(0)

    return File(file=buffer, name=name)


def remove_annotation_dates(annotation_object) -> None:
    """Remove both timestamp keys of an annotation so it has no standard creation/modification date."""

    for date_key in ('/CreationDate', '/ModDate'):
        if date_key in annotation_object:
            del annotation_object[date_key]


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

    def test_parse_pdf_date(self):
        parse_pdf_date = service.PdfProcessingServices.parse_pdf_date

        # timezone-less and "Z" values are treated as UTC (this matches the pre-existing annotations)
        self.assertEqual(parse_pdf_date('D:20250311081649'), datetime(2025, 3, 11, 8, 16, 49, tzinfo=timezone.utc))
        self.assertEqual(parse_pdf_date('20250311081649'), datetime(2025, 3, 11, 8, 16, 49, tzinfo=timezone.utc))
        self.assertEqual(parse_pdf_date('D:20250311081649Z'), datetime(2025, 3, 11, 8, 16, 49, tzinfo=timezone.utc))

        # explicit timezone offsets are normalized to UTC
        self.assertEqual(
            parse_pdf_date("D:20250311081649+05'30'"), datetime(2025, 3, 11, 2, 46, 49, tzinfo=timezone.utc)
        )
        self.assertEqual(
            parse_pdf_date("D:20250311081649-08'00'"), datetime(2025, 3, 11, 16, 16, 49, tzinfo=timezone.utc)
        )

        # truncated values are still parsed instead of failing
        self.assertEqual(parse_pdf_date('D:202503'), datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc))

        # missing or malformed values return None so the caller can fall back to another timestamp
        for invalid_date in [None, '', 'garbage', 'D:', 'D:20251301081649']:
            self.assertIsNone(parse_pdf_date(invalid_date))

    def test_set_highlights_and_comments_keeps_annotations_without_timestamps(self):
        # an annotated pdf where no comment/highlight has a standard timestamp used to be skipped entirely
        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='pdf_without_timestamps',
            file=build_demo_pdf_with_modified_annotations(remove_annotation_dates, 'no_dates.pdf'),
        )

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        # all recognizable annotations are still stored
        self.assertEqual(pdf.pdfcomment_set.count(), 2)
        self.assertEqual(pdf.pdfhighlight_set.count(), 2)

        # page and text are extracted exactly as for an annotation with a timestamp
        self.assertEqual(
            sorted(comment.text for comment in pdf.pdfcomment_set.all()), ['demo comment page 2', 'last page']
        )
        self.assertEqual(sorted(comment.page for comment in pdf.pdfcomment_set.all()), [2, 5])
        self.assertEqual(
            sorted(highlight.text for highlight in pdf.pdfhighlight_set.all()),
            [
                'Massa ullamcorper aenean molestie laoreet aenean sed laoreet. '
                'Ante non cursus proin mauris dictumst magnis',
                'Semper curabitur est maecenas orci dis accumsan sem dictum commodo?',
            ],
        )

        # the missing timestamp deterministically falls back to the pdf creation date
        for annotation in [*pdf.pdfcomment_set.all(), *pdf.pdfhighlight_set.all()]:
            self.assertEqual(annotation.creation_date, pdf.creation_date)

    def test_set_highlights_and_comments_stable_across_reruns(self):
        # mixed pdf: keep one valid timestamp, drop the timestamps of all other annotations
        def remove_dates_except_first_comment(annotation_object):
            if annotation_object.get('/Contents') == 'demo comment page 2':
                return
            remove_annotation_dates(annotation_object)

        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='pdf_stable_annotations',
            file=build_demo_pdf_with_modified_annotations(remove_dates_except_first_comment, 'stable.pdf'),
        )

        def extract_annotation_state():
            return sorted(
                (annotation.page, annotation.text, annotation.creation_date)
                for annotation in [*pdf.pdfcomment_set.all(), *pdf.pdfhighlight_set.all()]
            )

        service.PdfProcessingServices.set_highlights_and_comments(pdf)
        first_run = extract_annotation_state()

        # the valid timestamp is parsed from the pdf, not replaced by the fallback
        self.assertIn(
            (2, 'demo comment page 2', datetime(2025, 3, 11, 8, 16, 49, tzinfo=timezone.utc)), first_run
        )

        # re-running extraction the way a file update or a history rebuild does keeps page, text and time identical
        pdf.refresh_from_db()
        service.PdfProcessingServices.set_highlights_and_comments(pdf)
        second_run = extract_annotation_state()

        self.assertEqual(first_run, second_run)

    def test_set_highlights_and_comments_skips_only_the_broken_annotation(self):
        # break a single highlight (remove its quad points) and make sure the others are still extracted
        broken_highlights = []

        def break_first_highlight(annotation_object):
            if annotation_object.get('/Subtype') == '/Highlight' and not broken_highlights:
                del annotation_object['/QuadPoints']
                broken_highlights.append(True)

        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection,
            name='pdf_one_broken_annotation',
            file=build_demo_pdf_with_modified_annotations(break_first_highlight, 'broken_highlight.pdf'),
        )

        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        # the two comments and the remaining valid highlight survive, only the broken highlight is dropped
        self.assertEqual(pdf.pdfcomment_set.count(), 2)
        self.assertEqual(pdf.pdfhighlight_set.count(), 1)

    def test_set_highlights_and_comments_unreadable_file_keeps_existing(self):
        creation_date = datetime(2025, 3, 11, 8, 16, 49, tzinfo=timezone.utc)

        pdf = Pdf.objects.create(
            collection=self.user.profile.current_collection, name='pdf_unreadable_update', file='not_a_real_pdf'
        )
        PdfComment.objects.create(text='existing comment', page=1, creation_date=creation_date, pdf=pdf)
        PdfHighlight.objects.create(text='existing highlight', page=2, creation_date=creation_date, pdf=pdf)

        # the (updated) file cannot be parsed, so the already extracted annotations must be preserved, not wiped
        service.PdfProcessingServices.set_highlights_and_comments(pdf)

        self.assertEqual(pdf.pdfcomment_set.count(), 1)
        self.assertEqual(pdf.pdfhighlight_set.count(), 1)
        self.assertEqual(pdf.pdfcomment_set.first().text, 'existing comment')
        self.assertEqual(pdf.pdfhighlight_set.first().text, 'existing highlight')

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
