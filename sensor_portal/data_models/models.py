import os
from datetime import timedelta

from archiving.models import Archive, TarFile
from colorfield.fields import ColorField
from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import Point
from django.core.exceptions import (MultipleObjectsReturned,
                                    ObjectDoesNotExist, ValidationError)
from django.db import models
from django.db.models import (BooleanField, Case, Count, DateTimeField,
                              ExpressionWrapper, F, Max, Min, Q, Sum, Value,
                              When)
from django.db.models.functions import Cast, Concat
from django.urls import reverse
from django.utils import timezone as djtimezone
from django_icon_picker.field import IconField
from encrypted_model_fields.fields import EncryptedCharField
from external_storage_import.models import DataStorageInput
from sizefield.models import FileSizeField
from timezone_field import TimeZoneField
from utils.general import convert_unit
from utils.models import BaseModel
from utils.querysets import ApproximateCountQuerySet

from . import validators
from .general_functions import check_dt
from .job_handling_functions import get_job_from_name


class Site(BaseModel):
    name = models.CharField(max_length=50)
    short_name = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.short_name == "":
            self.short_name = self.name[0:10]
        return super().save(*args, **kwargs)


class DataType(BaseModel):
    name = models.CharField(max_length=20)
    colour = ColorField(default="#FFFFFF")
    symbol = IconField(blank=True)

    def __str__(self):
        return self.name


class Project(BaseModel):
    # Metadata
    project_ID = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=50)

    objectives = models.CharField(max_length=500, blank=True)
    principal_investigator = models.CharField(max_length=50, blank=True)
    principal_investigator_email = models.CharField(max_length=100, blank=True)
    contact = models.CharField(max_length=50, blank=True)
    contact_email = models.CharField(max_length=100, blank=True)
    organisation = models.CharField(max_length=100, blank=True)
    data_storages = models.ManyToManyField(
        DataStorageInput, related_name="linked_projects", blank=True)
    archive = models.ForeignKey(
        Archive, related_name="linked_projects", null=True, blank=True, on_delete=models.SET_NULL)

    automated_tasks = models.ManyToManyField(
        "ProjectJob", related_name="linked_projects", blank=True
    )

    def is_active(self):
        if self.id:
            return self.deployments.filter(is_active=True).exists()
        else:
            return False

    # User ownership
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, related_name="owned_projects",
                              on_delete=models.SET_NULL, null=True, db_index=True)
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="managed_projects")
    viewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="viewable_projects")
    annotators = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="annotatable_projects")

    clean_time = models.IntegerField(default=90)

    def __str__(self):
        return self.project_ID

    def get_absolute_url(self):
        return reverse('project-detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        if self.project_ID == "" or self.project_ID is None:
            self.project_ID = self.name[0:10]
        return super().save(*args, **kwargs)


class DeviceModel(BaseModel):
    name = models.CharField(max_length=50, blank=True, unique=True)
    manufacturer = models.CharField(max_length=50, blank=True)
    type = models.ForeignKey(DataType, models.PROTECT,
                             related_name="device_models")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, related_name="owned_device_models",
                              on_delete=models.SET_NULL, null=True)
    colour = ColorField(blank=True)
    symbol = IconField(blank=True)

    def save(self, *args, **kwargs):
        if self.colour == "":
            self.colour = self.type.colour
        if self.symbol == "":
            self.symbol = self.type.symbol
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Device(BaseModel):
    device_ID = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50, blank=True)
    model = models.ForeignKey(
        DeviceModel, models.PROTECT, related_name="registered_devices")

    type = models.ForeignKey(DataType, models.PROTECT,
                             related_name="devices", null=True)

    # User ownership
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, related_name="owned_devices",
                              on_delete=models.SET_NULL, null=True, db_index=True)
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="managed_devices")
    viewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="viewable_devices")
    annotators = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="annotatable_devices")

    autoupdate = models.BooleanField(default=False)
    update_time = models.IntegerField(default=48)

    username = models.CharField(
        max_length=100, unique=True, null=True, blank=True, default=None)
    password = EncryptedCharField(max_length=100, blank=True, null=True)
    input_storage = models.ForeignKey(
        DataStorageInput, null=True, blank=True, related_name="linked_devices", on_delete=models.SET_NULL)

    extra_data = models.JSONField(default=dict, blank=True)

    def is_active(self):
        if self.id:
            return self.deployments.filter(is_active=True).exists()
        else:
            return False

    def __str__(self):
        return self.device_ID

    def get_absolute_url(self):
        return f"/api/device/{self.pk}"

    def save(self, *args, **kwargs):
        if not self.type:
            self.type = self.model.type
        super().save(*args, **kwargs)

    def clean(self):
        result, message = validators.device_check_type(
            self.type, self.model)
        print(result, message)
        if not result:
            raise ValidationError(message)
        super(Device, self).clean()

    def deployment_from_date(self, dt):

        # print(dt)
        all_deploys = self.deployments.all()

        all_tz = all_deploys.values('time_zone', 'pk')

        all_tz = [{'time_zone': x.get(
            'time_zone', settings.TIME_ZONE), 'pk': x['pk']} for x in all_tz]

        all_dt = {x['pk']: check_dt(dt, x['time_zone']) for x in all_tz}

        whens = [When(pk=k, then=Value(v)) for k, v in all_dt.items()]

        all_deploys = all_deploys.annotate(
            dt=Case(*whens, output_field=DateTimeField(), default=Value(None)))

        # For deployments that have not ended - end date is shifted 100 years

        all_deploys = all_deploys.annotate(deployment_end_indefinite=Case(
            When(deployment_end__isnull=True,
                 then=ExpressionWrapper(
                     F('deployment_start') + timedelta(days=365 * 100),
                     output_field=DateTimeField()
                 )
                 ),
            default=F('deployment_end')
        )
        )

        # Annotate by whether the datetime lies in the deployment range

        all_deploys = all_deploys.annotate(in_deployment=ExpressionWrapper(
            Q(Q(deployment_start__lte=F('dt')) & Q(
                deployment_end_indefinite__gte=F('dt'))),
            output_field=BooleanField()
        )
        )

        try:
            correct_deployment = all_deploys.get(in_deployment=True)
            return correct_deployment
        except (ObjectDoesNotExist, MultipleObjectsReturned):
            # Check for complete failure or ambiguity
            all_true_deployments = all_deploys.filter(in_deployment=True)
            print(f"Error: found {all_true_deployments.count()} deployments")
            return None

    def check_overlap(self, new_start, new_end, deployment_pk):

        new_start = check_dt(new_start)
        if new_end is None:
            new_end = new_start + timedelta(days=365 * 100)
        else:
            new_end = check_dt(new_end)

        # print(deployment_pk)
        all_deploys = self.deployments.all().exclude(pk=deployment_pk)
        all_deploys = all_deploys.annotate(deployment_end_indefinite=Case(
            When(deployment_end__isnull=True,
                 then=ExpressionWrapper(
                     F('deployment_start') + timedelta(days=365 * 100),
                     output_field=DateTimeField()
                 )
                 ),
            default=F('deployment_end')
        )
        )
        # print(all_deploys.values('deployment_end', 'deployment_end_indefinite'))
        all_deploys = all_deploys.annotate(in_deployment=ExpressionWrapper(
            Q(Q(deployment_end_indefinite__gte=new_start)
              & Q(deployment_start__lte=new_end)),
            output_field=BooleanField()
        )
        )

        overlapping_deploys = all_deploys.filter(in_deployment=True)
        return list(overlapping_deploys.values_list('deployment_device_ID', flat=True))


class Deployment(BaseModel):
    deployment_device_ID = models.CharField(
        max_length=110, blank=True, editable=False, unique=True)
    deployment_ID = models.CharField(max_length=80)
    device_type = models.ForeignKey(
        DataType, models.PROTECT, related_name="deployments", null=True)
    device_n = models.IntegerField(default=1)

    deployment_start = models.DateTimeField(default=djtimezone.now)
    deployment_end = models.DateTimeField(blank=True, null=True)

    device = models.ForeignKey(
        Device, on_delete=models.PROTECT, related_name="deployments")
    site = models.ForeignKey(Site, models.PROTECT, related_name="deployments")
    project = models.ManyToManyField(
        Project, related_name="deployments", blank=True)

    latitude = models.DecimalField(
        max_digits=8, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField(
        max_digits=8, decimal_places=6, blank=True, null=True)
    point = gis_models.PointField(
        blank=True,
        null=True,
        spatial_index=True
    )

    extra_data = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    time_zone = TimeZoneField(use_pytz=True, default=settings.TIME_ZONE)

    # User ownership
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, related_name="owned_deployments",
                              on_delete=models.SET_NULL, null=True, db_index=True)
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="managed_deployments")
    viewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="viewable_deployments")
    annotators = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="annotatable_deployments")

    combo_project = models.CharField(
        max_length=100, blank=True, null=True, editable=False)
    last_image = models.ForeignKey("DataFile", blank=True, on_delete=models.SET_NULL, null=True, editable=False,
                                   related_name="deployment_last_image")

    thumb_url = models.CharField(
        max_length=500, null=True, blank=True, editable=False)

    def get_absolute_url(self):
        return reverse('deployment-detail', kwargs={'pk': self.pk})

    def __str__(self):
        return self.deployment_device_ID

    def clean(self):
        # result, message = validators.deployment_check_type(
        #     self.device_type, self.device)
        # if not result:
        #     raise ValidationError(message)

        result, message = validators.deployment_start_time_after_end_time(
            self.deployment_start, self.deployment_end)
        if not result:
            raise ValidationError(message)
        result, message = validators.deployment_check_overlap(
            self.deployment_start, self.deployment_end, self.device, self.pk)
        if not result:
            raise ValidationError(message)
        super(Deployment, self).clean()

    def save(self, *args, **kwargs):
        self.deployment_device_ID = f"{self.deployment_ID}_{self.device.type.name}_{self.device_n}"

        self.is_active = self.check_active()

        if self.device_type is None:
            self.device_type = self.device.type

        if self.longitude and self.latitude:
            self.point = Point(
                float(self.longitude),
                float(self.latitude),
                srid=4326
            )
        elif (self.longitude is None and self.latitude is None) and self.point is not None:
            self.longitude, self.latitude = self.point.coords
        else:
            self.point = None

        if self.id:
            self.combo_project = self.get_combo_project()

        super().save(*args, **kwargs)

    def get_combo_project(self):
        if self.project.all().exists():
            all_proj_id = list(
                self.project.all().values_list("project_ID", flat=True))
            all_proj_id.sort()
            return " ".join(all_proj_id)
        else:
            return ""

    def check_active(self):
        self.deployment_start = check_dt(self.deployment_start)
        if self.deployment_end:
            self.deployment_end = check_dt(self.deployment_end)
        if self.deployment_start <= djtimezone.now():
            if self.deployment_end is None or self.deployment_end >= djtimezone.now():
                return True

        return False

    def check_dates(self, dt_list):

        result_list = []

        for dt in dt_list:
            # if no TZ, localise to the device's timezone
            dt = check_dt(dt, self.time_zone)
            # print(dt)
            result_list.append((dt >= self.deployment_start) and (
                (self.deployment_end is None) or (dt <= self.deployment_end)))

        return result_list

    def set_thumb_url(self):

        last_file = self.files.filter(thumb_url__isnull=False, has_human=False).order_by(
            'recording_dt').last()
        if last_file is not None:
            self.last_image = last_file
            self.thumb_url = last_file.thumb_url
        else:
            self.last_image = None
            self.thumb_url = None


class DataFileQuerySet(ApproximateCountQuerySet):
    def full_paths(self):
        self = self.relative_paths()
        return self.annotate(full_path=Concat(F('local_path'), Value(os.sep), F('relative_path')))

    def relative_paths(self):
        self = self.full_names()
        return self.annotate(relative_path=Concat(F('path'), Value(os.sep), F('full_name')))

    def full_names(self):
        return self.annotate(full_name=Concat(F('file_name'), F('file_format')))

    def file_size_unit(self, unit=""):
        total_file_size = self.aggregate(total_file_size=Sum("file_size"))[
            "total_file_size"]
        converted_file_size = convert_unit(total_file_size, unit)
        return converted_file_size

    def file_count(self):
        return self.aggregate(total_file_size=Cast(Sum("file_size"), models.FloatField())/Cast(Value(1024*1024*1024), models.FloatField()),
                              object_n=Count("pk"),
                              archived_file_n=Sum(Case(When(local_storage=False, archived=True, then=Value(1)),
                                                       default=Value(0))))

    def min_date(self):
        return self.aggregate(min_date=Min("recording_dt"))["min_date"]

    def max_date(self):
        return self.aggregate(max_date=Max("recording_dt"))["max_date"]

    def device_type(self):
        return self.annotate(device_type=F('deployment__device__type__name'))


class DataFile(BaseModel):
    deployment = models.ForeignKey(
        Deployment, on_delete=models.CASCADE, related_name="files")

    file_type = models.ForeignKey(
        DataType, models.PROTECT, related_name="files", null=True, default=None)
    file_name = models.CharField(max_length=150, unique=True)
    file_size = FileSizeField()
    file_format = models.CharField(max_length=10)

    upload_dt = models.DateTimeField(default=djtimezone.now)
    recording_dt = models.DateTimeField(null=True, db_index=True)
    path = models.CharField(max_length=500)
    local_path = models.CharField(max_length=500, blank=True)

    extra_data = models.JSONField(default=dict, blank=True)
    linked_files = models.JSONField(default=dict, blank=True)

    thumb_url = models.CharField(max_length=500, null=True, blank=True)

    local_storage = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    tar_file = models.ForeignKey(
        TarFile, on_delete=models.SET_NULL, blank=True, null=True, related_name="files")
    favourite_of = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="favourites")

    do_not_remove = models.BooleanField(default=False)
    original_name = models.CharField(max_length=100, blank=True, null=True)
    file_url = models.CharField(max_length=500, null=True, blank=True)
    tag = models.CharField(max_length=250, null=True, blank=True)

    has_human = models.BooleanField(default=False)

    objects = DataFileQuerySet.as_manager()

    def __str__(self):
        return f"{self.file_name}{self.file_format}"

    def get_absolute_url(self):
        return reverse('datafile-detail', kwargs={'pk': self.pk})

    def add_favourite(self, user):
        self.favourite_of.add(user)
        self.save()

    def remove_favourite(self, user):
        self.favourite_of.remove(user)
        self.save()

    def full_path(self):
        return os.path.join(self.local_path, self.path, f"{self.file_name}{self.file_format}")

    def thumb_path(self):
        return os.path.join(
            self.local_path, self.path, self.file_name+"_THUMB.jpg")

    def set_file_url(self):
        if self.local_storage:
            # is some of this normpath and replace stuff really needed?
            self.file_url = os.path.normpath(
                os.path.join(settings.FILE_STORAGE_URL,
                             self.path,
                             (self.file_name + self.file_format))
            ).replace("\\", "/")
        else:
            self.file_url = None

    def set_linked_files_urls(self):
        for key in self.linked_files.keys():
            file_path = self.linked_files[key]["path"]
            rel_file_path = os.path.relpath(
                file_path, settings.FILE_STORAGE_ROOT)
            self.linked_files[key]["url"] = os.path.join(
                settings.FILE_STORAGE_URL, rel_file_path)

    def set_thumb_url(self, has_thumb=True):
        if has_thumb:
            self.thumb_url = os.path.normpath(os.path.join(settings.FILE_STORAGE_URL,
                                                           self.path, self.file_name+"_THUMB.jpg"))
        else:
            self.thumb_url = None

    def check_human(self):
        old_has_human = self.has_human
        new_has_human = self.observations.filter(
            taxon__taxon_code=settings.HUMAN_TAXON_CODE).exists()
        print(new_has_human)
        if old_has_human != new_has_human:
            self.has_human = new_has_human
            self.save()
            self.deployment.set_thumb_url()
            self.deployment.save()

    def clean_file(self, delete_obj=False):
        print(f"clean {delete_obj}")
        if (self.do_not_remove or self.deployment_last_image.exists()) and not delete_obj:
            return
        if self.local_storage:
            try:
                os.remove(self.full_path())
                os.removedirs(os.path.join(self.local_path, self.path))
            except OSError:
                pass

        try:
            thumb_path = self.thumb_path()
            os.remove(thumb_path)
            os.removedirs(os.path.split(thumb_path)[0])
        except TypeError:
            pass
        except OSError:
            pass

        for key, value in self.linked_files.items():
            try:
                extra_version_path = value["path"]
                os.remove(extra_version_path)
                os.removedirs(extra_version_path)
                self.linked_files.pop(key)
            except TypeError:
                pass
            except OSError:
                pass

        if not delete_obj:
            self.local_storage = False
            self.local_path = ""
            self.linked_files = {}
            self.set_thumb_url(False)
            self.save()

    def save(self, *args, **kwargs):
        if self.file_type is None:
            self.file_type = self.deployment.device.type
        self.set_file_url()
        super().save(*args, **kwargs)

    def clean(self):
        result, message = validators.data_file_in_deployment(
            self.recording_dt, self.deployment)
        if not result:
            raise ValidationError(message)
        super(DataFile, self).clean()


class ProjectJob(BaseModel):
    job_name = models.CharField(max_length=50)
    celery_job_name = models.CharField(max_length=50)
    job_args = models.JSONField(default=dict)

    def __str__(self):
        return self.job_name

    def get_job_signature(self, file_pks):
        # project level jobs always assumed to work on the file level, as they are fired by files being imported
        return get_job_from_name(self.celery_job_name, "datafile", file_pks, self.job_args)
