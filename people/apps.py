from django.apps import AppConfig
from django.db.models.signals import post_save


def _person_saved(sender, instance, raw=False, **kwargs):
    if raw:  # A change brought by sync; the node that made it sent the section entries too.
        return
    from .section_staff import follow_person

    follow_person(instance)


class PeopleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'people'

    def ready(self):
        from .models import Person

        post_save.connect(_person_saved, sender=Person, dispatch_uid="people.section_staff")
