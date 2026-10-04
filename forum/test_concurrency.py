"""Real independent connections and row locks; run with PostgreSQL (see README)."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings

from . import services
from .models import ContentLock, User, Topic, Tag, Message, Revision

PASSWORD = 'Orchid-forest-53!'
NEW_PASSWORD = 'River-and-sky-72!'


@skipUnless(connection.vendor == 'postgresql', 'Row-lock races require PostgreSQL')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        ContentLock.objects.get_or_create(pk=1)
        self.user = User.objects.create_user('owner', password=PASSWORD, is_moderator=True)
        self.member = User.objects.create_user('member', password=PASSWORD)
        self.tag = Tag.objects.create(name='A', author=self.user)
        self.tag2 = Tag.objects.create(name='B', author=self.user)
        self.topic = Topic.objects.create(title='Original', description='text', author=self.member)
        self.topic.tags.add(self.tag, self.tag2)
        self.message = Message.objects.create(topic=self.topic, author=self.member, text='keep me')

    def client_for_owner(self):
        client = Client()
        self.assertEqual(client.post('/login/', {'username': 'owner', 'password': PASSWORD}).status_code, 302)
        return client

    def in_parallel(self, *actions):
        barrier = Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as executor:
            futures = [executor.submit(run, action) for action in actions]
            return [f.result(timeout=20) for f in futures]

    def change(self, client, username):
        return client.post('/account/credentials/', {'username': username, 'current_password': PASSWORD,
                           'password1': NEW_PASSWORD, 'password2': NEW_PASSWORD})

    def test_two_old_version_changes_only_one_succeeds(self):
        first, second = self.client_for_owner(), self.client_for_owner()
        lock_barrier = Barrier(2)
        original_lock = services.lock_user
        def synchronized_lock(pk):
            lock_barrier.wait(timeout=10)
            return original_lock(pk)
        # Both requests pass middleware before either gets the user-row lock.
        with patch('forum.services.lock_user', side_effect=synchronized_lock):
            results = self.in_parallel(lambda: self.change(first, 'first'), lambda: self.change(second, 'second'))
        self.assertEqual(sorted(r.status_code for r in results), [302, 403])
        self.user.refresh_from_db()
        self.assertEqual(self.user.credential_version, 2)
        self.assertIn(self.user.username, ['first', 'second'])
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_login_racing_password_change_never_retains_old_access(self):
        owner, racing = self.client_for_owner(), Client()
        results = self.in_parallel(
            lambda: self.change(owner, 'owner'),
            lambda: racing.post('/login/', {'username': 'owner', 'password': PASSWORD}))
        self.assertEqual(results[0].status_code, 302)
        self.assertIn(results[1].status_code, [302, 400])
        response = racing.post(f'/messages/{self.message.pk}/delete/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Message.objects.filter(pk=self.message.pk).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.credential_version, 2)

    def test_parallel_tag_deletions_leave_one_tag(self):
        first, second = self.client_for_owner(), self.client_for_owner()
        results = self.in_parallel(lambda: first.post(f'/tags/{self.tag.pk}/delete/'),
                                   lambda: second.post(f'/tags/{self.tag2.pk}/delete/'))
        self.assertEqual(sorted(r.status_code for r in results), [302, 409])
        self.assertEqual(self.topic.tags.count(), 1)
        self.assertEqual(Revision.objects.count(), 1)

    def test_parallel_creations_have_contiguous_history(self):
        first, second = self.client_for_owner(), self.client_for_owner()
        results = self.in_parallel(lambda: first.post('/tags/', {'name': 'C'}),
                                   lambda: second.post('/tags/', {'name': 'D'}))
        self.assertEqual([r.status_code for r in results], [302, 302])
        revisions = list(Revision.objects.order_by('pk'))
        self.assertEqual(len(revisions), 2)
        self.assertEqual(revisions[0].after, revisions[1].before)
        self.assertEqual(revisions[1].after, services.snapshot())
