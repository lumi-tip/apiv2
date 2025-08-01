import html
import logging
import re

from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.utils.html import format_html

# from breathecode.admissions.admin import SyllabusVersionAdmin
from breathecode.services.seo import SEOAnalyzer
from breathecode.utils.admin import change_field

from .actions import (
    AssetThumbnailGenerator,
    add_syllabus_translations,
    get_user_from_github_username,
    process_asset_config,
    push_to_github,
    scan_asset_originality,
)
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
    CredentialsOriginality,
    KeywordCluster,
    OriginalityScan,
    SEOReport,
    SyllabusVersionProxy,
    AssetFlag,
)
from .tasks import (
    async_download_readme_images,
    async_pull_from_github,
    async_push_project_or_exercise_to_github,
    async_remove_img_from_cloud,
    async_test_asset,
    async_regenerate_asset_readme,
    async_update_frontend_asset_cache,
    async_upload_image_to_bucket,
    sync_asset_telemetry_stats,
)

logger = logging.getLogger(__name__)
lang_flags = {
    "en": "🇺🇸",
    "us": "🇺🇸",
    "ge": "🇩🇪",
    "po": "🇵🇹",
    "es": "🇪🇸",
    "it": "🇮🇹",
    None: "",
}


@admin.display(description="Add GITPOD flag (to open on gitpod)")
def add_gitpod(modeladmin, request, queryset):
    queryset.update(gitpod=True)


@admin.display(description="Remove GITPOD flag")
def remove_gitpod(modeladmin, request, queryset):
    queryset.update(gitpod=False)


@admin.display(description="Make it an EXTERNAL resource (new window)")
def make_external(modeladmin, request, queryset):
    queryset.update(external=True)


@admin.display(description="Make it an INTERNAL resource (same window)")
def make_internal(modeladmin, request, queryset):
    queryset.update(external=False)


def process_config_object(modeladmin, request, queryset):
    assets = queryset.all()
    for a in assets:
        process_asset_config(a, a.config)


def pull_content_from_github(modeladmin, request, queryset):
    queryset.update(sync_status="PENDING", status_text="Starting to sync...")
    assets = queryset.all()
    for a in assets:
        try:
            async_pull_from_github.delay(a.slug, request.user.id, override_meta=True)
            # async_pull_from_github(a.slug, request.user.id)  # uncomment for testing purposes
        except Exception as e:
            messages.error(request, a.slug + ": " + str(e))


def push_content_to_github(modeladmin, request, queryset):
    queryset.update(sync_status="PENDING", status_text="Starting to sync...")
    assets = queryset.all()
    for a in assets:
        # try:
        push_to_github(a.slug, request.user)
        #     # async_push_github_asset(a.slug, request.user.id)  # uncomment for testing purposes
        # except Exception as e:
        #     messages.error(request, a.slug + ': ' + str(e))


def pull_content_from_github_override_meta(modeladmin, request, queryset):
    queryset.update(sync_status="PENDING", status_text="Starting to sync...")
    assets = queryset.all()
    for a in assets:
        async_pull_from_github.delay(a.slug, request.user.id, override_meta=True)
        # pull_from_github(a.slug, override_meta=True)  # uncomment for testing purposes


@admin.display(description="Clean and regenerate readme (sync)")
def async_regenerate_readme(modeladmin, request, queryset):
    queryset.update(cleaning_status="PENDING", cleaning_status_details="Starting to clean...")
    assets = queryset.all()
    for a in assets:
        async_regenerate_asset_readme(a.slug)


def make_me_author(modeladmin, request, queryset):
    assets = queryset.all()
    for a in assets:
        a.author = request.user
        a.save()


def get_author_grom_github_usernames(modeladmin, request, queryset):
    assets = queryset.all()
    for a in assets:
        authors = get_user_from_github_username(a.authors_username)
        if len(authors) > 0:
            a.author = authors.pop()
            a.save()


def make_me_owner(modeladmin, request, queryset):
    assets = queryset.all()
    for a in assets:
        a.owner = request.user
        a.save()


def remove_dot_from_slug(modeladmin, request, queryset):
    assets = queryset.all()
    for a in assets:
        if "." in a.slug:
            a.slug = a.slug.replace(".", "-")
            a.save()


def async_generate_thumbnail(modeladmin, request, queryset):
    assets = queryset.all()
    for a in assets:
        generator = AssetThumbnailGenerator(a, "800", "600")
        url, permanent = generator.get_thumbnail_url()


def generate_spanish_translation(modeladmin, request, queryset):
    assets = queryset.all()
    for old in assets:
        old_id = old.id
        if old.lang not in ["us", "en"]:
            messages.error(request, f"Error in {old.slug}: Can only generate trasnlations for english lessons")
            continue

        new_asset = old.all_translations.filter(
            Q(lang__iexact="es") | Q(slug__iexact=old.slug + "-es") | Q(slug__iexact=old.slug + ".es")
        ).first()
        if new_asset is not None:
            messages.error(request, f"Translation to {old.slug} already exists with {new_asset.slug}")
            if ".es" in new_asset.slug:
                new_asset.slug = new_asset.slug.split(".")[0] + "-es"
                new_asset.save()

        else:
            new_asset = old
            new_asset.pk = None
            new_asset.lang = "es"
            new_asset.sync_status = "PENDING"
            new_asset.status_text = "Translation generated, waiting for sync"
            new_asset.slug = old.slug + "-es"
            new_asset.save()

        old = Asset.objects.get(id=old_id)
        old.all_translations.add(new_asset)
        for t in old.all_translations.all():
            new_asset.all_translations.add(t)
        for t in old.technologies.all():
            new_asset.technologies.add(t)


def async_test_asset_integrity(modeladmin, request, queryset):
    queryset.update(test_status="PENDING")
    assets = queryset.all()

    for a in assets:
        try:
            async_test_asset.delay(a.slug, log_errors=True, reset_errors=True, force=True)
        except Exception as e:
            messages.error(request, a.slug + ": " + str(e))


def test_asset_integrity(modeladmin, request, queryset):
    queryset.update(test_status="PENDING")
    assets = queryset.all()

    for a in assets:
        try:
            async_test_asset(a.slug, log_errors=True, reset_errors=True, force=True)
        except Exception as e:
            messages.error(request, a.slug + ": " + str(e))


def seo_report(modeladmin, request, queryset):
    assets = queryset.all()

    for a in assets:
        try:
            # async_execute_seo_report.delay(a.slug)
            SEOAnalyzer(a).start()
        except Exception as e:
            messages.error(request, a.slug + ": " + str(e))


def originality_report(modeladmin, request, queryset):
    assets = queryset.all()

    for a in assets:
        try:
            # async_scan_asset_originality.delay(a.slug)
            scan_asset_originality(a)
        except Exception as e:
            raise e
            messages.error(request, a.slug + ": " + str(e))


def seo_optimization_off(modeladmin, request, queryset):
    queryset.update(is_seo_tracked=False)


def seo_optimization_on(modeladmin, request, queryset):
    queryset.update(is_seo_tracked=True)


def load_readme_tasks(modeladmin, request, queryset):
    assets = queryset.all()
    for a in assets:
        try:
            tasks = a.get_tasks()
            print(f"{len(tasks)} tasks")
            for t in tasks:
                print(t["status"] + ": " + t["slug"] + "\n")
        except Exception as e:
            messages.error(request, a.slug + ": " + str(e))


def download_and_replace_images(modeladmin, request, queryset):
    assets = queryset.all()
    for a in assets:
        try:
            async_download_readme_images.delay(a.slug)
            messages.success(request, message="Asset was schedule for download")
        except Exception as e:
            messages.error(request, a.slug + ": " + str(e))


def reset_4geeks_com_cache(modeladmin, request, queryset):
    assets = queryset.all()
    for a in assets:
        try:
            async_update_frontend_asset_cache.delay(a.slug)
            messages.success(request, message="Assets cache on 4Geeks.com will be updated soon")
        except Exception as e:
            messages.error(request, a.slug + ": " + str(e))


def sync_telemetry_stats(modeladmin, request, queryset):
    """Sync telemetry stats for selected assets."""
    assets = queryset.all()
    for asset in assets:
        try:
            sync_asset_telemetry_stats.delay(asset.id)
            messages.success(request, f"Queued telemetry sync for asset {asset.slug}")
        except Exception as e:
            messages.error(request, f"Error queueing telemetry sync for asset {asset.slug}: {str(e)}")


@admin.display(description="Push PROJECT/EXERCISE assets to GitHub")
def push_projects_or_exercises_to_github(modeladmin, request, queryset):
    """Push selected PROJECT or EXERCISE assets to GitHub."""
    assets = queryset.filter(asset_type__in=["PROJECT", "EXERCISE"])

    if assets.count() == 0:
        messages.error(
            request, "No PROJECT or EXERCISE assets selected. This action only works with PROJECT and EXERCISE assets."
        )
        return

    # Check if any non-PROJECT/EXERCISE assets were selected
    total_selected = queryset.count()
    if total_selected > assets.count():
        skipped_count = total_selected - assets.count()
        messages.warning(request, f"Skipped {skipped_count} assets that are not PROJECT or EXERCISE type.")

    # Queue push tasks for valid assets
    for asset in assets:
        try:
            asset.sync_status = "PENDING"
            asset.status_text = "Queued for GitHub push..."
            asset.save()

            # async_push_project_or_exercise_to_github.delay(asset.slug, create_or_update=True)
            async_push_project_or_exercise_to_github.delay(asset.slug, create_or_update=True)
            messages.success(request, f"Queued GitHub push for asset {asset.slug}")
        except Exception as e:
            messages.error(request, f"Error queueing GitHub push for asset {asset.slug}: {str(e)}")

    if assets.count() > 0:
        messages.info(request, f"Queued {assets.count()} assets for GitHub push. Check sync status for progress.")


class AssessmentFilter(admin.SimpleListFilter):

    title = "Associated Assessment"

    parameter_name = "has_assessment"

    def lookups(self, request, model_admin):

        return (
            ("yes", "Has assessment"),
            ("no", "No assessment"),
        )

    def queryset(self, request, queryset):

        if self.value() == "yes":
            return queryset.filter(assessment__isnull=False)

        if self.value() == "no":
            return queryset.filter(assessment__isnull=True)


class AssetForm(forms.ModelForm):

    class Meta:
        model = Asset
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super(AssetForm, self).__init__(*args, **kwargs)

        if "all_translations" in self.fields and self.instance.pk:
            self.fields["all_translations"].queryset = Asset.objects.filter(
                asset_type=self.instance.asset_type
            ).order_by("slug")

        if "technologies" in self.fields:
            self.fields["technologies"].queryset = AssetTechnology.objects.all().order_by("slug")

        if "assets_related" in self.fields and self.instance.pk:
            self.fields["assets_related"].queryset = Asset.objects.exclude(pk=self.instance.pk)


class WithDescription(admin.SimpleListFilter):

    title = "With description"

    parameter_name = "has_description"

    def lookups(self, request, model_admin):

        return (
            ("yes", "Has description"),
            ("no", "No description"),
        )

    def queryset(self, request, queryset):

        if self.value() == "yes":
            return queryset.filter(description__isnull=False)

        if self.value() == "no":
            return queryset.filter(description__isnull=True)


class IsMarkdown(admin.SimpleListFilter):

    title = "Markdown Based"

    parameter_name = "is_markdown"

    def lookups(self, request, model_admin):

        return (
            ("yes", "Is Markdown"),
            ("no", "Is notebook or other"),
        )

    def queryset(self, request, queryset):

        if self.value() == "yes":
            return queryset.filter(readme_url__contains=".md")

        if self.value() == "no":
            return queryset.exclude(readme_url__contains=".md")


class WithKeywordFilter(admin.SimpleListFilter):

    title = "With Keyword"

    parameter_name = "has_keyword"

    def lookups(self, request, model_admin):

        return (
            ("yes", "Has keyword"),
            ("no", "No keyword"),
        )

    def queryset(self, request, queryset):

        if self.value() == "yes":
            return queryset.filter(seo_keywords__isnull=False)

        if self.value() == "no":
            return queryset.filter(seo_keywords__isnull=True)


class LearnpackDeployed(admin.SimpleListFilter):

    title = "LearnPack Deployed"

    parameter_name = "is_learnpack_deployed"

    def lookups(self, request, model_admin):

        return (
            ("yes", "Deployed"),
            ("no", "Not deployed"),
        )

    def queryset(self, request, queryset):

        if self.value() == "yes":
            return queryset.filter(learnpack_deploy_url__isnull=False)

        if self.value() == "no":
            return queryset.filter(learnpack_deploy_url__isnull=True)


class AcademySlugFilter(admin.SimpleListFilter):
    title = "Academy Slug"
    parameter_name = "academy_slug"

    def lookups(self, request, model_admin):
        from breathecode.admissions.models import Academy

        # Get all academies that have assets
        academies_with_assets = Academy.objects.filter(asset__isnull=False).distinct().order_by("slug")

        # Check if there are assets with null academy
        has_null_academy = Asset.objects.filter(academy__isnull=True).exists()

        lookups = []
        if has_null_academy:
            lookups.append(("null", "No Academy"))

        for academy in academies_with_assets:
            lookups.append((academy.slug, academy.slug))

        return lookups

    def queryset(self, request, queryset):
        if self.value() == "null":
            return queryset.filter(academy__isnull=True)
        elif self.value():
            return queryset.filter(academy__slug=self.value())
        return queryset


# Register your models here.
@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    form = AssetForm
    search_fields = ["title", "slug", "author__email", "url"]
    filter_horizontal = ("technologies", "all_translations", "seo_keywords", "assets_related")
    list_display = ("main", "current_status", "alias", "techs", "url_path")
    readonly_fields = ["flag_seed"]
    list_filter = [
        "asset_type",
        "status",
        "sync_status",
        "test_status",
        "lang",
        "external",
        AssessmentFilter,
        WithKeywordFilter,
        WithDescription,
        IsMarkdown,
        LearnpackDeployed,
        AcademySlugFilter,
    ]
    raw_id_fields = ["author", "owner", "superseded_by"]
    actions = (
        [
            test_asset_integrity,
            async_test_asset_integrity,
            add_gitpod,
            remove_gitpod,
            process_config_object,
            pull_content_from_github,
            pull_content_from_github_override_meta,
            push_content_to_github,
            push_projects_or_exercises_to_github,
            seo_optimization_off,
            seo_optimization_on,
            seo_report,
            originality_report,
            make_me_author,
            make_me_owner,
            get_author_grom_github_usernames,
            generate_spanish_translation,
            remove_dot_from_slug,
            load_readme_tasks,
            async_regenerate_readme,
            async_generate_thumbnail,
            download_and_replace_images,
            reset_4geeks_com_cache,
            sync_telemetry_stats,
        ]
        + change_field(["DRAFT", "NOT_STARTED", "PUBLISHED", "OPTIMIZED"], name="status")
        + change_field(["us", "es"], name="lang")
    )

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))

        # Make readme field read-only when updating an existing asset
        if obj is not None and "readme" not in readonly_fields:
            readonly_fields.append("readme_decoded")

        return readonly_fields

    def get_exclude(self, request, obj=None):
        """Hide the readme field from the admin form."""
        exclude = list(super().get_exclude(request, obj) or [])
        exclude.append("readme")
        return exclude

    def readme_decoded(self, obj):
        """Display the decoded (human-readable) version of the base64-encoded readme field."""
        if obj and obj.readme:
            try:
                decoded_content = Asset.decode(obj.readme)
                # Truncate if too long for admin display
                if len(decoded_content) > 1000:
                    return format_html(
                        '<div style="max-height: 300px; overflow-y: auto; white-space: pre-wrap; font-family: monospace; font-size: 12px; padding: 10px; border: 1px solid #dee2e6; border-radius: 4px;">{}</div>',
                        decoded_content[:1000] + "\n\n... (content truncated, showing first 1000 characters)",
                    )
                else:
                    return format_html(
                        '<div style="max-height: 300px; overflow-y: auto; white-space: pre-wrap; font-family: monospace; font-size: 12px; padding: 10px; border: 1px solid #dee2e6; border-radius: 4px;">{}</div>',
                        decoded_content,
                    )
            except Exception as e:
                return format_html('<span style="color: red;">Error decoding readme: {}</span>', str(e))
        return "No readme content"

    readme_decoded.short_description = "Readme Content (Decoded)"

    def url_path(self, obj):
        return format_html(
            f"""
            <a rel='noopener noreferrer' target='_blank' href='{obj.url}'>github</a> |
            <a rel='noopener noreferrer' target='_blank' href='/v1/registry/asset/preview/{obj.slug}'>preview</a>
        """
        )

    def main(self, obj):

        lang = obj.lang.lower() if isinstance(obj.lang, str) else "?"
        return format_html(
            f"""
                <p style="border: 1px solid #BDBDBD; border-radius: 3px; font-size: 10px; padding: 3px;margin: 0;">{lang_flags.get(lang, None)} {obj.asset_type}</p>
                <p style="margin: 0; padding: 0;">{obj.slug}</p>
                <p style="color: white; font-size: 10px;margin: 0; padding: 0;">{obj.title}</p>
            """
        )

    def current_status(self, obj):
        colors = {
            "PUBLISHED": "bg-success",
            "OK": "bg-success",
            "ERROR": "bg-error",
            "WARNING": "bg-warning",
            None: "bg-warning",
            "DRAFT": "bg-error",
            "OPTIMIZED": "bg-error",
            "PENDING_TRANSLATION": "bg-error",
            "PENDING": "bg-warning",
            "NOT_STARTED": "bg-error",
            "NEEDS_RESYNC": "bg-error",
            "UNLISTED": "bg-warning",
        }

        def from_status(s):
            if s in colors:
                return colors[s]
            return ""

        status = "No status"
        if obj.status_text is not None:
            status = re.sub(r"[^\w\._\-]", " ", obj.status_text)
        return format_html(
            f"""<table style='max-width: 200px;'><tr><td style='font-size: 10px !important;'>Publish</td><td style='font-size: 10px !important;'>Synch</td><td style='font-size: 10px !important;'>Test</td></tr>
        <td><span class='badge {from_status(obj.status)}'>{obj.status}</span></td>
        <td><span class='badge {from_status(obj.sync_status)}'>{obj.sync_status}</span></td>
        <td><span class='badge {from_status(obj.test_status)}'>{obj.test_status}</span></td>
        <tr><td colspan='3'>{status}</td></tr>
        </table>"""
        )

    def techs(self, obj):
        return ", ".join([t.slug for t in obj.technologies.all()])

    def alias(self, obj):
        aliases = AssetAlias.objects.filter(asset__all_translations__slug=obj.slug)
        get_lang = lambda l: l.lower() if isinstance(l, str) else "?"
        return format_html(
            "".join(
                [
                    f'<span style="display: inline-block; background: #2d302d; padding: 2px; border-radius: 3px; margin: 2px;">{lang_flags.get(get_lang(a.asset.lang), None)}{a.slug}</span>'
                    for a in aliases
                ]
            )
        )


def merge_technologies(modeladmin, request, queryset):
    technologies = queryset.all()

    target_tech = technologies.filter(parent__isnull=True, featured_asset__isnull=False).first()
    if target_tech is None:
        target_tech = technologies.filter(parent__isnull=True).first()

    for t in technologies:
        # skip the first one
        if target_tech is None:
            target_tech = t
            continue

        for a in t.asset_set.all():
            a.technologies.add(target_tech)

        if t.id != target_tech.id:
            t.parent = target_tech
            t.save()


def slug_to_lower_case(modeladmin, request, queryset):
    technologies = queryset.all()

    for t in technologies:
        lowercase_tech = AssetTechnology.objects.filter(slug=t.slug.lower()).first()
        if lowercase_tech is not None:
            for a in t.asset_set.all():
                lowercase_tech.asset_set.add(a)
            t.delete()
        else:
            t.slug = t.slug.lower()
            t.save()


class ParentFilter(admin.SimpleListFilter):

    title = "With Parent"

    parameter_name = "has_parent"

    def lookups(self, request, model_admin):

        return (
            ("parents", "Parents"),
            ("alias", "Aliases"),
        )

    def queryset(self, request, queryset):

        if self.value() == "parents":
            return queryset.filter(parent__isnull=True)

        if self.value() == "alias":
            return queryset.filter(parent__isnull=False)


class IsDeprecatedFilter(admin.SimpleListFilter):

    title = "Is Deprecated"

    parameter_name = "is_deprecated"

    def lookups(self, request, model_admin):

        return (("true", "True"), ("false", "False"))

    def queryset(self, request, queryset):
        if self.value() == "true":
            return queryset.filter(is_deprecated=True)

        if self.value() == "false":
            return queryset.filter(is_deprecated=False)


class VisibilityFilter(admin.SimpleListFilter):

    title = "Visibility"

    parameter_name = "visibility"

    def lookups(self, request, model_admin):

        return (("PUBLIC", "Public"), ("UNLISTED", "Unlisted"), ("PRIVATE", "Private"))

    def queryset(self, request, queryset):
        if self.value() == "PUBLIC":
            return queryset.filter(visibility="PUBLIC")

        if self.value() == "UNLISTED":
            return queryset.filter(visibility="UNLISTED")

        if self.value() == "PRIVATE":
            return queryset.filter(visibility="PRIVATE")


class SortPriorityFilter(admin.SimpleListFilter):

    title = "Sort Priority"

    parameter_name = "sort_priority"

    def lookups(self, request, model_admin):

        return (("1", "1"), ("2", "2"), ("3", "3"))

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(sort_priority="1")

        if self.value() == "2":
            return queryset.filter(sort_priority="2")

        if self.value() == "3":
            return queryset.filter(sort_priority="3")


def mark_technologies_as_unlisted(modeladmin, request, queryset):
    technologies = queryset.all()
    for technology in technologies:
        if technology.parent is not None or technology.asset_set.count() < 3:
            AssetTechnology.objects.filter(slug=technology.slug).update(visibility="UNLISTED")


@admin.register(AssetTechnology)
class AssetTechnologyAdmin(admin.ModelAdmin):
    search_fields = ["title", "slug"]
    list_display = ("id", "get_slug", "title", "parent", "featured_asset", "description", "visibility", "is_deprecated")
    list_filter = (ParentFilter, VisibilityFilter, IsDeprecatedFilter, SortPriorityFilter)
    raw_id_fields = ["parent", "featured_asset"]

    actions = (merge_technologies, slug_to_lower_case, mark_technologies_as_unlisted)

    def get_slug(self, obj):
        parent = ""
        if obj.parent is None:
            parent = "🤰🏻"

        return format_html(parent + " " + f'<a href="/admin/registry/assettechnology/{obj.id}/change/">{obj.slug}</a>')


@admin.register(AssetAlias)
class AssetAliasAdmin(admin.ModelAdmin):
    search_fields = ["slug"]
    list_display = ("slug", "asset", "created_at")
    list_filter = [
        "asset__asset_type",
        "asset__status",
        "asset__sync_status",
        "asset__test_status",
        "asset__lang",
        "asset__external",
    ]
    raw_id_fields = ["asset"]


def make_alias(modeladmin, request, queryset):
    errors = queryset.all()
    for e in errors:
        if e.slug != AssetErrorLog.SLUG_NOT_FOUND:
            messages.error(
                request, f"Error: You can only make alias for {AssetErrorLog.SLUG_NOT_FOUND} errors and it was {e.slug}"
            )

        if e.asset is None:
            messages.error(
                request,
                f"Error: Cannot make alias to fix error {e.slug} ({e.id}), please assign asset before trying to fix it",
            )

        else:
            alias = AssetAlias.objects.filter(slug=e.path).first()
            if alias is None:
                alias = AssetAlias(slug=e.path, asset=e.asset)
                alias.save()
                AssetErrorLog.objects.filter(
                    slug=e.slug, asset_type=e.asset_type, status="ERROR", path=e.path, asset=e.asset
                ).update(status="FIXED")
                continue

            if alias.asset.id != e.asset.id:
                messages.error(request, f"Slug {e.path} already exists for a different asset {alias.asset.asset_type}")


def change_status_fixed_including_similar(modeladmin, request, queryset):
    errors = queryset.all()
    for e in errors:
        AssetErrorLog.objects.filter(slug=e.slug, asset_type=e.asset_type, path=e.path, asset=e.asset).update(
            status="FIXED"
        )


def change_status_error_including_similar(modeladmin, request, queryset):
    errors = queryset.all()
    for e in errors:
        AssetErrorLog.objects.filter(slug=e.slug, asset_type=e.asset_type, path=e.path, asset=e.asset).update(
            status="ERROR"
        )


def change_status_ignored_including_similar(modeladmin, request, queryset):
    errors = queryset.all()
    for e in errors:
        AssetErrorLog.objects.filter(slug=e.slug, asset_type=e.asset_type, path=e.path, asset=e.asset).update(
            status="IGNORED"
        )


@admin.register(AssetErrorLog)
class AssetErrorLogAdmin(admin.ModelAdmin):
    search_fields = ["slug", "user__email", "user__first_name", "user__last_name"]
    list_display = ("slug", "path", "status_text", "user", "created_at", "asset")
    raw_id_fields = ["user", "asset"]
    list_filter = ["status", "slug", "asset_type"]
    actions = [
        make_alias,
        change_status_fixed_including_similar,
        change_status_error_including_similar,
        change_status_ignored_including_similar,
    ]

    def current_status(self, obj):
        colors = {
            "FIXED": "bg-success",
            "ERROR": "bg-error",
            "IGNORED": "",
            None: "bg-warning",
        }
        message = ""
        if obj.status_text is not None:
            message = html.escape(obj.status_text)
        return format_html(
            f'<span class="badge {colors[obj.status]}">{obj.slug}</span><small style="display: block;">{message}</small>'
        )


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    search_fields = ["slug", "title"]
    list_display = ("slug", "title", "academy")
    raw_id_fields = ["academy"]
    list_filter = ["academy"]


class KeywordAssignedFilter(admin.SimpleListFilter):

    title = "With Article"

    parameter_name = "has_article"

    def lookups(self, request, model_admin):

        return (
            ("yes", "Has article"),
            ("no", "No article"),
        )

    def queryset(self, request, queryset):

        if self.value() == "yes":
            return queryset.filter(asset__isnull=False)

        if self.value() == "no":
            return queryset.filter(asset__isnull=True)


@admin.register(AssetKeyword)
class AssetKeywordAdmin(admin.ModelAdmin):
    search_fields = ["slug", "title"]
    list_display = ("id", "slug", "title", "cluster")
    # raw_id_fields = ['academy']
    list_filter = [KeywordAssignedFilter]


@admin.register(KeywordCluster)
class KeywordClusterAdmin(admin.ModelAdmin):
    search_fields = ["slug", "title"]
    list_display = ("id", "slug", "title", "academy")
    raw_id_fields = ["academy"]
    list_filter = ["academy"]


@admin.register(AssetComment)
class AssetCommentAdmin(admin.ModelAdmin):
    list_display = ["asset", "text", "author"]
    search_fields = ("asset__slug", "author__first_name", "author__last_name", "author__email")
    raw_id_fields = ["asset", "author", "owner"]
    list_filter = ["asset__academy"]


@admin.register(SEOReport)
class SEOReportAdmin(admin.ModelAdmin):
    list_display = ["report_type", "created_at", "status", "asset"]
    search_fields = ("asset__slug", "asset__title", "report_type")
    raw_id_fields = ["asset"]
    list_filter = ["asset__academy"]


@admin.register(OriginalityScan)
class OriginalityScanAdmin(admin.ModelAdmin):
    list_display = ["id", "created_at", "status", "asset", "success", "score_original", "score_ai"]
    search_fields = ("asset__slug", "asset__title", "report_type")
    raw_id_fields = ["asset"]
    list_filter = ["asset__academy"]


@admin.register(CredentialsOriginality)
class CredentialsOriginalityAdmin(admin.ModelAdmin):
    list_display = ["id", "academy", "created_at", "balance", "last_call_at"]
    search_fields = ("academy__slug", "academy__title")
    raw_id_fields = ["academy"]
    list_filter = ["academy"]


def remove_image_from_bucket(modeladmin, request, queryset):
    images = queryset.all()
    for img in images:
        async_remove_img_from_cloud.delay(img.id)


def upload_image_to_bucket(modeladmin, request, queryset):
    images = queryset.all()
    for img in images:
        async_upload_image_to_bucket.delay(img.id)


@admin.register(AssetImage)
class AssetImageAdmin(admin.ModelAdmin):
    list_display = ["name", "current_status", "mime", "related_assets", "original", "bucket"]
    search_fields = ("name", "original_url", "bucket_url", "assets__slug")
    raw_id_fields = ["assets"]
    list_filter = ["mime", "assets__academy", "download_status"]
    actions = [remove_image_from_bucket]

    def related_assets(self, obj):
        return format_html(
            "".join(
                [
                    f'<a href="/admin/registry/asset/{a.id}/change/" style="display: inline-block; background: #2d302d; padding: 2px; border-radius: 3px; margin: 2px;">{a.slug}</a>'
                    for a in obj.assets.all()
                ]
            )
        )
        return format_html(f'<a href="/admin/registry/asset/{obj.asset.id}/change/">{obj.asset.slug}</a>')

    def original(self, obj):
        return format_html(f'<a target="_blank" href="{obj.original_url}">{obj.original_url}</a>')

    def bucket(self, obj):
        return format_html(f'<a target="_blank" href="{obj.bucket_url}">{obj.bucket_url}</a>')

    def current_status(self, obj):
        colors = {
            "DONE": "bg-success",
            "OK": "bg-success",
            "PENDING": "bg-warning",
            "WARNING": "bg-warning",
            "ERROR": "bg-error",
            "NEEDS_RESYNC": "bg-error",
        }
        return format_html(f"<span class='badge {colors[obj.download_status]}'>{obj.download_status}</span>")


def add_translations_into_json(modeladmin, request, queryset):
    versions = queryset.all()
    for s_version in versions:
        s_version.json = add_syllabus_translations(s_version.json)
        s_version.save()


@admin.register(SyllabusVersionProxy)
class SyllabusVersionAdmin(admin.ModelAdmin):
    list_display = ["syllabus", "version", "status", "url_path"]
    search_fields = ("syllabus__slug", "syllabus__name")
    # raw_id_fields = ['assets']
    list_filter = ["syllabus"]
    actions = [add_translations_into_json]

    def url_path(self, obj):
        return format_html(
            f"""
            <a rel='noopener noreferrer' target='_blank' href='/v1/admissions/syllabus/{obj.syllabus.id}/version/{obj.version}/preview'>preview</a>
        """
        )


@admin.register(ContentVariable)
class ContentVariablesAdmin(admin.ModelAdmin):
    list_display = ["key", "academy", "lang", "real_value"]
    search_fields = ("key",)
    # raw_id_fields = ['assets']
    list_filter = ["academy"]

    def real_value(self, obj):
        _values = {
            "MARKDOWN": obj.value[:200],
            "PYTHON_CODE": "python code",
            "FETCH_JSON": "JSON from: " + obj.value,
            "FETCH_TEXT": "Fetch from: " + obj.value,
        }
        return format_html(f"{_values[obj.var_type]}")


@admin.register(AssetContext)
class AssetContextAdmin(admin.ModelAdmin):
    list_display = ["asset", "ai_context"]
    search_fields = ("asset__slug", "asset__title")
    list_filter = ["asset__category", "asset__lang"]

    def ai_context(self, obj: AssetContext):
        lenght = len(obj.ai_context)
        ai_context = obj.ai_context

        if lenght <= 20:
            return ai_context

        return ai_context[:20] + "..."


def revoke_flags(modeladmin, request, queryset):
    """Revoke selected flags."""
    flags = queryset.filter(status="ACTIVE")
    for flag in flags:
        flag.revoke(revoked_by=request.user)
    messages.success(request, f"Successfully revoked {flags.count()} flags")


def activate_flags(modeladmin, request, queryset):
    """Activate selected flags (only non-expired ones)."""
    from django.utils import timezone

    flags = queryset.filter(status="REVOKED")
    activated_count = 0

    for flag in flags:
        if flag.expires_at is None or flag.expires_at > timezone.now():
            flag.status = "ACTIVE"
            flag.revoked_at = None
            flag.revoked_by = None
            flag.save()
            activated_count += 1

    messages.success(request, f"Successfully activated {activated_count} flags")


class FlagStatusFilter(admin.SimpleListFilter):
    title = "Flag Status"
    parameter_name = "flag_status"

    def lookups(self, request, model_admin):
        return (
            ("active", "Active"),
            ("revoked", "Revoked"),
            ("expired", "Expired"),
        )

    def queryset(self, request, queryset):
        from django.utils import timezone

        if self.value() == "active":
            return queryset.filter(status="ACTIVE", expires_at__gt=timezone.now())

        if self.value() == "revoked":
            return queryset.filter(status="REVOKED")

        if self.value() == "expired":
            return queryset.filter(expires_at__lte=timezone.now())


class HasUserFilter(admin.SimpleListFilter):
    title = "Has User"
    parameter_name = "has_user"

    def lookups(self, request, model_admin):
        return (
            ("yes", "Has User"),
            ("no", "No User"),
        )

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(user__isnull=False)

        if self.value() == "no":
            return queryset.filter(user__isnull=True)


@admin.register(AssetFlag)
class AssetFlagAdmin(admin.ModelAdmin):
    list_display = ["flag_id", "asset_info", "user_info", "academy_info", "current_status", "expires_at", "created_at"]
    search_fields = [
        "flag_id",
        "flag_value",
        "asset__slug",
        "asset__title",
        "user__email",
        "user__first_name",
        "user__last_name",
    ]
    list_filter = [FlagStatusFilter, HasUserFilter, "asset__academy", "asset__asset_type", "created_at"]
    raw_id_fields = ["asset", "user", "academy", "generated_by", "revoked_by"]
    readonly_fields = ["created_at", "updated_at"]
    actions = [revoke_flags, activate_flags]

    fieldsets = (
        ("Flag Information", {"fields": ("flag_id", "flag_value", "asset")}),
        ("Assignment", {"fields": ("user", "academy")}),
        ("Status", {"fields": ("status", "expires_at", "revoked_at", "revoked_by", "generated_by")}),
        ("Metadata", {"fields": ("metadata",), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def asset_info(self, obj):
        return format_html(
            '<a href="/admin/registry/asset/{}/change/" style="display: inline-block; background: #2d302d; padding: 2px; border-radius: 3px; margin: 2px;">{}</a>',
            obj.asset.id,
            obj.asset.slug,
        )

    asset_info.short_description = "Asset"

    def user_info(self, obj):
        if obj.user:
            return format_html('<a href="/admin/auth/user/{}/change/">{}</a>', obj.user.id, obj.user.email)
        return "No user"

    user_info.short_description = "User"

    def academy_info(self, obj):
        if obj.academy:
            return format_html(
                '<a href="/admin/admissions/academy/{}/change/">{}</a>', obj.academy.id, obj.academy.slug
            )
        return "No academy"

    academy_info.short_description = "Academy"

    def current_status(self, obj):
        colors = {
            "ACTIVE": "bg-success",
            "REVOKED": "bg-error",
            "EXPIRED": "bg-warning",
        }

        # Check if expired
        from django.utils import timezone

        status = obj.status
        if obj.expires_at and obj.expires_at <= timezone.now():
            status = "EXPIRED"

        return format_html('<span class="badge {}">{}</span>', colors.get(status, "bg-warning"), status)

    current_status.short_description = "Status"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("asset", "user", "academy", "generated_by", "revoked_by")
