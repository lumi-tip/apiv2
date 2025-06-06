from datetime import datetime

from capyc.rest_framework.exceptions import ValidationException
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

import breathecode.activity.tasks as tasks_activity
from breathecode.admissions.models import Academy, CohortUser
from breathecode.utils import GenerateLookupsMixin, HeaderLimitOffsetPagination, capable_of
from breathecode.utils.api_view_extensions.api_view_extensions import APIViewExtensions
from breathecode.utils.find_by_full_name import query_like_by_full_name

from .caches import AnswerCache
from .models import AcademyFeedbackSettings, Answer, Review, ReviewPlatform, Survey, SurveyTemplate
from .serializers import (
    AcademyFeedbackSettingsPUTSerializer,
    AcademyFeedbackSettingsSerializer,
    AnswerPUTSerializer,
    AnswerSerializer,
    BigAnswerSerializer,
    GetSurveySerializer,
    ReviewPlatformSerializer,
    ReviewPUTSerializer,
    ReviewSmallSerializer,
    SurveyPUTSerializer,
    SurveySerializer,
    SurveySmallSerializer,
    SurveyTemplateSerializer,
)
from .tasks import generate_user_cohort_survey_answers


@api_view(["GET"])
@permission_classes([AllowAny])
def track_survey_open(request, answer_id=None):

    item = None
    if answer_id is not None:
        item = Answer.objects.filter(id=answer_id, status="SENT").first()

    if item is not None:
        item.status = "OPENED"
        item.opened_at = timezone.now()
        item.save()

    image = Image.new("RGB", (1, 1))
    response = HttpResponse(content_type="image/png")
    image.save(response, "PNG")
    return response


@api_view(["GET"])
def get_survey_questions(request, survey_id=None):

    survey = Survey.objects.filter(id=survey_id).first()
    if survey is None:
        raise ValidationException("Survey not found", 404)

    utc_now = timezone.now()
    if utc_now > survey.sent_at + survey.duration:
        raise ValidationException("This survey has already expired", 400)

    cu = CohortUser.objects.filter(cohort=survey.cohort, role="STUDENT", user=request.user).first()
    if cu is None:
        raise ValidationException("This student does not belong to this cohort", 400)

    cohort_teacher = CohortUser.objects.filter(cohort=survey.cohort, role="TEACHER")
    if cohort_teacher.count() == 0:
        raise ValidationException("This cohort must have a teacher assigned to be able to survey it", 400)

    template_slug = survey.template_slug
    if template_slug is None:
        # If the survey does not have a template slug, we need to get the default template slug
        # from the AcademyFeedbackSettings model
        settings = AcademyFeedbackSettings.objects.filter(academy=survey.cohort.academy).first()
        template_slug = settings.cohort_survey_template.slug if settings and settings.cohort_survey_template else None
        survey.template_slug = template_slug
        survey.save()

    answers = generate_user_cohort_survey_answers(request.user, survey, status="OPENED", template_slug=template_slug)
    serializer = AnswerSerializer(answers, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["GET"])
def get_survey(request, survey_id=None):

    survey = Survey.objects.filter(id=survey_id).first()
    if survey is None:
        raise ValidationException("Survey not found", 404)

    utc_now = timezone.now()
    if utc_now > survey.sent_at + survey.duration:
        raise ValidationException("This survey has already expired", 400)

    serializer = SurveySerializer(survey)
    return Response(serializer.data, status=status.HTTP_200_OK)


# Create your views here.
class GetAnswerView(APIView):
    """
    List all snippets, or create a new snippet.
    """

    extensions = APIViewExtensions(cache=AnswerCache, sort="-created_at", paginate=True)

    @capable_of("read_nps_answers")
    def get(self, request, format=None, academy_id=None):
        handler = self.extensions(request)

        cache = handler.cache.get()
        if cache is not None:
            return cache

        items = Answer.objects.filter(academy__id=academy_id)

        lookup = {}

        users = request.GET.get("user", None)
        if users is not None and users != "":
            items = items.filter(user__id__in=users.split(","))

        cohorts = request.GET.get("cohort", None)
        if cohorts is not None and cohorts != "":
            items = items.filter(cohort__slug__in=cohorts.split(","))

        mentors = request.GET.get("mentor", None)
        if mentors is not None and mentors != "":
            items = items.filter(mentor__id__in=mentors.split(","))

        events = request.GET.get("event", None)
        if events is not None and events != "":
            items = items.filter(event__id__in=events.split(","))

        score = request.GET.get("score", None)
        if score is not None and score != "":
            lookup["score"] = score

        _status = request.GET.get("status", None)
        if _status is not None and _status != "":
            items = items.filter(status__in=_status.split(","))

        surveys = request.GET.get("survey", None)
        if surveys is not None and surveys != "":
            items = items.filter(survey__id__in=surveys.split(","))

        items = items.filter(**lookup)

        like = request.GET.get("like", None)
        if like is not None:
            items = query_like_by_full_name(like=like, items=items, prefix="user__")

        items = handler.queryset(items)
        serializer = AnswerSerializer(items, many=True)

        return handler.response(serializer.data)


class AnswerMeView(APIView):
    """
    Student answers a survey (normally several answers are required for each survey)
    """

    def put(self, request, answer_id=None):
        if answer_id is None:
            raise ValidationException("Missing answer_id", slug="missing-answer-id")

        answer = Answer.objects.filter(user=request.user, id=answer_id).first()
        if answer is None:
            raise ValidationException(
                "This survey does not exist for this user", code=404, slug="answer-of-other-user-or-not-exists"
            )

        serializer = AnswerPUTSerializer(answer, data=request.data, context={"request": request, "answer": answer_id})
        if serializer.is_valid():
            tasks_activity.add_activity.delay(
                request.user.id, "nps_answered", related_type="feedback.Answer", related_id=answer_id
            )
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request, answer_id=None):
        if answer_id is None:
            raise ValidationException("Missing answer_id", slug="missing-answer-id")

        answer = Answer.objects.filter(user=request.user, id=answer_id).first()
        if answer is None:
            raise ValidationException(
                "This survey does not exist for this user", code=404, slug="answer-of-other-user-or-not-exists"
            )

        serializer = BigAnswerSerializer(answer)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AcademyAnswerView(APIView):

    @capable_of("read_nps_answers")
    def get(self, request, academy_id=None, answer_id=None):
        if answer_id is None:
            raise ValidationException("Missing answer_id", code=404)

        answer = Answer.objects.filter(academy__id=academy_id, id=answer_id).first()
        if answer is None:
            raise ValidationException("This survey does not exist for this academy")

        serializer = BigAnswerSerializer(answer)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AcademySurveyView(APIView, HeaderLimitOffsetPagination, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    @capable_of("crud_survey")
    def post(self, request, academy_id=None):

        serializer = SurveySerializer(data=request.data, context={"request": request, "academy_id": academy_id})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    """
    List all snippets, or create a new snippet.
    """

    @capable_of("crud_survey")
    def put(self, request, survey_id=None, academy_id=None):
        if survey_id is None:
            raise ValidationException("Missing survey_id")

        survey = Survey.objects.filter(id=survey_id).first()
        if survey is None:
            raise NotFound("This survey does not exist")

        serializer = SurveyPUTSerializer(
            survey, data=request.data, context={"request": request, "survey": survey_id, "academy_id": academy_id}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("read_survey")
    def get(self, request, survey_id=None, academy_id=None):
        if survey_id is not None:
            survey = Survey.objects.filter(id=survey_id).first()
            if survey is None:
                raise NotFound("This survey does not exist")

            serializer = GetSurveySerializer(survey)
            return Response(serializer.data, status=status.HTTP_200_OK)

        items = Survey.objects.filter(cohort__academy__id=academy_id)
        lookup = {}

        if "status" in self.request.GET:
            param = self.request.GET.get("status")
            lookup["status"] = param

        if "cohort" in self.request.GET:
            param = self.request.GET.get("cohort")
            lookup["cohort__slug"] = param

        if "lang" in self.request.GET:
            param = self.request.GET.get("lang")
            lookup["lang"] = param

        if "template_slug" in self.request.GET:
            param = self.request.GET.get("template_slug")
            lookup["template_slug"] = param

        if "title" in self.request.GET:
            title = self.request.GET.get("title")
            items = items.filter(title__icontains=title)

        if "total_score" in self.request.GET:
            total_score = self.request.GET.get("total_score")
            lookup_map = {
                "gte": "scores__total__gte",
                "lte": "scores__total__lte",
                "gt": "scores__total__gt",
                "lt": "scores__total__lt",
            }

            try:
                # Check for prefix (e.g., gte:8)
                if ":" in total_score:
                    prefix, value = total_score.split(":", 1)
                    if prefix in lookup_map:
                        score_value = int(value)
                        items = items.filter(**{lookup_map[prefix]: score_value})
                    else:
                        raise ValidationException(f"Invalid total_score format {total_score}", slug="score-format")
                else:
                    # Exact match (e.g., 8)
                    score_value = int(total_score)
                    items = items.filter(scores__total__gte=score_value, scores__total__lt=score_value + 1)
            except ValueError:
                raise ValidationException(f"Invalid total_score format {total_score}", slug="score-format")

        sort = self.request.GET.get("sort")
        if sort is None:
            sort = "-created_at"
        items = items.filter(**lookup).order_by(sort)

        page = self.paginate_queryset(items, request)
        serializer = SurveySmallSerializer(page, many=True)

        if self.is_paginate(request):
            return self.get_paginated_response(serializer.data)
        else:
            return Response(serializer.data, status=status.HTTP_200_OK)

    @capable_of("crud_survey")
    def delete(self, request, academy_id=None, survey_id=None):

        lookups = self.generate_lookups(request, many_fields=["id"])

        if lookups and survey_id:
            raise ValidationException(
                "survey_id was provided in url " "in bulk mode request, use querystring style instead",
                code=400,
                slug="survey-id-and-lookups-together",
            )

        if not lookups and not survey_id:
            raise ValidationException(
                "survey_id was not provided in url", code=400, slug="without-survey-id-and-lookups"
            )

        if lookups:
            items = Survey.objects.filter(**lookups, cohort__academy__id=academy_id).exclude(status="SENT")

            ids = [item.id for item in items]

            if answers := Answer.objects.filter(survey__id__in=ids, status="ANSWERED"):

                slugs = set([answer.survey.cohort.slug for answer in answers])

                raise ValidationException(
                    f'Survey cannot be deleted because it has been answered for cohorts {", ".join(slugs)}',
                    code=400,
                    slug="survey-cannot-be-deleted",
                )

            for item in items:
                item.delete()

            return Response(None, status=status.HTTP_204_NO_CONTENT)

        sur = Survey.objects.filter(id=survey_id, cohort__academy__id=academy_id).exclude(status="SENT").first()
        if sur is None:
            raise ValidationException("Survey not found", 404, slug="survey-not-found")

        if Answer.objects.filter(survey__id=survey_id, status="ANSWERED"):
            raise ValidationException("Survey cannot be deleted", code=400, slug="survey-cannot-be-deleted")

        sur.delete()
        return Response(None, status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([AllowAny])
def get_review_platform(request, platform_slug=None):

    items = ReviewPlatform.objects.all()
    if platform_slug is not None:
        items = items.filter(slug=platform_slug).first()
        if items is not None:
            serializer = ReviewPlatformSerializer(items, many=False)
            return Response(serializer.data)
        else:
            raise ValidationException("Review platform not found", slug="reivew_platform_not_found", code=404)
    else:
        serializer = ReviewPlatformSerializer(items, many=True)
        return Response(serializer.data)


@api_view(["GET"])
@permission_classes([AllowAny])
def get_reviews(request):
    """
    List all snippets, or create a new snippet.
    """
    items = Review.objects.filter(
        is_public=True,
        status="DONE",
        comments__isnull=False,
        total_rating__isnull=False,
        total_rating__gt=0,
        total_rating__lte=10,
    ).exclude(comments__exact="")

    lookup = {}

    if "academy" in request.GET:
        param = request.GET.get("academy")
        lookup["cohort__academy__id"] = param

    if "lang" in request.GET:
        param = request.GET.get("lang")
        lookup["lang"] = param

    items = items.filter(**lookup).order_by("-created_at")

    serializer = ReviewSmallSerializer(items, many=True)
    return Response(serializer.data)


class ReviewView(APIView, HeaderLimitOffsetPagination, GenerateLookupsMixin):
    """
    List all snippets, or create a new snippet.
    """

    @capable_of("read_review")
    def get(self, request, format=None, academy_id=None):

        academy = Academy.objects.get(id=academy_id)
        items = Review.objects.filter(cohort__academy__id=academy.id)
        lookup = {}

        start = request.GET.get("start", None)
        if start is not None:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
            lookup["created_at__gte"] = start_date

        end = request.GET.get("end", None)
        if end is not None:
            end_date = datetime.strptime(end, "%Y-%m-%d").date()
            lookup["created_at__lte"] = end_date

        if "status" in self.request.GET:
            param = self.request.GET.get("status")
            lookup["status"] = param

        if "platform" in self.request.GET:
            param = self.request.GET.get("platform")
            items = items.filter(platform__name__icontains=param)

        if "cohort" in self.request.GET:
            param = self.request.GET.get("cohort")
            lookup["cohort__id"] = param

        if "author" in self.request.GET:
            param = self.request.GET.get("author")
            lookup["author__id"] = param

        sort_by = "-created_at"
        if "sort" in self.request.GET and self.request.GET["sort"] != "":
            sort_by = self.request.GET.get("sort")

        items = items.filter(**lookup).order_by(sort_by)

        like = request.GET.get("like", None)
        if like is not None:
            items = query_like_by_full_name(like=like, items=items, prefix="author__")

        page = self.paginate_queryset(items, request)
        serializer = ReviewSmallSerializer(page, many=True)

        if self.is_paginate(request):
            return self.get_paginated_response(serializer.data)
        else:
            return Response(serializer.data, status=200)

    @capable_of("crud_review")
    def put(self, request, review_id, academy_id=None):

        review = Review.objects.filter(id=review_id, cohort__academy__id=academy_id).first()
        if review is None:
            raise NotFound("This review does not exist on this academy")

        serializer = ReviewPUTSerializer(
            review, data=request.data, context={"request": request, "review": review_id, "academy_id": academy_id}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @capable_of("crud_review")
    def delete(self, request, academy_id=None):
        # TODO: here i don't add one single delete, because i don't know if it is required
        lookups = self.generate_lookups(request, many_fields=["id"])
        # automation_objects

        if not lookups:
            raise ValidationException("Missing parameters in the querystring", code=400)

        items = Review.objects.filter(**lookups, academy__id=academy_id)

        for item in items:
            item.status = "IGNORE"
            item.save()

        return Response(None, status=status.HTTP_204_NO_CONTENT)


class AcademyFeedbackSettingsView(APIView):
    @capable_of("get_academy_feedback_settings")
    def get(self, request, academy_id):

        try:
            settings = AcademyFeedbackSettings.objects.get(academy__id=academy_id)
        except AcademyFeedbackSettings.DoesNotExist:
            raise ValidationException("Academy feedback settings not found", code=400)

        serializer = AcademyFeedbackSettingsSerializer(settings)
        return Response(serializer.data)

    @capable_of("crud_academy_feedback_settings")
    def put(self, request, academy_id):
        academy = Academy.objects.get(id=academy_id)
        # Look for a shared English template to use as default
        default_template = SurveyTemplate.objects.filter(
            is_shared=True, lang="en", original__isnull=True  # Only get original templates
        ).first()

        defaults = {}

        # Add template to defaults if found
        if "cohort_survey_template" not in request.data:
            defaults["cohort_survey_template"] = default_template
        if "liveclass_survey_template" not in request.data:
            defaults["liveclass_survey_template"] = default_template
        if "event_survey_template" not in request.data:
            defaults["event_survey_template"] = default_template
        if "mentorship_session_survey_template" not in request.data:
            defaults["mentorship_session_survey_template"] = default_template

        settings, created = AcademyFeedbackSettings.objects.get_or_create(academy=academy, defaults=defaults)

        serializer = AcademyFeedbackSettingsPUTSerializer(
            settings, data=request.data, context={"request": request, "academy_id": academy_id}
        )

        if serializer.is_valid():
            serializer.save()
            return Response(AcademyFeedbackSettingsSerializer(settings).data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AcademySurveyTemplateView(APIView):
    @capable_of("read_survey_template")
    def get(self, request, academy_id=None):
        templates = SurveyTemplate.objects.filter(Q(academy__id=academy_id) | Q(is_shared=True))

        # Check if 'is_shared' is present and true in the querystring
        is_shared = request.GET.get("is_shared", "false").lower() == "false"
        if is_shared:
            templates = templates.filter(is_shared=False)

        if "lang" in self.request.GET:
            param = self.request.GET.get("lang")
            templates = templates.filter(lang=param)

        serializer = SurveyTemplateSerializer(templates, many=True)
        return Response(serializer.data)
