from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    is_moderator = models.BooleanField(default=False)
    credential_version = models.PositiveIntegerField(default=1)


class Tag(models.Model):
    name = models.CharField('Название', max_length=50)
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(Lower('name'), name='tag_name_case_insensitive')]

    def __str__(self):
        return self.name


class Topic(models.Model):
    title = models.CharField('Название', max_length=200)
    description = models.TextField('Описание', max_length=10000)
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    tags = models.ManyToManyField(Tag, related_name='topics')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class Message(models.Model):
    topic = models.ForeignKey(Topic, related_name='messages', on_delete=models.CASCADE)
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    text = models.TextField('Сообщение', max_length=10000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']


class ContentLock(models.Model):
    """One row serializes content mutations and snapshots (also on SQLite)."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    revision = models.PositiveBigIntegerField(default=0)


class Revision(models.Model):
    """Append-only through the application; contains no credentials or sessions."""
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    operation = models.CharField(max_length=100)
    before = models.JSONField()
    after = models.JSONField()

    class Meta:
        ordering = ['pk']
