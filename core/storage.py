from whitenoise.storage import CompressedManifestStaticFilesStorage


class VersionedStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """File names carry a hash of their content, so browsers can keep them for a year and never see an old one."""

    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:
            # A module bundle's static files were not collected with the build (schule/bundles.py);
            # core.middleware.ModuleStaticMiddleware serves them from the bundle under their plain name.
            return name
