def site(request):
    from .models import Report, Review, Version

    waiting = 0
    if request.user.is_authenticated and request.user.is_staff:
        waiting = (Version.objects.filter(status=Version.SUBMITTED).count() + Review.objects.filter(text_status=Review.SUBMITTED).count()
                   + Report.objects.filter(handled=False).count())
    from django.conf import settings

    return {"review_waiting": waiting, "site_url": settings.SITE_URL}
