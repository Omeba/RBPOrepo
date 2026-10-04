from io import StringIO
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from .models import User, Tag, Topic, Message, Revision
from .services import snapshot


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AdminTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('PROVERKA', password='Original-pass-72!')

    def login(self):
        self.assertEqual(self.client.post('/login/', {'username': 'PROVERKA', 'password': 'Original-pass-72!'}).status_code, 302)

    def grant(self):
        call_command('set_admin', 'PROVERKA', seed_tags=True, stdout=StringIO())
        self.user.refresh_from_db()

    def test_grant_preserves_password_revokes_sessions_and_seeds_audited_tags(self):
        self.login()
        old_hash = self.user.password
        self.grant()
        self.assertEqual(self.user.password, old_hash)
        self.assertTrue(self.user.is_staff and self.user.is_superuser and self.user.is_moderator)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.assertEqual(Tag.objects.count(), 5)
        self.assertEqual(Revision.objects.count(), 5)
        self.assertEqual(set(Tag.objects.values_list('author_id', flat=True)), {self.user.pk})
        self.grant()
        self.assertEqual(Tag.objects.count(), 5)
        self.assertEqual(Revision.objects.count(), 5)
        self.login()
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_admin_cannot_bypass_content_or_user_services(self):
        self.grant()
        self.login()
        before = snapshot()
        for model in ('user', 'tag', 'topic', 'message', 'revision'):
            self.assertEqual(self.client.get(f'/admin/forum/{model}/').status_code, 200)
            self.assertEqual(self.client.post(f'/admin/forum/{model}/add/', {'name': 'bypass'}).status_code, 403)
        for model, pk in [('user', self.user.pk), ('tag', Tag.objects.first().pk), ('revision', Revision.objects.first().pk)]:
            self.assertEqual(self.client.get(f'/admin/forum/{model}/{pk}/change/').status_code, 200)
            self.assertEqual(self.client.post(f'/admin/forum/{model}/{pk}/change/', {'is_staff': ''}).status_code, 403)
            self.assertEqual(self.client.post(f'/admin/forum/{model}/{pk}/delete/').status_code, 403)
        self.assertEqual(snapshot(), before)
        self.assertNotContains(self.client.get(f'/admin/forum/user/{self.user.pk}/change/'), self.user.password)

    def test_admin_uses_existing_authentication_and_password_change(self):
        self.assertRedirects(self.client.post('/admin/login/', {'username': 'PROVERKA', 'password': 'Original-pass-72!'}), '/login/')
        self.login()
        self.assertEqual(self.client.get('/admin/').status_code, 302)  # regular member
        self.grant()
        self.login()
        self.assertRedirects(self.client.get('/admin/password_change/'), '/account/credentials/')
