def waiting(request):
    """What reviewers have to look at for websites (the "Prüfen" link counts it with the templates)."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return {"sites_waiting": 0}
    from .models import Access, SiteReport

    return {"sites_waiting": Access.objects.filter(status=Access.REQUESTED).count() + SiteReport.objects.filter(handled=False).count()}
