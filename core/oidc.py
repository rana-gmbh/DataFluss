from django.conf import settings
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from . import audit
from .models import User


class ExistingUsersOnlyBackend(OIDCAuthenticationBackend):
    """Microsoft sign-in matches accounts created by IT. It never creates one."""

    def get_userinfo(self, access_token, id_token, payload):
        return {**super().get_userinfo(access_token, id_token, payload), "tid": payload.get("tid"), "oid": payload.get("oid")}

    def verify_claims(self, claims):
        return claims.get("tid") == settings.OIDC_TENANT and bool(claims.get("oid")) and super().verify_claims(claims)

    def filter_users_by_claims(self, claims):
        """The address only finds the account once, because it can change. After that the Microsoft ID counts."""
        subject = f"{claims['tid']}:{claims['oid']}"
        users = User.objects.filter(oidc_subject=subject, is_active=True)
        if users.exists():
            return users
        email = (claims.get("email") or claims.get("preferred_username") or "").lower()
        if not email:
            return User.objects.none()
        return User.objects.filter(email__iexact=email, oidc_subject="", is_active=True)

    def create_user(self, claims):
        return None

    def update_user(self, user, claims):
        user.oidc_subject = f"{claims['tid']}:{claims['oid']}"
        user.save(update_fields=["oidc_subject"])
        return user

    def get_or_create_user(self, access_token, id_token, payload):
        user = super().get_or_create_user(access_token, id_token, payload)
        if user is not None:
            audit.log("login", f"Anmeldung über Microsoft 365: {user.username}", user=user)
        return user
