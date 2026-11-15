from django.apps import AppConfig
from django.db.models.signals import post_migrate, post_save


class PredictorConfig(AppConfig):
    name = 'predictor'

    def ready(self):
        from . import access
        from .models import UserProfile

        post_migrate.connect(access.sync_roles, sender=self, dispatch_uid='predictor_sync_roles')

        def keep_group_in_step(sender, instance, **kwargs):
            access.apply_group(instance.user, instance.role)

        post_save.connect(keep_group_in_step, sender=UserProfile, dispatch_uid='predictor_profile_group')
