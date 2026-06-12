from datetime import datetime, timezone
from io import BytesIO

import qrcode
from base import base_views
from base.service import construct_query_overview_url
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.sessions.models import Session
from django.core.files import File
from django.db.models import F, Q, QuerySet
from django.db.models.functions import Lower
from django.http import Http404, HttpRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from rapidfuzz import fuzz, utils
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
from pdf.models.shared_pdf_models import SharedPdf
from pdf.services.pdf_services import check_object_access_allowed
from pdf.services.shared_pdf_services import (
    check_shared_access_allowed,
    check_shared_access_allowed_by_identifier,
    get_future_datetime,
)
from pdf.services.workspace_services import get_shared_pdfs_of_workspace
from pdf.views.pdf_views import PdfMixin
from qrcode.image import svg
from users.service import get_viewer_theme_and_color


class BaseShareMixin:
    obj_name = 'shared_pdf'


class AddSharedPdfMixin(BaseShareMixin):
    form = ShareForm
    template_name = 'add_shared_pdf.html'

    @staticmethod
    def generate_qr_code(qr_code_content: str) -> BytesIO:  # pragma: no cover
        """
        Create a qr code and return as a Bytes object.
        """

        qr = qrcode.QRCode(image_factory=svg.SvgPathImage, box_size=12, border=1)
        qr.add_data(qr_code_content)
        qr.make(fit=True)

        qr_img = qr.make_image(fill_color="black", back_color="white")
        # save as bytes object so django can use it as a file for file field
        qr_as_byte = BytesIO()
        qr_img.save(qr_as_byte)

        return qr_as_byte

    def get_context_get(self, request: HttpRequest, pdf_id: str):
        """Get the context needed to be passed to the template containing the form for adding a shared PDf."""

        pdf = PdfMixin.get_object(request, pdf_id)
        form = self.form

        context = {'form': form(profile=request.user.profile), 'pdf_name': pdf.name}

        return context

    @classmethod
    def obj_save(cls, form: ShareForm, request: HttpRequest, identifier: str):
        """Save the shared PDF based on the submitted form."""

        shared_pdf = form.save(commit=False)
        shared_pdf.pdf = PdfMixin.get_object(request, identifier)

        cls.add_qr_code(shared_pdf, request)
        cls.set_access_dates(shared_pdf, form.data.get('expiration_input'), form.data.get('deletion_input'))

    @classmethod
    def add_qr_code(cls, shared_pdf: SharedPdf, request: HttpRequest):
        """Add the QR code to the shared PDF."""

        qr_code_content = (
            f'{request.scheme}://{request.get_host()}{reverse("view_shared_pdf", kwargs={"identifier": shared_pdf.id})}'
        )
        qr_as_byte = cls.generate_qr_code(qr_code_content)

        shared_pdf.file.save(None, File(qr_as_byte))

    @staticmethod
    def set_access_dates(shared_pdf, expiration_input, deletion_input):
        """Set the deletion date and expiration date of the shared PDF."""

        if expiration_input:
            shared_pdf.expiration_date = get_future_datetime(expiration_input)
        if deletion_input:
            shared_pdf.deletion_date = get_future_datetime(deletion_input)

        shared_pdf.save()


class OverviewMixin(BaseShareMixin):
    overview_page_name = 'shared_overview/overview_page'

    @staticmethod
    def get_sorting(request: HttpRequest):
        """Get the sorting of the overview page."""

        profile = request.user.profile

        sorting_dict = {
            'Newest': '-creation_date',
            'Oldest': 'creation_date',
            'Name_asc': Lower('name'),
            'Name_desc': Lower('name').desc(),
        }

        return sorting_dict[profile.shared_pdf_sorting]

    @classmethod
    def filter_objects(cls, request: HttpRequest) -> QuerySet:
        """Filter the shared PDFs based on search, status, and password filters."""

        shared_pdfs = request.user.profile.current_shared_pdfs

        # always exclude deleted shares (scheduled deletion date has passed)
        shared_pdfs = shared_pdfs.filter(
            Q(deletion_date__isnull=True) | Q(deletion_date__gt=datetime.now(timezone.utc))
        )

        # status filter (DB-level)
        status = request.GET.get('status', '')
        now = datetime.now(timezone.utc)

        if status == 'active':
            shared_pdfs = shared_pdfs.filter(
                Q(expiration_date__isnull=True) | Q(expiration_date__gt=now)
            ).filter(
                Q(max_views__isnull=True) | Q(views__lt=F('max_views'))
            )
        elif status == 'expired':
            shared_pdfs = shared_pdfs.filter(
                Q(expiration_date__isnull=False, expiration_date__lte=now)
                | Q(max_views__isnull=False, views__gte=F('max_views'))
            )

        # password filter (DB-level)
        password_filter = request.GET.get('password', '')

        if password_filter == 'protected':
            shared_pdfs = shared_pdfs.exclude(Q(password__isnull=True) | Q(password=''))
        elif password_filter == 'unprotected':
            shared_pdfs = shared_pdfs.filter(Q(password__isnull=True) | Q(password=''))

        # fuzzy text search against share name and underlying PDF name
        search = request.GET.get('search', '')

        if search:
            shared_pdfs = cls.fuzzy_filter_shared_pdfs(shared_pdfs, search)

        return shared_pdfs

    @staticmethod
    def fuzzy_filter_shared_pdfs(shared_pdfs: QuerySet, search: str) -> QuerySet:
        """Fuzzy match against both share name and underlying PDF name."""

        fuzzy_result = []

        for shared_pdf in shared_pdfs.select_related('pdf'):
            # check share name
            w_ratio_name = fuzz.WRatio(search, shared_pdf.name, processor=utils.default_process)
            partial_ratio_name = fuzz.partial_ratio(search, shared_pdf.name, processor=utils.default_process)

            # check underlying PDF name
            w_ratio_pdf = fuzz.WRatio(search, shared_pdf.pdf.name, processor=utils.default_process)
            partial_ratio_pdf = fuzz.partial_ratio(search, shared_pdf.pdf.name, processor=utils.default_process)

            name_match = (w_ratio_name + partial_ratio_name) / 2 > 85 or partial_ratio_name > 95
            pdf_match = (w_ratio_pdf + partial_ratio_pdf) / 2 > 85 or partial_ratio_pdf > 95

            if name_match or pdf_match:
                fuzzy_result.append(shared_pdf.id)

        return shared_pdfs.filter(id__in=fuzzy_result)

    @staticmethod
    def get_extra_context(request: HttpRequest) -> dict:  # pragma: no cover
        """get further information that needs to be passed to the template."""

        return {
            'page': 'shared_pdf_overview',
            'current_collection_id': request.user.profile.current_collection_id,
            'current_collection_name': request.user.profile.current_collection_name,
            'current_workspace_id': request.user.profile.current_workspace_id,
            'search_query': request.GET.get('search', ''),
            'status_filter': request.GET.get('status', ''),
            'password_filter': request.GET.get('password', ''),
        }


class SharedPdfMixin(BaseShareMixin):
    obj_class = SharedPdf

    @staticmethod
    @check_object_access_allowed
    def get_object(request: HttpRequest, identifier: str):
        """Get the shared pdf specified by the ID"""

        user_profile = request.user.profile
        shared_pdf = user_profile.all_shared_pdfs.get(id=identifier)

        return shared_pdf


class EditSharedPdfMixin(SharedPdfMixin):
    fields_requiring_extra_processing = ['expiration_date', 'deletion_date', 'name']

    @staticmethod
    def get_edit_form_dict():
        """Get the forms of the fields that can be edited as a dict."""

        form_dict = {
            'description': SharedDescriptionForm,
            'name': SharedNameForm,
            'max_views': SharedMaxViewsForm,
            'password': SharedPasswordForm,
            'expiration_date': SharedExpirationDateForm,
            'deletion_date': SharedDeletionDateForm,
        }

        return form_dict

    def get_edit_form_get(self, field_name: str, shared_pdf: SharedPdf):
        """Get the form belonging to the specified field."""

        form_dict = self.get_edit_form_dict()

        initial_dict = {
            'name': {'name': shared_pdf.name},
            'description': {'description': shared_pdf.description},
            'max_views': {'max_views': shared_pdf.max_views},
            'password': {'password': ''},  # nosec B105
            'expiration_date': {'expiration_date': ''},
            'deletion_date': {'deletion_date': ''},
        }

        form = form_dict[field_name](initial=initial_dict[field_name])

        return form

    @classmethod
    def process_field(cls, field_name: str, shared_pdf: SharedPdf, request: HttpRequest, form_data: dict):
        """Process fields that are not covered in the base edit view."""

        if field_name == 'expiration_date':
            shared_pdf.expiration_date = get_future_datetime(form_data['expiration_input'])
            shared_pdf.save()
        elif field_name == 'deletion_date':
            shared_pdf.deletion_date = get_future_datetime(form_data['deletion_input'])
            shared_pdf.save()
        elif field_name == 'name':
            shared_pdfs = get_shared_pdfs_of_workspace(request.user.profile.current_workspace)
            existing_obj = shared_pdfs.filter(name__iexact=form_data.get('name')).first()

            if existing_obj and str(existing_obj.id) != str(shared_pdf.id):
                messages.warning(request, _('This name is already used by another shared PDF!'))
            else:
                shared_pdf.name = form_data.get('name').strip()
                shared_pdf.save()


class PdfPublicMixin:
    @staticmethod
    def get_object(request: HttpRequest, shared_id: str):
        """Get the shared pdf specified by the ID"""

        if check_shared_access_allowed_by_identifier(shared_id, request.session):
            shared_pdf = SharedPdf.objects.get(pk=shared_id)

            return shared_pdf.pdf
        else:
            raise Http404('Access to shared pdf not allowed!')


class BaseSharedPdfPublicView(View):
    @staticmethod
    @check_object_access_allowed
    # first parameter needed because of the decorator
    def get_shared_pdf_public(_, shared_id: str):
        """Get the shared pdf specified by the ID without being logged in."""

        shared_pdf = SharedPdf.objects.get(pk=shared_id)

        return shared_pdf


class Share(AddSharedPdfMixin, base_views.BaseAdd):
    """View for new shared PDF files."""


class Overview(OverviewMixin, base_views.BaseOverview):
    """
    View for the shared PDF overview page. It's also responsible for paginating the shared PDFs.
    """


class OverviewQuery(BaseShareMixin, base_views.BaseOverviewQuery):
    """View for performing searches and filtering on the shared PDF overview page."""

    def get(self, request: HttpRequest):
        referer_url = request.META.get('HTTP_REFERER', f'{self.obj_name}_overview')

        search_query = request.GET.get('search', '')
        remove_tag_query = request.GET.get('remove', '')
        special_selection_query = request.GET.get('selection', '')
        status_query = request.GET.get('status', '')
        password_query = request.GET.get('password', '')

        # use base class URL construction for search/tags/selection
        redirect_url = construct_query_overview_url(
            referer_url, search_query, special_selection_query, remove_tag_query, self.obj_name
        )

        # append shared-PDF-specific filter params
        extra_params = []

        if status_query:
            extra_params.append(f'status={status_query}')
        if password_query:
            extra_params.append(f'password={password_query}')

        if extra_params:
            separator = '&' if '?' in redirect_url else '?'
            redirect_url = f'{redirect_url}{separator}{"&".join(extra_params)}'

        return redirect(redirect_url)


class Delete(SharedPdfMixin, base_views.BaseDelete):
    """View for deleting the shared PDF specified by its ID."""


class Edit(EditSharedPdfMixin, base_views.BaseDetailsEdit):
    """
    The view for editing a shared PDF's name and description. The field, that is to be changed, is specified by the
    'field' argument.
    """


class Details(SharedPdfMixin, base_views.BaseDetails):
    """View for displaying the details page of a shared PDF."""


class ServeQrCode(SharedPdfMixin, base_views.BaseServe):
    """View used for serving the qr code of a shared PDF files specified by the shared PDF id"""


class DownloadQrCode(SharedPdfMixin, base_views.BaseDownload):
    """View used for downloading the qr code of a shared PDF files specified by the shared PDF id"""

    @staticmethod
    def get_suffix():  # pragma: no cover
        """
        Return svg suffix
        """

        return '.svg'


@method_decorator(login_not_required, name="dispatch")
class Serve(PdfPublicMixin, base_views.BaseServe):
    """View used for serving shared PDF files specified by the shared PDF id"""


@method_decorator(login_not_required, name="dispatch")
class Download(PdfPublicMixin, base_views.BaseDownload):
    """View for downloading the PDF specified by the ID."""


@method_decorator(login_not_required, name="dispatch")
class ViewShared(BaseSharedPdfPublicView):
    """The view responsible for displaying the shared PDF file specified by the shared PDF id in the browser."""

    def get(self, request: HttpRequest, identifier: str):
        shared_pdf = self.get_shared_pdf_public(request, identifier)

        if shared_pdf.inactive or shared_pdf.deleted:
            return render(request, 'view_shared_inactive.html')
        elif check_shared_access_allowed(shared_pdf, request.session):
            return self.render_shared_pdf_view(request, shared_pdf)
        else:
            return render(
                request,
                'view_shared_info.html',
                {'shared_pdf': shared_pdf, 'form': ViewSharedPasswordForm, 'host': request.get_host()},
            )

    def post(self, request: HttpRequest, identifier: str):
        shared_pdf = self.get_shared_pdf_public(request, identifier)

        if shared_pdf.inactive or shared_pdf.deleted:
            return render(request, 'view_shared_inactive.html')
        else:
            form = ViewSharedPasswordForm(request.POST, shared_pdf=shared_pdf)

            if not shared_pdf.password or form.is_valid():
                if not request.session or not request.session.session_key:
                    request.session.create()
                    # set session expiry to 1 week
                    request.session.set_expiry(604800)
                    request.session.save()

                shared_pdf.sessions.add(Session.objects.get(session_key=request.session.session_key))
                return redirect('view_shared_pdf', identifier=shared_pdf.id)
            else:
                return render(request, 'view_shared_info.html', {'shared_pdf': shared_pdf, 'form': form})

    @staticmethod
    def render_shared_pdf_view(request: HttpRequest, shared_pdf: SharedPdf):
        shared_pdf.views += 1
        shared_pdf.save()

        theme, theme_color = get_viewer_theme_and_color()

        return render(
            request,
            'viewer.html',
            {
                'tab_title': 'PdfDing',
                'current_page': 1,
                'shared_pdf_id': shared_pdf.id,
                'revision': shared_pdf.pdf.revision,
                'theme': theme,
                'theme_color': theme_color,
                'user_view_bool': False,
            },
        )
