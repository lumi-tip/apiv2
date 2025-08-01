import logging
import os
import re
from pathlib import Path
from typing import Any

import aiohttp
import requests
from breathecode.services.github import Github
from adrf.decorators import api_view
from adrf.views import APIView
from asgiref.sync import sync_to_async
from capyc.core.i18n import translation
from capyc.rest_framework.exceptions import ValidationException
from circuitbreaker import CircuitBreakerError
from django.core.validators import URLValidator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.clickjacking import xframe_options_exempt
from linked_services.django.service import Service
from rest_framework import status
from rest_framework.decorators import permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from slugify import slugify
from django.utils import timezone


from breathecode.admissions.models import Academy
from breathecode.authenticate.actions import get_user_language
from breathecode.authenticate.models import ProfileAcademy, User, CredentialsGithub, Token
from breathecode.notify.actions import send_email_message
from breathecode.registry.permissions.consumers import asset_by_slug
from breathecode.services.seo import SEOAnalyzer
from breathecode.utils import GenerateLookupsMixin, capable_of, consume
from breathecode.utils.api_view_extensions.api_view_extensions import APIViewExtensions
from breathecode.utils.decorators import has_permission
from breathecode.utils.decorators.capable_of import acapable_of
from breathecode.utils.views import render_message

from .actions import (
    AssetThumbnailGenerator,
    aclean_asset_readme,
    apull_from_github,
    apush_to_github,
    apush_project_or_exercise_to_github,
    ascan_asset_originality,
    atest_asset,
    test_asset,
)
from .caches import AssetCache, AssetCommentCache, CategoryCache, ContentVariableCache, KeywordCache, TechnologyCache
from .models import (
    Asset,
    AssetAlias,
    AssetCategory,
    AssetComment,
    AssetContext,
    AssetErrorLog,
    AssetImage,
    AssetKeyword,
    AssetTechnology,
    ContentVariable,
    KeywordCluster,
    OriginalityScan,
    SEOReport,
)
from .serializers import (
    AcademyAssetSerializer,
    AcademyCommentSerializer,
    AcademyErrorSerializer,
    AssetAliasSerializer,
    AssetBigAndTechnologyPublishedSerializer,
    AssetBigSerializer,
    AssetBigTechnologySerializer,
    AssetCategorySerializer,
    AssetContextSerializer,
    AssetExpandableSerializer,
    AssetImageSmallSerializer,
    AssetKeywordBigSerializer,
    AssetKeywordSerializer,
    AssetMidSerializer,
    AssetPUTMeSerializer,
    AssetPUTSerializer,
    AssetSerializer,
    AssetTechnologySerializer,
    AssetTinySerializer,
    KeywordClusterBigSerializer,
    KeywordClusterMidSerializer,
    KeywordSmallSerializer,
    OriginalityScanSerializer,
    PostAcademyAssetSerializer,
    PostAssetCommentSerializer,
    PostAssetSerializer,
    POSTCategorySerializer,
    PostKeywordClusterSerializer,
    PostKeywordSerializer,
    PutAssetCommentSerializer,
    PutAssetErrorSerializer,
    PUTCategorySerializer,
    PUTKeywordSerializer,
    SEOReportSerializer,
    TechnologyPUTSerializer,
    VariableSmallSerializer,
)
from .tasks import async_build_asset_context, async_pull_from_github, async_regenerate_asset_readme
from .utils import AssetErrorLogType, is_url, prompt_technologies

logger = logging.getLogger(__name__)

SYSTEM_EMAIL = os.getenv("SYSTEM_EMAIL", None)
ENV = os.getenv("ENV", "development")


@api_view(["GET"])
@permission_classes([AllowAny])
def forward_asset_url(request, asset_slug=None):

    asset = Asset.get_by_slug(asset_slug, request)
    if asset is None:
        return render_message(request, f"Asset with slug {asset_slug} not found")

    validator = URLValidator()
    try:

        if not asset.external and asset.asset_type == "LESSON":
            slug = Path(asset.readme_url).stem
            url = "https://4geeks.com/en/lesson/" + slug + "?plain=true"

            if ENV == "development":
                return render_message(request, "Redirect to: " + url, academy=asset.academy)
            else:
                return HttpResponseRedirect(redirect_to=url)

        validator(asset.url)
        if asset.gitpod:
            return HttpResponseRedirect(redirect_to="https://gitpod.io#" + asset.url)
        else:
            return HttpResponseRedirect(redirect_to=asset.url)
    except Exception as e:
        logger.error(e)
        msg = f"The url for the {asset.asset_type.lower()} your are trying to open ({asset_slug}) was not found, this error has been reported and will be fixed soon."
        AssetErrorLog(
            slug=AssetErrorLogType.INVALID_URL,
            path=asset_slug,
            asset=asset,
            asset_type=asset.asset_type,
            status_text=msg,
        ).save()

        return render_message(request, msg, academy=asset.academy)


@api_view(["GET"])
@permission_classes([AllowAny])
def handle_internal_link(request):
    """
    Handle internal GitHub links by proxying the content through the asset owner's credentials.

    This view receives requests for internal GitHub files and uses the asset owner's GitHub
    credentials to fetch the content, allowing access to private repository files.

    Query parameters:
    - asset: The asset ID
    - path: The relative path to the file in the repository
    - token: Bearer session token for authentication (optional for public repos)
    """

    asset_id = request.GET.get("id")
    file_path = request.GET.get("path")
    token = request.GET.get("token")

    if not asset_id:
        return render_message(request, "Missing asset parameter", status=400)

    if not file_path:
        return render_message(request, "Missing path parameter", status=400)

    # Get the asset
    try:
        asset = Asset.objects.get(id=asset_id)
    except Asset.DoesNotExist:
        return render_message(request, f"Asset with id {asset_id} not found", status=404)

    # Check if asset has an owner with GitHub credentials
    if not asset.owner:
        return render_message(request, "Asset has no owner configured", status=400)

    credentials = CredentialsGithub.objects.filter(user=asset.owner).first()
    if not credentials:
        return render_message(request, "No GitHub credentials found for asset owner", status=400)

    # Optional token validation for private access
    if token:
        try:
            valid_token = Token.objects.filter(key=token).first()
            if not valid_token or (valid_token.expires_at and valid_token.expires_at < timezone.now()):
                return render_message(request, "Invalid or expired token", status=401)
        except Exception:
            return render_message(request, "Invalid token", status=401)

    # Get repository information from asset
    try:
        org_name, repo_name, branch_name = asset.get_repo_meta()
    except Exception as e:
        logger.error(f"Error parsing repository metadata for asset {asset_id}: {str(e)}")
        return render_message(request, "Invalid repository URL in asset", status=400)

    # Use GitHub API to fetch the file content
    try:

        github = Github(credentials.token)

        # Construct the API URL for the file
        api_url = f"/repos/{org_name}/{repo_name}/contents/{file_path}"
        if branch_name:
            api_url += f"?ref={branch_name}"

        # Fetch the file content
        response = github.get(api_url)

        if "content" in response:
            # Decode the base64 content
            import base64

            content = base64.b64decode(response["content"]).decode("utf-8")

            # Determine content type based on file extension
            file_extension = Path(file_path).suffix.lower()
            content_type = "text/plain"

            if file_extension in [".md", ".markdown"]:
                content_type = "text/markdown"
            elif file_extension in [".html", ".htm"]:
                content_type = "text/html"
            elif file_extension in [".json"]:
                content_type = "application/json"
            elif file_extension in [".py"]:
                content_type = "text/x-python"
            elif file_extension in [".js"]:
                content_type = "text/javascript"
            elif file_extension in [".css"]:
                content_type = "text/css"
            elif file_extension in [".xml"]:
                content_type = "application/xml"
            elif file_extension in [".yml", ".yaml"]:
                content_type = "text/yaml"

            return HttpResponse(content, content_type=content_type)
        else:
            return render_message(request, "File content not found", status=404)

    except Exception as e:
        logger.error(f"Error fetching file from GitHub: {str(e)}")
        error_str = str(e).lower()

        if "404" in error_str or "not found" in error_str:
            return render_message(request, f"File not found: {file_path}", status=404)
        elif "403" in error_str or "forbidden" in error_str:
            return render_message(request, "Access forbidden to this file", status=403)
        elif "401" in error_str or "unauthorized" in error_str:
            return render_message(request, "GitHub authentication failed", status=401)
        else:
            return render_message(request, f"Error accessing file: {str(e)}", status=500)


@api_view(["GET"])
@permission_classes([AllowAny])
@xframe_options_exempt
def render_preview_html(request, asset_slug):
    asset = Asset.get_by_slug(asset_slug, request)
    if asset is None:
        return render_message(request, f"Asset with slug {asset_slug} not found")

    if asset.asset_type == "QUIZ":
        return render_message(request, "Quiz cannot be previewed", academy=asset.academy)

    readme = None
    try:
        readme = asset.get_readme(parse=True, silent=False)
    except Exception as e:
        return render_message(request, f"Error parsing readme for asset {asset_slug}: {str(e)}", academy=asset.academy)

    response = render(
        request,
        readme["frontmatter"]["format"] + ".html",
        {
            **AssetBigSerializer(asset).data,
            "html": readme["html"],
            "theme": request.GET.get("theme", "light"),
            "plain": request.GET.get("plain", "false"),
            "styles": readme["frontmatter"]["inlining"]["css"][0] if "inlining" in readme["frontmatter"] else None,
            "frontmatter": readme["frontmatter"].items(),
        },
    )

    # Set Content-Security-Policy header
    response["Content-Security-Policy"] = "frame-ancestors 'self' https://4geeks.com"

    return response


# Create your views here.


class TechnologyView(APIView):
    """
    View to retrieve a list of technologies or a specific technology by slug.
    """

    permission_classes = [AllowAny]
    extensions = APIViewExtensions(cache=TechnologyCache, paginate=True, sort="sort_priority")

    def get(self, request, tech_slug=None, extension=None):
        lang = request.GET.get("lang", "en")
        if lang == "en":
            lang = "us"

        handler = self.extensions(request)
        cache = handler.cache.get()
        if cache is not None:
            return cache

        if tech_slug:
            try:
                technology = AssetTechnology.objects.get(slug=tech_slug)
            except AssetTechnology.DoesNotExist:
                raise ValidationException(f"Technology with slug '{tech_slug}' not found", code=404)
            serializer = AssetBigTechnologySerializer(technology)
            return Response(serializer.data)

        # Initialize items queryset
        items = AssetTechnology.objects.all()

        # Handle parent filter
        if "parent" in request.GET:
            parent_param = request.GET.get("parent")
            if parent_param.lower() == "true":
                # Filter out all technologies that are children (have a parent)
                items = items.filter(parent__isnull=True)
            elif parent_param.isdigit():
                # Keep only technologies whose parent is an asset with that id
                items = items.filter(parent_id=parent_param)
        else:
            # Default behavior: filter out children technologies
            items = items.filter(parent__isnull=True)

        # Handle priority filter (comma-separated integers)
        if "sort_priority" in request.GET:
            priority_param = request.GET.get("sort_priority")
            try:
                # Split by comma and convert to list of integers
                priority_values = [int(p) for p in priority_param.split(",") if p.strip()]
                if priority_values:
                    items = items.filter(sort_priority__in=priority_values)
            except ValueError:
                raise ValidationException(
                    translation(
                        lang,
                        en="The priority parameter must contain comma-separated integers",
                        es="El parametro priority debe contener enteros separados por coma",
                        slug="priority-format-invalid",
                    )
                )

        if "lang" in request.GET:
            param = request.GET.get("lang")
            items = items.filter(Q(lang__iexact=param) | Q(lang="") | Q(lang__isnull=True))

        if "is_deprecated" not in request.GET or request.GET.get("is_deprecated").lower() == "false":
            items = items.filter(is_deprecated=False)

        like = request.GET.get("like", None)
        if like and like not in ["undefined", ""]:
            items = items.filter(Q(slug__icontains=like) | Q(title__icontains=like))

        if "visibility" in request.GET:
            visibility_param = request.GET.get("visibility")
            items = items.filter(visibility__iexact=visibility_param)

        items = handler.queryset(items)

        if extension == "txt":
            technologies_text = "The following technolgies are the only ones that can be use to categorize lessons, articles, quiz, projects, exercises or any other learning asset at 4Geeks: \n\n"
            technologies_text += prompt_technologies(items)

            return HttpResponse(technologies_text, content_type="text/plain")

        serializer = AssetTechnologySerializer(items, many=True)
        return handler.response(serializer.data)


class AcademyTechnologyView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(cache=TechnologyCache, sort="-slug", paginate=True)

    def _has_valid_parent(self):
        regex = r"^(?:\d+,)*(?:\d+)$"
        return bool(re.findall(regex, self.request.GET.get("parent", "")))

    @capable_of("read_technology")
    def get(self, request, academy_id=None):
        lang = get_user_language(request)
        handler = self.extensions(request)
        cache = handler.cache.get()
        if cache is not None:
            return cache

        items = AssetTechnology.objects.all()
        lookup = {}

        has_valid_parent = self._has_valid_parent()
        if self.request.GET.get("include_children") != "true" and not has_valid_parent:
            items = items.filter(parent__isnull=True)

        if "language" in self.request.GET or "lang" in self.request.GET:
            param = self.request.GET.get("language", "")
            if not param:
                param = self.request.GET.get("lang")

            if param == "en":
                param = "us"
            items = items.filter(Q(lang__iexact=param) | Q(lang="") | Q(lang__isnull=True))

        if "sort_priority" in self.request.GET:
            param = self.request.GET.get("sort_priority")
            try:
                param = int(param)

                lookup["sort_priority__iexact"] = param

            except Exception:
                raise ValidationException(
                    translation(
                        lang,
                        en="The parameter must be an integer",
                        es="El parametró debe ser un entero",
                        slug="not-an-integer",
                    )
                )

        if "visibility" in self.request.GET:
            param = self.request.GET.get("visibility")
            lookup["visibility__in"] = [p.upper() for p in param.split(",")]
        else:
            lookup["visibility"] = "PUBLIC"

        if has_valid_parent:
            param = self.request.GET.get("parent")
            lookup["parent__id__in"] = [int(p) for p in param.split(",")]

        like = request.GET.get("like", None)
        if like is not None and like != "undefined" and like != "":
            items = items.filter(Q(slug__icontains=slugify(like)) | Q(title__icontains=like))

        if slug := request.GET.get("slug"):
            lookup["slug__in"] = slug.split(",")

        if asset_slug := request.GET.get("asset_slug"):
            lookup["featured_asset__slug__in"] = asset_slug.split(",")

        if asset_type := request.GET.get("asset_type"):
            lookup["featured_asset__asset_type__in"] = asset_type.split(",")

        if "is_deprecated" not in request.GET or request.GET.get("is_deprecated").lower() == "false":
            lookup["is_deprecated"] = False

        items = items.filter(**lookup).order_by("sort_priority")
        items = handler.queryset(items)

        serializer = AssetBigTechnologySerializer(items, many=True)

        return handler.response(serializer.data)

    @capable_of("crud_technology")
    def put(self, request, tech_slug=None, academy_id=None):

        lookups = self.generate_lookups(request, many_fields=["slug"])

        if lookups and tech_slug:
            raise ValidationException(
                "user_id or cohort_id was provided in url " "in bulk mode request, use querystring style instead",
                code=400,
            )

        if "slug" not in request.GET and tech_slug is None:
            raise ValidationException("Missing technology slug(s)")
        elif tech_slug is not None:
            lookups["slug__in"] = [tech_slug]

        techs = AssetTechnology.objects.filter(**lookups).order_by("sort_priority")
        _count = techs.count()
        if _count == 0:
            raise ValidationException("This technolog(ies) does not exist for this academy", 404)

        serializers = []
        for t in techs:
            serializer = TechnologyPUTSerializer(
                t, data=request.data, many=False, context={"request": request, "academy_id": academy_id}
            )
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            serializers.append(serializer)

        resp = []
        for s in serializers:
            tech = s.save()
            resp.append(AssetBigTechnologySerializer(tech, many=False).data)

        if tech_slug is not None:
            return Response(resp.pop(), status=status.HTTP_200_OK)
        else:
            return Response(resp, status=status.HTTP_200_OK)


@api_view(["GET"])
def get_categories(request):
    items = AssetCategory.objects.filter(visibility="PUBLIC")
    serializer = AssetCategorySerializer(items, many=True)
    return Response(serializer.data)


@api_view(["GET"])
def get_keywords(request):
    items = AssetKeyword.objects.all()
    serializer = AssetKeywordSerializer(items, many=True)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([AllowAny])
def get_translations(request):
    langs = Asset.objects.all().values_list("lang", flat=True)
    langs = set(langs)

    return Response([{"slug": l, "title": l} for l in langs])


@api_view(["POST"])
@permission_classes([AllowAny])
def handle_test_asset(request):
    test_asset(request.data)
    return Response({"status": "ok"})


@api_view(["GET"])
@permission_classes([AllowAny])
@xframe_options_exempt
def render_readme(request, asset_slug, extension="raw"):

    asset = Asset.get_by_slug(asset_slug, request)
    if asset is None:
        raise ValidationException(f"Asset {asset_slug} not found", status.HTTP_404_NOT_FOUND)

    response = HttpResponse("Invalid extension format", content_type="text/html")
    if extension == "raw":
        readme = asset.get_readme()
        response = HttpResponse(readme["decoded_raw"], content_type="text/markdown")

    if extension == "html":
        if asset.html is not None and asset.html != "":
            response = HttpResponse(asset.html, content_type="text/html")
        else:
            asset.log_error(
                AssetErrorLogType.EMPTY_HTML, status_text="Someone requested the asset HTML via API and it was empty"
            )
            readme = asset.get_readme(parse=True, remove_frontmatter=request.GET.get("frontmatter", "true") != "false")
            asset.html = readme["html"]
            asset.save()
            response = HttpResponse(readme["html"], content_type="text/html")

    elif extension in ["md", "mdx", "txt"]:
        readme = asset.get_readme(parse=True, remove_frontmatter=request.GET.get("frontmatter", "true") != "false")
        response = HttpResponse(readme["decoded"], content_type="text/markdown")

    elif extension == "ipynb":
        readme = asset.get_readme()
        response = HttpResponse(readme["decoded"], content_type="application/json")

    return response


@api_view(["GET"])
@permission_classes([AllowAny])
def get_alias_redirects(request):
    aliases = AssetAlias.objects.all()
    redirects = {}

    if "academy" in request.GET:
        param = request.GET.get("academy", "")
        aliases = aliases.filter(asset__academy__id__in=param.split(","))

    for a in aliases:
        if a.slug != a.asset.slug:
            redirects[a.slug] = {"slug": a.asset.slug, "type": a.asset.asset_type, "lang": a.asset.lang}

    return Response(redirects)


@api_view(["GET"])
@permission_classes([AllowAny])
def get_config(request, asset_slug):
    asset = Asset.get_by_slug(asset_slug, request)
    if asset is None:
        raise ValidationException(f"Asset not {asset_slug} found", status.HTTP_404_NOT_FOUND)

    main_branch = "master"
    response = requests.head(f"{asset.url}/tree/{main_branch}", allow_redirects=False, timeout=2)
    if response.status_code == 302:
        main_branch = "main"

    try:
        response = requests.get(f"{asset.url}/blob/{main_branch}/learn.json?raw=true", timeout=2)
        if response.status_code == 404:
            response = requests.get(f"{asset.url}/blob/{main_branch}/bc.json?raw=true", timeout=2)
            if response.status_code == 404:
                raise ValidationException(f"Config file not found for {asset.url}", code=404, slug="config_not_found")

        return Response(response.json())
    except Exception:
        data = {
            "MESSAGE": f"learn.json or bc.json not found or invalid for for: \n {asset.url}",
            "TITLE": f"Error fetching the exercise meta-data learn.json for {asset.asset_type.lower()} {asset.slug}",
        }

        to = SYSTEM_EMAIL
        if asset.author is not None:
            to = asset.author.email

        send_email_message("message", to=to, data=data, academy=asset.academy)
        raise ValidationException(
            f"Config file invalid or not found for {asset.url}", code=404, slug="config_not_found"
        )


class AssetThumbnailView(APIView):
    """
    get:
        Get asset thumbnail.
    """

    permission_classes = [AllowAny]

    async def get(self, request, asset_slug):
        width = int(request.GET.get("width", "0"))
        height = int(request.GET.get("height", "0"))

        asset = await Asset.objects.filter(slug=asset_slug).afirst()
        generator = AssetThumbnailGenerator(asset, width, height)

        url, permanent = await generator.aget_thumbnail_url()
        return redirect(url, permanent=permanent)

    @acapable_of("crud_asset")
    async def post(self, request, asset_slug, academy_id):
        lang = get_user_language(request)

        width = int(request.GET.get("width", "0"))
        height = int(request.GET.get("height", "0"))

        asset = await Asset.objects.filter(slug=asset_slug, academy__id=academy_id).afirst()
        if asset is None:
            raise ValidationException(
                f"Asset with slug {asset_slug} not found for this academy", slug="asset-slug-not-found", code=400
            )

        generator = AssetThumbnailGenerator(asset, width, height)

        # wait one second
        try:
            asset = await generator.acreate(delay=1500)

        except CircuitBreakerError:
            raise ValidationException(
                translation(
                    lang,
                    en="The circuit breaker is open due to an error, please try again later",
                    es="El circuit breaker está abierto debido a un error, por favor intente más tarde",
                    slug="circuit-breaker-open",
                ),
                slug="circuit-breaker-open",
                data={"service": "Google Cloud Storage"},
                silent=True,
                code=503,
            )
        except aiohttp.ClientError as e:
            raise ValidationException(f"Error calling service to generate thumbnail screenshot: {str(e)}", code=500)

        serializer = AcademyAssetSerializer(asset)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AcademyContentVariableView(APIView):
    """
    get:
        Get content variables thumbnail.
    """

    extensions = APIViewExtensions(cache=ContentVariableCache, paginate=True)

    @capable_of("read_content_variables")
    def get(self, request, academy_id, variable_slug=None):
        handler = self.extensions(request)

        if variable_slug is not None:
            variable = ContentVariable.objects.filter(slug=variable_slug).first()
            if variable is None:
                raise ValidationException(
                    f"Variable {variable_slug} not found for this academy", status.HTTP_404_NOT_FOUND
                )

            serializer = VariableSmallSerializer(variable)
            return handler.response(serializer.data)

        items = ContentVariable.objects.filter(academy__id=academy_id)
        lookup = {}

        if "lang" in self.request.GET:
            param = self.request.GET.get("lang")
            lookup["lang"] = param

        items = items.filter(**lookup)
        items = handler.queryset(items)

        serializer = VariableSmallSerializer(items, many=True)

        return handler.response(serializer.data)

    # this method will force to reset the thumbnail
    @capable_of("crud_asset")
    def post(self, request, asset_slug, academy_id):
        lang = get_user_language(request)

        width = int(request.GET.get("width", "0"))
        height = int(request.GET.get("height", "0"))

        asset = Asset.objects.filter(slug=asset_slug, academy__id=academy_id).first()
        if asset is None:
            raise ValidationException(
                f"Asset with slug {asset_slug} not found for this academy", slug="asset-slug-not-found", code=400
            )

        generator = AssetThumbnailGenerator(asset, width, height)

        # wait one second
        try:
            asset = generator.create(delay=1500)

        except CircuitBreakerError:
            raise ValidationException(
                translation(
                    lang,
                    en="The circuit breaker is open due to an error, please try again later",
                    es="El circuit breaker está abierto debido a un error, por favor intente más tarde",
                    slug="circuit-breaker-open",
                ),
                slug="circuit-breaker-open",
                data={"service": "Google Cloud Storage"},
                silent=True,
                code=503,
            )

        serializer = AcademyAssetSerializer(asset)
        return Response(serializer.data, status=status.HTTP_200_OK)


# Create your views here.
class AssetView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    permission_classes = [AllowAny]
    extensions = APIViewExtensions(cache=AssetCache, sort="-published_at", paginate=True)

    def get(self, request, asset_slug=None):
        handler = self.extensions(request)

        cache = handler.cache.get()
        if cache is not None:
            return cache

        lang = get_user_language(request)

        if asset_slug is not None:
            asset = Asset.get_by_slug(asset_slug, request)
            if asset is None:
                raise ValidationException(f"Asset {asset_slug} not found", status.HTTP_404_NOT_FOUND)

            serializer = AssetBigAndTechnologyPublishedSerializer(asset)
            return handler.response(serializer.data)

        items = Asset.objects.all()
        lookup = {}
        query = handler.lookup.build(
            lang,
            strings={
                "iexact": [
                    "test_status",
                    "sync_status",
                ],
                "in": [
                    "id",
                    "difficulty",
                    "status",
                    "asset_type",
                    "category__slug",
                    "technologies__slug",
                    "seo_keywords__slug",
                ],
            },
            ids=["author", "owner"],
            bools={
                "exact": ["with_video", "interactive", "graded"],
            },
            overwrite={
                "category": "category__slug",
                "technologies": "technologies__slug",
                "seo_keywords": "seo_keywords__slug",
            },
        )

        like = request.GET.get("like", None)
        if like is not None:
            if is_url(like):
                items = items.filter(Q(readme_url__icontains=like) | Q(url__icontains=like))
            else:
                items = items.filter(
                    Q(slug__icontains=slugify(like))
                    | Q(title__icontains=like)
                    | Q(assetalias__slug__icontains=slugify(like))
                )

        if "learnpack_deploy_url" in self.request.GET:
            param = self.request.GET.get("learnpack_deploy_url")
            if param.lower() == "true":
                items = items.exclude(learnpack_deploy_url__isnull=True).exclude(learnpack_deploy_url="")
            elif param.lower() == "false":
                items = items.filter(Q(learnpack_deploy_url__isnull=True) | Q(learnpack_deploy_url=""))
            elif is_url(param):
                items = items.filter(learnpack_deploy_url=param)

        if "slug" in self.request.GET:
            asset_type = self.request.GET.get("asset_type", None)
            param = self.request.GET.get("slug")
            slugs = [s.strip() for s in param.split(",")]

            # For single slug, try to get exact asset to maintain backward compatibility
            if len(slugs) == 1:
                asset = Asset.get_by_slug(slugs[0], request, asset_type=asset_type)
                if asset is not None:
                    lookup["slug"] = asset.slug
                else:
                    lookup["slug"] = slugs[0]
            else:
                # For multiple slugs, filter by all provided slugs
                lookup["slug__in"] = slugs

        if "language" in self.request.GET:
            param = self.request.GET.get("language")
            if param == "en":
                param = "us"
            lookup["lang"] = param

        if "status" not in self.request.GET:
            lookup["status__in"] = ["PUBLISHED"]

        try:
            if "academy" in self.request.GET and self.request.GET.get("academy") not in ["null", ""]:
                param = self.request.GET.get("academy")
                lookup["academy__in"] = [int(p) for p in param.split(",")]
        except Exception:
            raise ValidationException(
                translation(
                    lang,
                    en="The academy filter value should be an integer",
                    es="El valor del filtro de academy debería ser un entero",
                    slug="academy-id-must-be-integer",
                ),
                code=400,
            )

        if "video" in self.request.GET:
            param = self.request.GET.get("video")
            if param == "true":
                lookup["with_video"] = True

        lookup["external"] = False
        if "external" in self.request.GET:
            param = self.request.GET.get("external")
            if param == "true":
                lookup["external"] = True
            elif param == "both":
                lookup.pop("external", None)

        need_translation = self.request.GET.get("need_translation", False)
        if need_translation == "true":
            items = items.annotate(num_translations=Count("all_translations")).filter(num_translations__lte=1)

        if "exclude_category" in self.request.GET:
            param = self.request.GET.get("exclude_category")
            items = items.exclude(category__slug__in=[p for p in param.split(",") if p])

        if "authors_username" in self.request.GET:
            au = self.request.GET.get("authors_username", "").split(",")
            query = Q()
            for username in au:
                query |= Q(authors_username__icontains=username)
            items = items.exclude(~query)

        items = items.filter(query, **lookup, visibility="PUBLIC").distinct()
        items = handler.queryset(items)

        expand = self.request.GET.get("expand")

        if "big" in self.request.GET:
            serializer = AssetMidSerializer(items, many=True)
        elif expand is not None:
            serializer = AssetExpandableSerializer(items, many=True, expand=expand.split(","))
        else:
            serializer = AssetSerializer(items, many=True)

        return handler.response(serializer.data)


# Create your views here.
class AssetMeView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    permission_classes = [AllowAny]
    extensions = APIViewExtensions(cache=AssetCache, sort="-published_at", paginate=True)

    def get(self, request, asset_slug=None):
        handler = self.extensions(request)

        cache = handler.cache.get()
        if cache is not None:
            return cache

        lang = get_user_language(request)

        if asset_slug is not None:
            asset = Asset.get_by_slug(asset_slug, request)
            if asset is None:
                raise ValidationException(f"Asset {asset_slug} not found", status.HTTP_404_NOT_FOUND)

            serializer = AssetBigAndTechnologyPublishedSerializer(asset)
            return handler.response(serializer.data)

        items = Asset.objects.filter(owner=request.user)
        lookup = {}
        query = handler.lookup.build(
            lang,
            strings={
                "iexact": [
                    "test_status",
                    "sync_status",
                ],
                "in": [
                    "difficulty",
                    "status",
                    "asset_type",
                    "category__slug",
                    "technologies__slug",
                    "seo_keywords__slug",
                ],
            },
            ids=["author", "owner"],
            bools={
                "exact": ["with_video", "interactive", "graded"],
            },
            overwrite={
                "category": "category__slug",
                "technologies": "technologies__slug",
                "seo_keywords": "seo_keywords__slug",
            },
        )

        like = request.GET.get("like", None)
        if like is not None:
            if is_url(like):
                items = items.filter(Q(readme_url__icontains=like) | Q(url__icontains=like))
            else:
                items = items.filter(
                    Q(slug__icontains=slugify(like))
                    | Q(title__icontains=like)
                    | Q(assetalias__slug__icontains=slugify(like))
                )

        if "learnpack_deploy_url" in self.request.GET:
            param = self.request.GET.get("learnpack_deploy_url")
            if param.lower() == "true":
                items = items.exclude(learnpack_deploy_url__isnull=True).exclude(learnpack_deploy_url="")
            elif param.lower() == "false":
                items = items.filter(Q(learnpack_deploy_url__isnull=True) | Q(learnpack_deploy_url=""))
            elif is_url(param):
                items = items.filter(learnpack_deploy_url=param)

        if "slug" in self.request.GET:
            asset_type = self.request.GET.get("asset_type", None)
            param = self.request.GET.get("slug")
            slugs = [s.strip() for s in param.split(",")]

            # For single slug, try to get exact asset to maintain backward compatibility
            if len(slugs) == 1:
                asset = Asset.get_by_slug(slugs[0], request, asset_type=asset_type)
                if asset is not None:
                    lookup["slug"] = asset.slug
                else:
                    lookup["slug"] = slugs[0]
            else:
                # For multiple slugs, filter by all provided slugs
                lookup["slug__in"] = slugs

        if "language" in self.request.GET:
            param = self.request.GET.get("language")
            if param == "en":
                param = "us"
            lookup["lang"] = param

        if "status" not in self.request.GET:
            lookup["status__in"] = ["PUBLISHED"]

        try:
            if "academy" in self.request.GET and self.request.GET.get("academy") not in ["null", ""]:
                param = self.request.GET.get("academy")
                lookup["academy__in"] = [int(p) for p in param.split(",")]
        except Exception:
            raise ValidationException(
                translation(
                    lang,
                    en="The academy filter value should be an integer",
                    es="El valor del filtro de academy debería ser un entero",
                    slug="academy-id-must-be-integer",
                ),
                code=400,
            )

        if "video" in self.request.GET:
            param = self.request.GET.get("video")
            if param == "true":
                lookup["with_video"] = True

        lookup["external"] = False
        if "external" in self.request.GET:
            param = self.request.GET.get("external")
            if param == "true":
                lookup["external"] = True
            elif param == "both":
                lookup.pop("external", None)

        need_translation = self.request.GET.get("need_translation", False)
        if need_translation == "true":
            items = items.annotate(num_translations=Count("all_translations")).filter(num_translations__lte=1)

        if "exclude_category" in self.request.GET:
            param = self.request.GET.get("exclude_category")
            items = items.exclude(category__slug__in=[p for p in param.split(",") if p])

        if "authors_username" in self.request.GET:
            au = self.request.GET.get("authors_username", "").split(",")
            query = Q()
            for username in au:
                query |= Q(authors_username__icontains=username)
            items = items.exclude(~query)

        items = items.filter(query, **lookup, visibility="PUBLIC").distinct()
        items = handler.queryset(items)

        expand = self.request.GET.get("expand")

        if "big" in self.request.GET:
            serializer = AssetMidSerializer(items, many=True)
        elif expand is not None:
            serializer = AssetExpandableSerializer(items, many=True, expand=expand.split(","))
        else:
            serializer = AssetSerializer(items, many=True)

        return handler.response(serializer.data)

    @has_permission("learnpack_create_package")
    def put(self, request, asset_slug=None):

        logged_in_user = request.user
        data_list = request.data
        if not isinstance(request.data, list):

            # make it a list
            data_list = [request.data]

            if asset_slug is None:
                raise ValidationException("Missing asset_slug")

            asset = Asset.objects.filter(slug__iexact=asset_slug, owner=logged_in_user).first()
            if asset is None:
                raise ValidationException(f"This asset {asset_slug} does not exist or you don't have access to it", 404)

            data_list[0]["id"] = asset.id

        all_assets = []
        for data in data_list:

            if "technologies" in data and len(data["technologies"]) > 0 and isinstance(data["technologies"][0], str):
                found_technology_slugs = list(
                    AssetTechnology.objects.filter(slug__in=data["technologies"]).values_list("slug", flat=True)
                )
                not_found_technologies = [slug for slug in data["technologies"] if slug not in found_technology_slugs]

                if not_found_technologies:
                    raise ValidationException(
                        f"The following technologies were not found: {', '.join(not_found_technologies)}"
                    )

                technology_ids = AssetTechnology.objects.filter(slug__in=data["technologies"]).values_list(
                    "pk", flat=True
                )
                data["technologies"] = technology_ids

            if "seo_keywords" in data and len(data["seo_keywords"]) > 0:
                if isinstance(data["seo_keywords"][0], str):
                    data["seo_keywords"] = AssetKeyword.objects.filter(slug__in=data["seo_keywords"]).values_list(
                        "pk", flat=True
                    )

            if (
                "all_translations" in data
                and len(data["all_translations"]) > 0
                and isinstance(data["all_translations"][0], str)
            ):
                data["all_translations"] = Asset.objects.filter(slug__in=data["all_translations"]).values_list(
                    "pk", flat=True
                )

            if "id" not in data:
                raise ValidationException("Cannot determine asset id", slug="without-id")

            instance = Asset.objects.filter(id=data["id"], owner=logged_in_user).first()
            if not instance:
                raise ValidationException(
                    f"Asset({data["id"]}) does not exist or you don't have access to it", code=404, slug="not-found"
                )
            all_assets.append(instance)

        all_serializers = []
        index = -1
        for data in data_list:
            index += 1
            serializer = AssetPUTMeSerializer(all_assets[index], data=data, context={"request": request})
            all_serializers.append(serializer)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        all_assets = []
        for serializer in all_serializers:
            all_assets.append(serializer.save())

        if isinstance(request.data, list):
            serializer = AcademyAssetSerializer(all_assets, many=True)
        else:
            serializer = AcademyAssetSerializer(all_assets.pop(), many=False)

        return Response(serializer.data, status=status.HTTP_200_OK)

    @has_permission("learnpack_create_package")
    def post(self, request):

        data = {
            **request.data,
        }

        if "seo_keywords" in data and len(data["seo_keywords"]) > 0:
            if isinstance(data["seo_keywords"][0], str):
                data["seo_keywords"] = AssetKeyword.objects.filter(slug__in=data["seo_keywords"]).values_list(
                    "pk", flat=True
                )

        if (
            "all_translations" in data
            and len(data["all_translations"]) > 0
            and isinstance(data["all_translations"][0], str)
        ):
            data["all_translations"] = Asset.objects.filter(slug__in=data["all_translations"]).values_list(
                "pk", flat=True
            )

        if "technologies" in data and len(data["technologies"]) > 0 and isinstance(data["technologies"][0], str):
            found_technology_slugs = list(
                AssetTechnology.objects.filter(slug__in=data["technologies"]).values_list("slug", flat=True)
            )
            not_found_technologies = [slug for slug in data["technologies"] if slug not in found_technology_slugs]

            if not_found_technologies:
                raise ValidationException(
                    f"The following technologies were not found: {', '.join(not_found_technologies)}"
                )

            technology_ids = (
                AssetTechnology.objects.filter(slug__in=data["technologies"])
                .values_list("pk", flat=True)
                .order_by("sort_priority")
            )
            data["technologies"] = technology_ids

        serializer = PostAssetSerializer(data=data, context={"request": request})
        if serializer.is_valid():
            instance = serializer.save()
            # only pull if the readme raw is not already set
            if instance.readme_url and "github.com" in instance.readme_url and instance.readme_raw is None:
                async_pull_from_github.delay(instance.slug)
            return Response(AssetBigSerializer(instance).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AssetContextView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    permission_classes = [AllowAny]
    extensions = APIViewExtensions(cache=AssetCache, paginate=True)

    def get(self, request, asset_id=None):
        handler = self.extensions(request)

        cache = handler.cache.get()
        if cache is not None:
            return cache

        asset_context = AssetContext.objects.filter(asset__id=asset_id).first()
        if asset_context is None:
            asset = Asset.objects.filter(id=asset_id).first()
            if asset is None:
                raise ValidationException(
                    f"Asset {asset_id} not found", status.HTTP_404_NOT_FOUND, slug="asset-not-found"
                )
            asset_context = AssetContext(
                asset=asset,
                status="PROCESSING",
                status_text="The asset context is being generated in the background and will be ready in a moment.",
            )
            asset_context.save()
            async_build_asset_context.delay(asset_id)

        serializer = AssetContextSerializer(asset_context, many=False)

        return handler.response(serializer.data)


# Create your views here.
class AcademyAssetActionView(APIView):
    """
    List all snippets, or create a new snippet.
    """

    @staticmethod
    async def update_asset_action(asset: Asset, user: User, data: dict[str, Any], academy_id: int):
        """
        This function updates the asset type based on the action slug
        """

        action_slug = data.get("action_slug")

        possible_actions = ["test", "pull", "push", "analyze_seo", "clean", "originality", "claim_asset", "create_repo"]
        if action_slug not in possible_actions:
            raise ValidationException(f"Invalid action {action_slug}")
        try:
            if action_slug == "test":
                await atest_asset(asset)
            elif action_slug == "clean":
                async_regenerate_asset_readme.delay(asset.slug)
            elif action_slug == "pull":
                override_meta = False
                if data and "override_meta" in data:
                    override_meta = data["override_meta"]
                await apull_from_github(asset.slug, override_meta=override_meta)
            elif action_slug == "push":
                if asset.asset_type not in ["ARTICLE", "LESSON", "QUIZ"]:
                    raise ValidationException(
                        f"Asset type {asset.asset_type} cannot be pushed to GitHub, please update the Github repository manually"
                    )

                await apush_to_github(asset.slug, owner=user)
            elif action_slug == "create_repo":
                if asset.asset_type not in ["PROJECT", "EXERCISE"]:
                    raise ValidationException(
                        f"Asset type {asset.asset_type} cannot use create_repo action. Only PROJECT and EXERCISE assets can create repositories."
                    )

                # Extract organization_github_username from data if provided
                organization_github_username = data.get("organization_github_username")
                await apush_project_or_exercise_to_github(
                    asset.slug, create_or_update=True, organization_github_username=organization_github_username
                )
            elif action_slug == "analyze_seo":
                report = SEOAnalyzer(asset)
                await report.astart()
            elif action_slug == "originality":

                if asset.asset_type not in ["ARTICLE", "LESSON"]:
                    raise ValidationException("Only lessons and articles can be scanned for originality")
                await ascan_asset_originality(asset)
            elif action_slug == "claim_asset":

                if asset.academy is not None:
                    raise ValidationException(
                        translation(
                            "en",
                            en=f"Asset {asset.slug} already belongs to academy {asset.academy.name}",
                            es=f"El asset {asset.slug} ya pertenece a la academia {asset.academy.name}",
                            slug="asset-already-claimed",
                        )
                    )
                academy = await Academy.objects.filter(id=academy_id).afirst()
                asset.academy = academy
                await asset.asave()

        except Exception as e:
            logger.exception(e)
            if isinstance(e, Exception):
                raise ValidationException(str(e))

            raise ValidationException(
                "; ".join([k.capitalize() + ": " + "".join(v) for k, v in e.message_dict.items()])
            )

        # Using sync_to_async is still needed because Serpy serializers might perform synchronous operations.
        # Prefetching reduces the chance of implicit sync DB calls within the serializer.
        serializer = AcademyAssetSerializer(asset)
        data = await sync_to_async(
            lambda: serializer.data
        )()  # Use a lambda to ensure serializer.data is called within sync_to_async
        return data

    @acapable_of("crud_asset")
    async def put(self, request, asset_slug, action_slug, academy_id=None):

        if asset_slug is None:
            raise ValidationException("Missing asset_slug")

        # Fetch asset asynchronously, prefetching related fields needed by the serializer
        asset = (
            await Asset.objects.select_related("category", "assessment", "author", "owner", "academy")
            .prefetch_related(
                # Prefetching previous_version might need adjustment if it causes deep recursion issues
                "seo_keywords__cluster",
                "previous_version",
                "all_translations",
                "technologies",
                "assets_related",
            )
            .filter(slug__iexact=asset_slug)
            .filter(Q(academy__id=academy_id) | Q(academy__isnull=True))
            .afirst()
        )

        if asset is None:
            raise ValidationException(f"This asset {asset_slug} does not exist for this academy {academy_id}", 404)

        data = await self.update_asset_action(
            asset, request.user, {**request.data, "action_slug": action_slug}, academy_id
        )
        return Response(data, status=status.HTTP_200_OK)

    @staticmethod
    async def create_asset_action(action_slug: str, asset: Asset, user: User, data: dict[str, Any], academy_id: int):
        """
        This function creates a new asset
        """

        try:
            if action_slug == "test":
                await atest_asset(asset)
            elif action_slug == "clean":
                await aclean_asset_readme(asset, silent=False)
            elif action_slug == "pull":
                override_meta = False
                if data and "override_meta" in data:
                    override_meta = data["override_meta"]
                await apull_from_github(asset.slug, override_meta=override_meta)
            elif action_slug == "push":
                if asset.asset_type not in ["ARTICLE", "LESSON"]:
                    raise ValidationException(
                        "Only lessons and articles and be pushed to github, please update the Github repository yourself and come back to pull the changes from here"
                    )

                await apush_to_github(asset.slug, owner=user)
            elif action_slug == "create_repo":
                if asset.asset_type not in ["PROJECT", "EXERCISE"]:
                    raise ValidationException(
                        f"Asset type {asset.asset_type} cannot use create_repo action. Only PROJECT and EXERCISE assets can create repositories."
                    )

                # Extract organization_github_username from data if provided
                organization_github_username = data.get("organization_github_username")
                await apush_project_or_exercise_to_github(
                    asset.slug, create_or_update=True, organization_github_username=organization_github_username
                )
            elif action_slug == "analyze_seo":
                report = SEOAnalyzer(asset)
                await report.astart()
            elif action_slug == "claim_asset":

                if asset.academy is not None:
                    raise ValidationException(
                        translation(
                            "en",
                            en=f"Asset {asset.slug} already belongs to academy {asset.academy.name}",
                            es=f"El asset {asset.slug} ya pertenece a la academia {asset.academy.name}",
                            slug="asset-already-claimed",
                        )
                    )
                academy = await Academy.objects.filter(id=academy_id).afirst()
                asset.academy = academy
                await asset.asave()

            return True

        except Exception as e:
            logger.exception(e)
            return False

    @acapable_of("crud_asset")
    async def post(self, request, action_slug, academy_id=None):
        if action_slug not in ["test", "pull", "push", "analyze_seo", "claim_asset", "create_repo"]:
            raise ValidationException(f"Invalid action {action_slug}")

        # Check if 'assets' key exists
        if "assets" not in request.data:
            raise ValidationException("Assets not found in the body of the request.")

        assets = request.data["assets"]

        # Check if the assets list is empty
        if not assets:
            raise ValidationException("The list of Assets is empty.")

        invalid_assets = []

        for asset_slug in assets:
            asset = (
                await Asset.objects.select_related("academy")
                .filter(slug__iexact=asset_slug)
                .filter(Q(academy__id=academy_id) | Q(academy__isnull=True))
                .afirst()
            )
            if asset is None:
                invalid_assets.append(asset_slug)
                continue

            if not await self.create_asset_action(action_slug, asset, request.user, request.data, academy_id):
                invalid_assets.append(asset_slug)

        pulled_assets = list(set(assets).difference(set(invalid_assets)))

        if len(pulled_assets) < 1:
            raise ValidationException(f"Failed to {action_slug} for these assets: {invalid_assets}")

        return Response(
            f'These asset readmes were pulled correctly from GitHub: {pulled_assets}. {f"These assets {invalid_assets} do not exist for this academy {academy_id}" if len(invalid_assets) > 0 else ""}',
            status=status.HTTP_200_OK,
        )


# Create your views here.
class AcademyAssetSEOReportView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(sort="-created_at", paginate=True)

    @capable_of("read_asset")
    def get(self, request, asset_slug, academy_id):

        handler = self.extensions(request)

        reports = SEOReport.objects.filter(asset__slug=asset_slug)
        if reports.count() == 0:
            raise ValidationException(f"No report found for asset {asset_slug}", status.HTTP_404_NOT_FOUND)

        reports = handler.queryset(reports)
        serializer = SEOReportSerializer(reports, many=True)
        return handler.response(serializer.data)


# Create your views here.
class AcademyAssetOriginalityView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(sort="-created_at", paginate=True)

    @capable_of("read_asset")
    def get(self, request, asset_slug, academy_id):

        handler = self.extensions(request)

        scans = OriginalityScan.objects.filter(asset__slug=asset_slug)
        if scans.count() == 0:
            raise ValidationException(f"No originality scans found for asset {asset_slug}", status.HTTP_404_NOT_FOUND)

        scans = scans.order_by("-created_at")

        scans = handler.queryset(scans)
        serializer = OriginalityScanSerializer(scans, many=True)
        return handler.response(serializer.data)


class AssetSupersedesView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    @capable_of("read_asset")
    def get(self, request, asset_slug=None, academy_id=None):

        asset = Asset.get_by_slug(asset_slug, request)

        supersedes = []
        _aux = asset
        while _aux.superseded_by is not None:
            supersedes.append(_aux.superseded_by)
            _aux = _aux.superseded_by

        previous = []
        _aux = asset
        try:
            while _aux.previous_version is not None:
                previous.append(_aux.previous_version)
                _aux = _aux.previous_version
        except Exception:
            pass

        return Response(
            {
                "supersedes": AssetTinySerializer(supersedes, many=True).data,
                "previous": AssetTinySerializer(previous, many=True).data,
            }
        )


class AcademyAssetView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(cache=AssetCache, sort="-published_at", paginate=True)

    def _get_asset_by_slug_or_id(self, asset_slug_or_id, academy_id, request=None):
        """
        Helper method to retrieve an asset by either slug or ID.

        Args:
            asset_slug_or_id: The asset identifier (slug or ID)
            academy_id: The academy ID to filter by
            request: The request object (for compatibility with get_by_slug)

        Returns:
            Asset instance or None if not found
        """
        if asset_slug_or_id.isdigit():
            # It's an ID
            return Asset.objects.filter(id=int(asset_slug_or_id), academy__id=academy_id).first()
        else:
            # It's a slug - use the existing get_by_slug method with academy filter
            asset = Asset.get_by_slug(asset_slug_or_id, request)
            if asset is None or (asset.academy is not None and asset.academy.id != int(academy_id)):
                return None
            return asset

    @capable_of("read_asset")
    def get(self, request, asset_slug_or_id=None, academy_id=None):
        handler = self.extensions(request)

        cache = handler.cache.get()
        if cache is not None:
            return cache

        member = ProfileAcademy.objects.filter(user=request.user, academy__id=academy_id).first()
        if member is None:
            raise ValidationException("You don't belong to this academy", status.HTTP_400_BAD_REQUEST)

        if asset_slug_or_id is not None:
            asset = self._get_asset_by_slug_or_id(asset_slug_or_id, academy_id, request)
            if asset is None:
                raise ValidationException(
                    f"Asset {asset_slug_or_id} not found for this academy", status.HTTP_404_NOT_FOUND
                )

            serializer = AcademyAssetSerializer(asset)
            return handler.response(serializer.data)

        items = Asset.objects.filter(Q(academy__id=academy_id) | Q(academy__isnull=True))

        lookup = {}

        if member.role.slug == "content_writer":
            items = items.filter(author__id=request.user.id)
        elif "author" in self.request.GET:
            param = self.request.GET.get("author")
            lookup["author__id"] = param

        if "owner" in self.request.GET:
            param = self.request.GET.get("owner")
            lookup["owner__id"] = param

        like = request.GET.get("like", None)
        if like is not None:
            if is_url(like):
                items = items.filter(Q(readme_url__icontains=like) | Q(url__icontains=like))
            else:
                items = items.filter(
                    Q(slug__icontains=slugify(like))
                    | Q(title__icontains=like)
                    | Q(assetalias__slug__icontains=slugify(like))
                )

        if "asset_type" in self.request.GET:
            param = self.request.GET.get("asset_type")
            lookup["asset_type__iexact"] = param

        if "category" in self.request.GET:
            param = self.request.GET.get("category")
            lookup["category__slug__in"] = [p.lower() for p in param.split(",")]

        if "test_status" in self.request.GET:
            param = self.request.GET.get("test_status")
            lookup["test_status"] = param.upper()

        if "sync_status" in self.request.GET:
            param = self.request.GET.get("sync_status")
            lookup["sync_status"] = param.upper()

        if "slug" in self.request.GET:
            asset_type = self.request.GET.get("asset_type", None)
            param = self.request.GET.get("slug")
            asset = Asset.get_by_slug(param, request, asset_type=asset_type)
            if asset is not None:
                lookup["slug"] = asset.slug
            else:
                lookup["slug"] = param

        if "language" in self.request.GET or "lang" in self.request.GET:
            param = self.request.GET.get("language")
            if not param:
                param = self.request.GET.get("lang")

            if param == "en":
                param = "us"
            lookup["lang"] = param

        if "visibility" in self.request.GET:
            param = self.request.GET.get("visibility")
            lookup["visibility__in"] = [p.upper() for p in param.split(",")]
        else:
            lookup["visibility"] = "PUBLIC"

        if "technologies" in self.request.GET:
            param = self.request.GET.get("technologies")
            lookup["technologies__slug__in"] = [p.lower() for p in param.split(",")]

        if "keywords" in self.request.GET:
            param = self.request.GET.get("keywords")
            items = items.filter(seo_keywords__slug__in=[p.lower() for p in param.split(",")])

        if "status" in self.request.GET:
            param = self.request.GET.get("status")
            lookup["status__in"] = [p.upper() for p in param.split(",")]
        else:
            items = items.exclude(status="DELETED")

        if "sync_status" in self.request.GET:
            param = self.request.GET.get("sync_status")
            lookup["sync_status__in"] = [p.upper() for p in param.split(",")]

        if "video" in self.request.GET:
            param = self.request.GET.get("video")
            if param == "true":
                lookup["with_video"] = True

        if "interactive" in self.request.GET:
            param = self.request.GET.get("interactive")
            if param == "true":
                lookup["interactive"] = True

        if "graded" in self.request.GET:
            param = self.request.GET.get("graded")
            if param == "true":
                lookup["graded"] = True

        lookup["external"] = False
        if "external" in self.request.GET:
            param = self.request.GET.get("external")
            if param == "true":
                lookup["external"] = True
            elif param == "both":
                lookup.pop("external", None)

        if "superseded_by" in self.request.GET:
            param = self.request.GET.get("superseded_by")
            if param.lower() in ["none", "null"]:
                lookup["superseded_by__isnull"] = True
            else:
                if param.isnumeric():
                    lookup["superseded_by__id"] = param
                else:
                    lookup["superseded_by__slug"] = param

        if "previous_version" in self.request.GET:
            param = self.request.GET.get("previous_version")
            if param.lower() in ["none", "null"]:
                lookup["previous_version__isnull"] = True
            else:
                if param.isnumeric():
                    lookup["previous_version__id"] = param
                else:
                    lookup["previous_version__slug"] = param

        published_before = request.GET.get("published_before", "")
        if published_before != "":
            items = items.filter(published_at__lte=published_before)

        published_after = request.GET.get("published_after", "")
        if published_after != "":
            items = items.filter(published_at__gte=published_after)

        need_translation = self.request.GET.get("need_translation", False)
        if need_translation == "true":
            items = items.annotate(num_translations=Count("all_translations")).filter(num_translations__lte=1)

        items = items.filter(**lookup).distinct()
        items = handler.queryset(items)

        serializer = AcademyAssetSerializer(items, many=True)

        return handler.response(serializer.data)

    @capable_of("crud_asset")
    def put(self, request, asset_slug_or_id=None, academy_id=None):

        data_list = request.data
        if not isinstance(request.data, list):

            # make it a list
            data_list = [request.data]

            if asset_slug_or_id is None:
                raise ValidationException("Missing asset_slug_or_id")

            # Use helper method to find asset by slug or ID
            asset = self._get_asset_by_slug_or_id(asset_slug_or_id, academy_id, request)
            if asset is None:
                raise ValidationException(
                    f"This asset {asset_slug_or_id} does not exist for this academy {academy_id}", 404
                )

            data_list[0]["id"] = asset.id

        all_assets = []
        for data in data_list:

            if "technologies" in data and len(data["technologies"]) > 0 and isinstance(data["technologies"][0], str):
                found_technology_slugs = list(
                    AssetTechnology.objects.filter(slug__in=data["technologies"]).values_list("slug", flat=True)
                )
                not_found_technologies = [slug for slug in data["technologies"] if slug not in found_technology_slugs]

                if not_found_technologies:
                    raise ValidationException(
                        f"The following technologies were not found: {', '.join(not_found_technologies)}"
                    )

                technology_ids = AssetTechnology.objects.filter(slug__in=data["technologies"]).values_list(
                    "pk", flat=True
                )
                data["technologies"] = technology_ids

            if "seo_keywords" in data and len(data["seo_keywords"]) > 0:
                if isinstance(data["seo_keywords"][0], str):
                    data["seo_keywords"] = AssetKeyword.objects.filter(slug__in=data["seo_keywords"]).values_list(
                        "pk", flat=True
                    )

            if (
                "all_translations" in data
                and len(data["all_translations"]) > 0
                and isinstance(data["all_translations"][0], str)
            ):
                data["all_translations"] = Asset.objects.filter(slug__in=data["all_translations"]).values_list(
                    "pk", flat=True
                )

            if "id" not in data:
                raise ValidationException("Cannot determine asset id", slug="without-id")

            instance = Asset.objects.filter(id=data["id"], academy__id=academy_id).first()
            if not instance:
                raise ValidationException(
                    f'Asset({data["id"]}) does not exist on this academy', code=404, slug="not-found"
                )
            all_assets.append(instance)

        all_serializers = []
        index = -1
        for data in data_list:
            index += 1
            serializer = AssetPUTSerializer(
                all_assets[index], data=data, context={"request": request, "academy_id": academy_id}
            )
            all_serializers.append(serializer)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        all_assets = []
        for serializer in all_serializers:
            all_assets.append(serializer.save())

        if isinstance(request.data, list):
            serializer = AcademyAssetSerializer(all_assets, many=True)
        else:
            serializer = AcademyAssetSerializer(all_assets.pop(), many=False)

        return Response(serializer.data, status=status.HTTP_200_OK)

    @capable_of("crud_asset")
    def post(self, request, academy_id=None):

        data = {
            **request.data,
        }

        if "seo_keywords" in data and len(data["seo_keywords"]) > 0:
            if isinstance(data["seo_keywords"][0], str):
                data["seo_keywords"] = AssetKeyword.objects.filter(slug__in=data["seo_keywords"]).values_list(
                    "pk", flat=True
                )

        if (
            "all_translations" in data
            and len(data["all_translations"]) > 0
            and isinstance(data["all_translations"][0], str)
        ):
            data["all_translations"] = Asset.objects.filter(slug__in=data["all_translations"]).values_list(
                "pk", flat=True
            )

        if "technologies" in data and len(data["technologies"]) > 0 and isinstance(data["technologies"][0], str):
            found_technology_slugs = list(
                AssetTechnology.objects.filter(slug__in=data["technologies"]).values_list("slug", flat=True)
            )
            not_found_technologies = [slug for slug in data["technologies"] if slug not in found_technology_slugs]

            if not_found_technologies:
                raise ValidationException(
                    f"The following technologies were not found: {', '.join(not_found_technologies)}"
                )

            technology_ids = (
                AssetTechnology.objects.filter(slug__in=data["technologies"])
                .values_list("pk", flat=True)
                .order_by("sort_priority")
            )
            data["technologies"] = technology_ids

        serializer = PostAcademyAssetSerializer(data=data, context={"request": request, "academy": academy_id})
        if serializer.is_valid():
            instance = serializer.save()
            # only pull if the readme raw is not already set
            if instance.readme_url and "github.com" in instance.readme_url and instance.readme_raw is None:
                async_pull_from_github.delay(instance.slug)
            return Response(AssetBigSerializer(instance).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class V2AcademyAssetView(APIView):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(cache=AssetCache, sort="-published_at", paginate=True)

    @capable_of("read_asset")
    @consume("read_lesson", consumer=asset_by_slug)
    def get(self, request, asset: Asset, academy: Academy):
        serializer = AcademyAssetSerializer(asset)
        return Response(serializer.data)


class AssetImageView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(sort="-created_at", paginate=True)

    @capable_of("read_asset")
    def get(self, request, academy_id=None):

        handler = self.extensions(request)

        items = AssetImage.objects.filter(assets__academy__id=academy_id)
        lookup = {}

        if "slug" in self.request.GET:
            param = self.request.GET.get("slug")
            lookup["assets__slug__in"] = [p.lower() for p in param.split(",")]

        if "download_status" in self.request.GET:
            param = self.request.GET.get("download_status")
            lookup["download_status__in"] = [p.upper() for p in param.split(",")]

        if "original_url" in self.request.GET:
            param = self.request.GET.get("original_url")
            lookup["original_url"] = param

        items = items.filter(**lookup)
        items = handler.queryset(items)

        serializer = AssetImageSmallSerializer(items, many=True)
        return handler.response(serializer.data)


class AcademyAssetCommentView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(cache=AssetCommentCache, sort="-created_at", paginate=True)

    @capable_of("read_asset")
    def get(self, request, academy_id=None):

        handler = self.extensions(request)
        cache = handler.cache.get()
        if cache is not None:
            return cache

        items = AssetComment.objects.filter(asset__academy__id=academy_id)
        lookup = {}

        if "asset" in self.request.GET:
            param = self.request.GET.get("asset")
            lookup["asset__slug__in"] = [p.lower() for p in param.split(",")]

        if "resolved" in self.request.GET:
            param = self.request.GET.get("resolved")
            if param == "true":
                lookup["resolved"] = True
            elif param == "false":
                lookup["resolved"] = False

        if "delivered" in self.request.GET:
            param = self.request.GET.get("delivered")
            if param == "true":
                lookup["delivered"] = True
            elif param == "false":
                lookup["delivered"] = False

        if "owner" in self.request.GET:
            param = self.request.GET.get("owner")
            lookup["owner__email"] = param

        if "author" in self.request.GET:
            param = self.request.GET.get("author")
            lookup["author__email"] = param

        items = items.filter(**lookup)
        items = handler.queryset(items)

        serializer = AcademyCommentSerializer(items, many=True)
        return handler.response(serializer.data)

    @capable_of("crud_asset")
    def post(self, request, academy_id=None):

        payload = {**request.data, "author": request.user.id}

        serializer = PostAssetCommentSerializer(data=payload, context={"request": request, "academy": academy_id})
        if serializer.is_valid():
            serializer.save()
            serializer = AcademyCommentSerializer(serializer.instance)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("crud_asset")
    def put(self, request, comment_id, academy_id=None):

        if comment_id is None:
            raise ValidationException("Missing comment_id")

        comment = AssetComment.objects.filter(id=comment_id, asset__academy__id=academy_id).first()
        if comment is None:
            raise ValidationException("This comment does not exist for this academy", 404)

        data = {**request.data}
        if "status" in request.data and request.data["status"] == "NOT_STARTED":
            data["author"] = None

        serializer = PutAssetCommentSerializer(comment, data=data, context={"request": request, "academy": academy_id})
        if serializer.is_valid():
            serializer.save()
            serializer = AcademyCommentSerializer(serializer.instance)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("crud_asset")
    def delete(self, request, comment_id=None, academy_id=None):

        if comment_id is None:
            raise ValidationException("Missing comment ID on the URL", 404)

        comment = AssetComment.objects.filter(id=comment_id, asset__academy__id=academy_id).first()
        if comment is None:
            raise ValidationException("This comment does not exist", 404)

        comment.delete()
        return Response(None, status=status.HTTP_204_NO_CONTENT)


class AcademyAssetErrorView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(sort="-created_at", paginate=True)

    @capable_of("read_asset_error")
    def get(self, request, academy_id=None):

        handler = self.extensions(request)
        # cache = handler.cache.get()
        # if cache is not None:
        #     return cache

        items = AssetErrorLog.objects.filter(Q(asset__academy__id=academy_id) | Q(asset__isnull=True))
        lookup = {}

        if "asset" in self.request.GET:
            param = self.request.GET.get("asset")
            lookup["asset__slug__in"] = [p.lower() for p in param.split(",")]

        if "slug" in self.request.GET:
            param = self.request.GET.get("slug")
            lookup["slug__in"] = [p.lower() for p in param.split(",")]

        if "status" in self.request.GET:
            param = self.request.GET.get("status")
            lookup["status__in"] = [p.upper() for p in param.split(",")]

        if "asset_type" in self.request.GET:
            param = self.request.GET.get("asset_type")
            lookup["asset_type__in"] = [p.upper() for p in param.split(",")]

        items = items.filter(**lookup)

        like = request.GET.get("like", None)
        if like is not None and like != "undefined" and like != "":
            items = items.filter(Q(slug__icontains=slugify(like)) | Q(path__icontains=like))

        items = handler.queryset(items)

        serializer = AcademyErrorSerializer(items, many=True)
        return handler.response(serializer.data)

    @capable_of("crud_asset_error")
    def put(self, request, academy_id=None):

        data_list = request.data
        if not isinstance(request.data, list):
            data_list = [request.data]

        all_errors = []
        for data in data_list:
            error_id = data.get("id")
            if not error_id:
                raise ValidationException("Missing error id")

            error = AssetErrorLog.objects.filter(
                Q(id=error_id) & (Q(asset__academy__id=academy_id) | Q(asset__isnull=True))
            ).first()
            if error is None:
                raise ValidationException(f"This error with id {error_id} does not exist for this academy", 404)

            serializer = PutAssetErrorSerializer(error, data=data, context={"request": request, "academy": academy_id})
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            all_errors.append(serializer)

        for serializer in all_errors:
            serializer.save()

        return Response([serializer.data for serializer in all_errors], status=status.HTTP_200_OK)

    @capable_of("crud_asset_error")
    def delete(self, request, error_id=None, academy_id=None):

        lookups = self.generate_lookups(request, many_fields=["id"])
        if not lookups and not error_id:
            raise ValidationException("provide arguments in the url", code=400, slug="without-lookups-and-error-id")

        if lookups and error_id:
            raise ValidationException(
                "error_id in url " "in bulk mode request, use querystring style instead",
                code=400,
                slug="lookups-and-error-id-together",
            )

        if error_id:
            error = AssetErrorLog.objects.filter(id=error_id, asset__academy__id=academy_id).first()
            if error is None:
                raise ValidationException("This error does not exist", 404)

            error.delete()

        if lookups:
            items = AssetErrorLog.objects.filter(**lookups)
            items.delete()

        return Response(None, status=status.HTTP_204_NO_CONTENT)


class AcademyAssetAliasView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(sort="-created_at", paginate=True)

    @capable_of("read_asset")
    def get(self, request, alias_slug=None, academy_id=None):

        handler = self.extensions(request)
        # cache = handler.cache.get()
        # if cache is not None:
        #     return cache

        lang = get_user_language(request)

        if alias_slug:
            item = AssetAlias.objects.filter(slug=alias_slug, asset__academy__id=academy_id).first()
            if not item:
                raise ValidationException(
                    translation(
                        lang,
                        en="Asset alias with slug {alias_slug} not found for this academy",
                        es="No se ha encontrado el alias {alias_slug} para esta academia",
                        slug="not-found",
                    )
                )

            serializer = AssetAliasSerializer(item, many=False)
            return handler.response(serializer.data)

        items = AssetAlias.objects.filter(asset__academy__id=academy_id)
        lookup = {}

        if "asset" in self.request.GET:
            param = self.request.GET.get("asset")
            lookup["asset__slug__in"] = [p.lower() for p in param.split(",")]

        items = items.filter(**lookup)
        items = handler.queryset(items)

        serializer = AssetAliasSerializer(items, many=True)
        return handler.response(serializer.data)

    @capable_of("crud_asset")
    def delete(self, request, alias_slug=None, academy_id=None):
        lang = get_user_language(request)
        if not alias_slug:
            raise ValidationException(
                translation(
                    lang,
                    en="Missing alias slug",
                    es="Especifica el slug del alias que deseas eliminar",
                    slug="missing-alias-slug",
                )
            )

        item = AssetAlias.objects.filter(slug=alias_slug, asset__academy__id=academy_id).first()
        if not item:
            raise ValidationException(
                translation(
                    lang,
                    en=f"Asset alias with slug {alias_slug} not found for this academy",
                    es=f"No se ha encontrado el alias {alias_slug} para esta academia",
                    slug="not-found",
                )
            )

        if item.asset.slug == item.slug:
            raise ValidationException(
                translation(
                    lang,
                    en="Rename the asset slug before deleting this alias",
                    es="Necesitas renombrar el slug principal del asset antes de eliminar este alias",
                    slug="rename-asset-slug",
                )
            )

        item.delete()

        return Response(None, status=status.HTTP_204_NO_CONTENT)


class AcademyCategoryView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(cache=CategoryCache, sort="-created_at", paginate=True)

    @capable_of("read_category")
    def get(self, request, category_slug=None, academy_id=None):

        handler = self.extensions(request)
        cache = handler.cache.get()
        if cache is not None:
            return cache

        items = AssetCategory.objects.filter(academy__id=academy_id)
        lookup = {}

        like = request.GET.get("like", None)
        if like is not None and like != "undefined" and like != "":
            items = items.filter(Q(slug__icontains=slugify(like)) | Q(title__icontains=like))

        lang = request.GET.get("lang", None)
        if lang is not None:
            items = items.filter(lang__iexact=lang)

        items = items.filter(**lookup)
        items = handler.queryset(items)

        serializer = AssetCategorySerializer(items, many=True)
        return handler.response(serializer.data)

    @capable_of("crud_category")
    def post(self, request, academy_id=None):

        data = {**request.data}
        if "lang" in data:
            data["lang"] = data["lang"].upper()

        serializer = POSTCategorySerializer(data=data, context={"request": request, "academy": academy_id})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("crud_category")
    def put(self, request, category_slug, academy_id=None):

        cat = None
        if category_slug.isnumeric():
            cat = AssetCategory.objects.filter(id=category_slug, academy__id=academy_id).first()
        else:
            cat = AssetCategory.objects.filter(slug=category_slug, academy__id=academy_id).first()

        if cat is None:
            raise ValidationException("This category does not exist for this academy", 404)

        data = {**request.data}
        if "lang" in data:
            data["lang"] = data["lang"].upper()

        serializer = PUTCategorySerializer(cat, data=data, context={"request": request, "academy": academy_id})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("crud_category")
    def delete(self, request, academy_id=None):
        lookups = self.generate_lookups(request, many_fields=["id"])
        if lookups:
            items = AssetCategory.objects.filter(**lookups, academy__id=academy_id)

            for item in items:
                item.delete()
            return Response(None, status=status.HTTP_204_NO_CONTENT)
        else:
            raise ValidationException("Category ids were not provided", 404, slug="missing_ids")


class AcademyKeywordView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(cache=KeywordCache, sort="-created_at", paginate=True)

    @capable_of("read_keyword")
    def get(self, request, keyword_slug=None, academy_id=None):

        handler = self.extensions(request)
        cache = handler.cache.get()
        if cache is not None:
            return cache

        items = AssetKeyword.objects.filter(academy__id=academy_id)
        lookup = {}

        if "cluster" in self.request.GET:
            param = self.request.GET.get("cluster")
            if param == "null":
                lookup["cluster"] = None
            else:
                lookup["cluster__slug__in"] = [p.lower() for p in param.split(",")]

        like = request.GET.get("like", None)
        if like is not None and like != "undefined" and like != "":
            items = items.filter(Q(slug__icontains=slugify(like)) | Q(title__icontains=like))

        lang = request.GET.get("lang", None)
        if lang is not None and lang != "undefined" and lang != "":
            lookup["lang__iexact"] = lang

        items = items.filter(**lookup)
        items = handler.queryset(items)

        serializer = KeywordSmallSerializer(items, many=True)
        return handler.response(serializer.data)

    @capable_of("crud_keyword")
    def post(self, request, academy_id=None):

        payload = {**request.data}

        serializer = PostKeywordSerializer(data=payload, context={"request": request, "academy": academy_id})
        if serializer.is_valid():
            serializer.save()
            serializer = AssetKeywordBigSerializer(serializer.instance)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("crud_keyword")
    def put(self, request, keyword_slug, academy_id=None):

        keywd = AssetKeyword.objects.filter(slug=keyword_slug, academy__id=academy_id).first()
        if keywd is None:
            raise ValidationException("This keyword does not exist for this academy", 404)

        data = {**request.data}

        serializer = PUTKeywordSerializer(keywd, data=data, context={"request": request, "academy": academy_id})
        if serializer.is_valid():
            serializer.save()
            serializer = AssetKeywordBigSerializer(serializer.instance)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("crud_keyword")
    def delete(self, request, academy_id=None):
        lookups = self.generate_lookups(request, many_fields=["id"])
        if lookups:
            items = AssetKeyword.objects.filter(**lookups, academy__id=academy_id)

            for item in items:
                item.delete()
            return Response(None, status=status.HTTP_204_NO_CONTENT)
        else:
            raise ValidationException("Asset ids were not provided", 404, slug="missing_ids")


class AcademyKeywordClusterView(APIView, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(sort="-created_at", paginate=True)

    @capable_of("read_keywordcluster")
    def get(self, request, cluster_slug=None, academy_id=None):

        if cluster_slug is not None:
            item = KeywordCluster.objects.filter(academy__id=academy_id, slug=cluster_slug).first()
            if item is None:
                raise ValidationException(
                    f"Cluster with slug {cluster_slug} not found for this academy",
                    status.HTTP_404_NOT_FOUND,
                    slug="cluster-not-found",
                )
            serializer = KeywordClusterBigSerializer(item)
            return Response(serializer.data, status=status.HTTP_200_OK)

        handler = self.extensions(request)

        # cache has been disabled because I cant get it to refresh then keywords are resigned to assets
        # cache = handler.cache.get()
        # if cache is not None:
        #     return cache

        items = KeywordCluster.objects.filter(academy__id=academy_id)
        lookup = {}

        if "visibility" in self.request.GET:
            param = self.request.GET.get("visibility")
            lookup["visibility"] = param.upper()
        else:
            lookup["visibility"] = "PUBLIC"

        like = request.GET.get("like", None)
        if like is not None and like != "undefined" and like != "":
            items = items.filter(Q(slug__icontains=slugify(like)) | Q(title__icontains=like))

        if "lang" in request.GET:
            lang = request.GET.get("lang")
            if lang:
                lookup["lang__iexact"] = lang

        items = items.filter(**lookup)
        items = handler.queryset(items)

        serializer = KeywordClusterMidSerializer(items, many=True)
        return handler.response(serializer.data)

    @capable_of("crud_keywordcluster")
    def post(self, request, academy_id=None):

        payload = {**request.data, "author": request.user.id}

        serializer = PostKeywordClusterSerializer(data=payload, context={"request": request, "academy": academy_id})
        if serializer.is_valid():
            serializer.save()
            serializer = KeywordClusterBigSerializer(serializer.instance)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("crud_keywordcluster")
    def put(self, request, cluster_slug, academy_id=None):

        cluster = KeywordCluster.objects.filter(slug=cluster_slug, academy__id=academy_id).first()
        if cluster is None:
            raise ValidationException("This cluster does not exist for this academy", 404)

        data = {**request.data}
        data.pop("academy", False)

        serializer = PostKeywordClusterSerializer(
            cluster, data=data, context={"request": request, "academy": academy_id}
        )
        if serializer.is_valid():
            serializer.save()
            serializer = KeywordClusterBigSerializer(serializer.instance)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("crud_keywordcluster")
    def delete(self, request, academy_id=None):
        lookups = self.generate_lookups(request, many_fields=["id"])
        if lookups:
            items = KeywordCluster.objects.filter(**lookups, academy__id=academy_id)

            for item in items:
                item.delete()
            return Response(None, status=status.HTTP_204_NO_CONTENT)
        else:
            raise ValidationException("Cluster ids were not provided", 404, slug="missing_ids")


class CodeCompilerView(APIView):
    """
    Proxy endpoint to communicate with rigobot for code compilation.
    """

    @consume("ai-compilation")
    async def post(self, request):
        """
        POST request to compile code using rigobot.
        The request will be proxied to one of two rigobot endpoints based on the request body:
        - /v1/prompting/completion/code-compiler-with-context/ if inputs.secondary_files or inputs.main_file is present
        - /v1/prompting/completion/code-compiler/ otherwise
        """

        # Get rigobot token using user's token
        async with Service("rigobot", request.user.id, proxy=True) as s:
            # Determine which endpoint to use based on request body
            inputs = request.data.get("inputs", {})
            if "secondary_files" in inputs or "main_file" in inputs:
                url = "/v1/prompting/completion/code-compiler-with-context/"
            else:
                url = "/v1/prompting/completion/code-compiler/"

            return await s.post(url, json=request.data)
