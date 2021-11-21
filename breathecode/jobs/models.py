# from breathecode.admissions.models import Academy
from django.db import models

# Create your models here.


class Platform(models.Model):
    """ Create a new platform for Jobs"""
    name = models.CharField(max_length=150)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    def __str__(self):
        return f'{self.name} ({self.id})'


SYNCHED = 'SYNCHED'
PENDING = 'PENDING'
WARNING = 'WARNING'
ERROR = 'ERROR'
SPIDER_STATUS = (
    (SYNCHED, 'Synched'),
    (PENDING, 'Pending'),
    (WARNING, 'Warning'),
    (ERROR, 'Error'),
)


class Spider(models.Model):
    """ Create a new platform for Jobs"""
    name = models.CharField(max_length=150)
    zyte_spider_number = models.IntegerField()
    zyte_job_number = models.IntegerField()
    status = models.CharField(max_length=15, choices=SPIDER_STATUS, default=PENDING)

    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, null=False, blank=False)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    def __str__(self):
        return f'{self.name} ({self.id})'


class Employer(models.Model):
    """ something """
    name = models.CharField(max_length=100)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    def __str__(self):
        return f'{self.name} ({self.id})'


class Position(models.Model):
    """ something """
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    def __str__(self):
        return f'{self.name} ({self.id})'


class Tag(models.Model):
    """ something """
    slug = models.SlugField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    def __str__(self):
        return f'{self.slug} ({self.id})'


class Location(models.Model):
    """ something """
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    def __str__(self):
        return f'{self.city} ({self.id})'


OPENED = 'OPENED'
FILLED = 'FILLED'
JOB_STATUS = (
    (OPENED, 'Opened'),
    (FILLED, 'Filled'),
)

FULLTIME = 'Full-time'
INTERNSHIP = 'Internship'
PARTTIME = 'Part-time'
TEMPORARY = 'Temporary'
CONTRACT = 'Contract'
JOB_TYPE = (
    (FULLTIME, 'Full'),
    (INTERNSHIP, 'Internship'),
    (PARTTIME, 'Part-time'),
    (TEMPORARY, 'Temporary'),
    (CONTRACT, 'Contract'),
)


class Job(models.Model):
    """ Create a new platform for Jobs"""
    title = models.CharField(max_length=150)
    published = models.CharField(max_length=20)
    status = models.CharField(max_length=15, choices=JOB_STATUS, default=OPENED)
    apply_url = models.URLField(max_length=256)
    salary = models.CharField(max_length=12)
    job_type = models.CharField(max_length=15, choices=JOB_TYPE, default=FULLTIME)
    remote = models.BooleanField(default=False, verbose_name='This is a boolean field')
    employer = models.ForeignKey(Employer, on_delete=models.CASCADE, null=False, blank=False)
    position = models.ForeignKey(Position, on_delete=models.CASCADE, null=False, blank=False)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, null=False, blank=False)
    location = models.ForeignKey(Location, on_delete=models.CASCADE, null=False, blank=False)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    def __str__(self):
        return f'{self.title} ({self.id})'


# Create your models here.
#This is a mommit
