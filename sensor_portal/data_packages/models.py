import os

from data_models.models import DataFile
from django.conf import settings
from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from utils.general import try_remove_file_clean_dirs
from utils.models import BaseModel

from .create_zip_functions import create_zip

# Create your models here.

status = (
    (0, 'Started'),
    (1, 'Unarchiving'),
    (2, 'Creating bundle'),
    (3, 'Ready'),
    (4, 'Failed'),

)

metadata_type = (
    (0, 'base'),
    (1, 'Camera trap DP'),
    (2, 'COCO'),
)


class DataPackage(BaseModel):
    name = models.CharField(max_length=200)
    data_files = models.ManyToManyField(
        DataFile, related_name="data_bundles")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, related_name="data_bundles",
                              on_delete=models.SET_NULL, null=True)
    status = models.IntegerField(
        choices=status, default=0)
    metadata_type = models.IntegerField(
        choices=metadata_type, default=0)
    includes_files = models.BooleanField(default=True)
    file_url = models.CharField(max_length=500, blank=True, null=True)

    def set_file_url(self):
        if self.status == 3:
            zip_name = self.name
            if "zip" not in zip_name:
                zip_name += ".zip"
            self.file_url = os.path.normpath(
                os.path.join(settings.FILE_STORAGE_URL,
                             settings.PACKAGE_PATH,
                             zip_name)
            ).replace("\\", "/")
        else:
            self.file_url = None

    def __str__(self):
        return self.name

    def make_zip(self):
        create_zip(self.name, self.data_files,
                   self.metadata_type, self.includes_files)
        self.status = 3
        self.save()

    def save(self, *args, **kwargs):
        self.set_file_url()
        super().save(*args, **kwargs)

    def clean_data_package(self):
        if self.status == 3:
            package_path = os.path.join(
                settings.FILE_STORAGE_ROOT, settings.PACKAGE_PATH)

            try_remove_file_clean_dirs(
                os.path.join(package_path, self.name+"zip"))
            return True
        elif self.status == 4:
            return True
        else:
            return False


@receiver(pre_delete, sender=DataPackage)
def pre_remove_bundle(sender, instance: DataPackage, **kwargs):
    # deletes the attached file form data storage
    success = instance.clean_data_package()
    if not success:
        raise (Exception(f"Could not remove data package {instance.name}"))
