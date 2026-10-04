import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import User, Tag, Topic, Message, Revision
from .services import snapshot, restore_revision

PASSWORD = 'Orchid-forest-53!'
NEW_PASSWORD = 'River-and-sky-72!'


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ForumTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user('alice', password=PASSWORD)
        cls.bob = User.objects.create_user('bob', password=PASSWORD)
        cls.mod = User.objects.create_user('moderator', password=PASSWORD, is_moderator=True)
        cls.other_mod = User.objects.create_user('other_moderator', password=PASSWORD, is_moderator=True)
        cls.tag = Tag.objects.create(name='AI', author=cls.mod)
        cls.tag2 = Tag.objects.create(name='Django', author=cls.mod)
        cls.foreign_tag = Tag.objects.create(name='ML', author=cls.other_mod)
        cls.topic = Topic.objects.create(title='Обсуждение', description='Текст', author=cls.alice)
        cls.topic.tags.add(cls.tag)
        cls.message = Message.objects.create(topic=cls.topic, text='Ответ', author=cls.bob)

    def login(self, user=None, client=None):
        client = client or self.client
        response = client.post(reverse('login'), {'username': (user or self.alice).username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)
        return client

    def create_topic(self, **overrides):
        data = {'title': 'Новая тема', 'description': 'Описание', 'tags': [self.tag.pk]}
        data.update(overrides)
        return self.client.post(reverse('topic_create'), data)

    def change(self, client=None, **overrides):
        data = {'current_password': PASSWORD, 'username': 'renamed', 'password1': NEW_PASSWORD, 'password2': NEW_PASSWORD}
        data.update(overrides)
        return (client or self.client).post(reverse('credentials'), data)

    def test_public_pages_and_health(self):
        for name, args in [('home', []), ('search', []), ('topic', [self.topic.pk]), ('tags', []),
                           ('register', []), ('login', []), ('health', [])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)
        self.assertEqual(self.client.get('/health/').json()['status'], 'ok')

    def test_guest_cannot_mutate_any_content(self):
        before = snapshot()
        requests = [('/topics/new/', {'title': 'x', 'description': 'y', 'tags': [self.tag.pk]}),
                    (f'/topics/{self.topic.pk}/messages/', {'text': 'x'}),
                    (f'/topics/{self.topic.pk}/delete/', {}),
                    (f'/messages/{self.message.pk}/delete/', {}), ('/tags/', {'name': 'x'}),
                    (f'/tags/{self.tag.pk}/delete/', {})]
        for path, data in requests:
            with self.subTest(path=path):
                self.assertIn(self.client.post(path, data).status_code, (302, 403))
                self.assertEqual(snapshot(), before)
        self.assertEqual(Revision.objects.count(), 0)

    def test_registration_hashes_password_and_assigns_member(self):
        result = self.client.post('/register/', {'username': 'newuser', 'password1': PASSWORD, 'password2': PASSWORD})
        self.assertEqual(result.status_code, 302)
        user = User.objects.get(username='newuser')
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        self.assertFalse(user.is_moderator or user.is_staff or user.is_superuser)

    def test_registration_rejects_role_forgery(self):
        result = self.client.post('/register/', {'username': 'newuser', 'password1': PASSWORD,
                                                'password2': PASSWORD, 'is_moderator': 'true'})
        self.assertEqual(result.status_code, 400)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_registration_rejects_weak_mismatched_and_duplicate_credentials(self):
        for username, p1, p2 in [('alice', PASSWORD, PASSWORD), ('new', '123', '123'), ('new', PASSWORD, NEW_PASSWORD)]:
            self.assertEqual(self.client.post('/register/', {'username': username, 'password1': p1, 'password2': p2}).status_code, 400)

    def test_end_to_end_create_search_reply_delete(self):
        self.login()
        self.assertEqual(self.create_topic().status_code, 302)
        topic = Topic.objects.latest('pk')
        self.assertEqual(topic.author, self.alice)
        self.assertEqual(list(topic.tags.all()), [self.tag])
        self.assertContains(self.client.get('/topics/', {'tags': self.tag.pk}), 'Новая тема')
        self.login(self.bob)
        self.assertEqual(self.client.post(reverse('message_create', args=[topic.pk]), {'text': 'Ответ участника'}).status_code, 302)
        message = topic.messages.get()
        self.assertEqual(message.author, self.bob)
        self.assertEqual(self.client.post(reverse('message_delete', args=[message.pk])).status_code, 302)
        self.login()
        self.assertEqual(self.client.post(reverse('topic_delete', args=[topic.pk])).status_code, 302)
        self.assertFalse(Topic.objects.filter(pk=topic.pk).exists())
        self.assertEqual(Revision.objects.count(), 4)

    def test_topic_requires_valid_tag_and_nonblank_text(self):
        self.login()
        for data in [{'tags': []}, {'tags': [999999]}, {'title': '  '}, {'description': ''}, {'title': 'x' * 201}]:
            with self.subTest(data=data):
                self.assertEqual(self.create_topic(**data).status_code, 400)
        self.assertEqual(Topic.objects.count(), 1)

    def test_message_rejects_blank_and_missing_topic(self):
        self.login()
        self.assertEqual(self.client.post(reverse('message_create', args=[self.topic.pk]), {'text': '  '}).status_code, 400)
        self.assertEqual(self.client.post('/topics/999999/messages/', {'text': 'text'}).status_code, 404)
        self.assertEqual(Message.objects.count(), 1)

    def test_member_cannot_delete_others_objects(self):
        self.login(self.bob)
        self.assertEqual(self.client.post(reverse('topic_delete', args=[self.topic.pk])).status_code, 403)
        self.login()
        self.assertEqual(self.client.post(reverse('message_delete', args=[self.message.pk])).status_code, 403)
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())
        self.assertTrue(Message.objects.filter(pk=self.message.pk).exists())

    def test_owner_deletes_topic_and_all_member_messages(self):
        self.login()
        self.assertEqual(self.client.post(reverse('topic_delete', args=[self.topic.pk])).status_code, 302)
        self.assertFalse(Message.objects.exists())

    def test_moderator_deletes_member_content(self):
        self.login(self.mod)
        self.assertEqual(self.client.post(reverse('message_delete', args=[self.message.pk])).status_code, 302)
        self.assertEqual(self.client.post(reverse('topic_delete', args=[self.topic.pk])).status_code, 302)

    def test_moderator_cannot_delete_other_moderator_objects(self):
        topic = Topic.objects.create(title='Moderator', description='x', author=self.other_mod)
        topic.tags.add(self.tag)
        message = Message.objects.create(topic=topic, text='x', author=self.other_mod)
        self.login(self.mod)
        before = snapshot()
        for route, pk in [('topic_delete', topic.pk), ('message_delete', message.pk), ('tag_delete', self.foreign_tag.pk)]:
            self.assertEqual(self.client.post(reverse(route, args=[pk])).status_code, 403)
        self.assertEqual(snapshot(), before)

    def test_moderator_can_delete_own_objects(self):
        self.login(self.mod)
        self.create_topic()
        topic = Topic.objects.latest('pk')
        self.client.post(reverse('message_create', args=[topic.pk]), {'text': 'own'})
        self.assertEqual(self.client.post(reverse('message_delete', args=[topic.messages.get().pk])).status_code, 302)
        self.assertEqual(self.client.post(reverse('topic_delete', args=[topic.pk])).status_code, 302)
        self.assertEqual(self.client.post(reverse('tag_delete', args=[self.tag2.pk])).status_code, 302)

    def test_cascade_cannot_remove_other_moderator_message(self):
        Message.objects.create(topic=self.topic, author=self.other_mod, text='Protected')
        for actor in [self.alice, self.mod]:
            self.login(actor)
            self.assertEqual(self.client.post(reverse('topic_delete', args=[self.topic.pk])).status_code, 409)
        self.assertEqual(self.topic.messages.count(), 2)

    def test_member_cannot_manage_tags(self):
        self.login()
        self.assertEqual(self.client.post('/tags/', {'name': 'forged'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('tag_delete', args=[self.tag.pk])).status_code, 403)

    def test_tag_create_and_case_insensitive_duplicate(self):
        self.login(self.mod)
        self.assertEqual(self.client.post('/tags/', {'name': 'Robotics'}).status_code, 302)
        self.assertEqual(Tag.objects.get(name='Robotics').author, self.mod)
        self.assertEqual(self.client.post('/tags/', {'name': 'robotics'}).status_code, 400)
        self.assertEqual(Revision.objects.count(), 1)

    def test_last_tag_deletion_forbidden_but_redundant_tag_allowed(self):
        self.login(self.mod)
        self.assertEqual(self.client.post(reverse('tag_delete', args=[self.tag.pk])).status_code, 409)
        self.topic.tags.add(self.tag2)
        self.assertEqual(self.client.post(reverse('tag_delete', args=[self.tag.pk])).status_code, 302)
        self.assertEqual(list(self.topic.tags.all()), [self.tag2])

    def test_author_account_role_forgery_rejected(self):
        self.login(self.mod)
        before = snapshot()
        for key in ['author', 'author_id', 'user_id', 'is_moderator']:
            self.assertEqual(self.create_topic(**{key: self.bob.pk}).status_code, 400)
            self.assertEqual(self.client.post(reverse('message_create', args=[self.topic.pk]), {'text': 'x', key: self.bob.pk}).status_code, 400)
            self.assertEqual(self.client.post('/tags/', {'name': 'forged', key: self.bob.pk}).status_code, 400)
            self.assertEqual(self.client.post(reverse('topic_delete', args=[self.topic.pk]), {key: self.alice.pk}).status_code, 400)
        self.assertEqual(snapshot(), before)

    def test_get_deletion_logout_and_edit_routes_do_not_mutate(self):
        self.login()
        before = snapshot()
        for url in [reverse('topic_delete', args=[self.topic.pk]), reverse('message_delete', args=[self.message.pk]),
                    reverse('tag_delete', args=[self.tag.pk]), '/logout/']:
            self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(f'/topics/{self.topic.pk}/edit/', {'title': 'x'}).status_code, 404)
        self.assertEqual(snapshot(), before)

    def test_unsupported_json_cannot_bypass_field_validation(self):
        self.login()
        response = self.client.post(reverse('topic_delete', args=[self.topic.pk]),
                                    json.dumps({'author': self.bob.pk}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())

    def test_csrf_enforced_for_all_mutations(self):
        c = Client(enforce_csrf_checks=True)
        c.get('/login/')
        token = c.cookies['csrftoken'].value
        self.assertEqual(c.post('/login/', {'username': 'alice', 'password': PASSWORD, 'csrfmiddlewaretoken': token}).status_code, 302)
        before = snapshot()
        for url, data in [('/topics/new/', {'title': 'x'}), ('/tags/', {'name': 'x'}),
                          (reverse('topic_delete', args=[self.topic.pk]), {}),
                          (reverse('message_create', args=[self.topic.pk]), {'text': 'x'}),
                          ('/account/credentials/', {'username': 'forged'}), ('/logout/', {})]:
            self.assertEqual(c.post(url, data).status_code, 403)
        self.assertEqual(snapshot(), before)

    def test_text_is_escaped_not_executed(self):
        self.login()
        payload = '<script>alert(1)</script>'
        self.create_topic(title=payload, description=payload)
        topic = Topic.objects.latest('pk')
        self.client.post(reverse('message_create', args=[topic.pk]), {'text': payload})
        response = self.client.get(reverse('topic', args=[topic.pk]))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, payload)

    def test_search_counts_distinct_tags_messages_and_orders_matches_first(self):
        both = Topic.objects.create(title='Both', description='x', author=self.alice)
        both.tags.add(self.tag, self.tag2)
        for _ in range(3):
            Message.objects.create(topic=self.topic, author=self.alice, text='x')
        response = self.client.get('/topics/', {'tags': [self.tag.pk, self.tag2.pk]})
        topics = list(response.context['page_obj'])
        self.assertEqual([t.pk for t in topics], [both.pk, self.topic.pk])
        self.assertEqual(topics[0].matched_tags, 2)
        self.assertEqual(topics[1].message_count, 4)

    def test_search_tiebreak_uses_age_and_activity(self):
        old = Topic.objects.create(title='Old', description='x', author=self.alice)
        old.tags.add(self.tag)
        Topic.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=100))
        for _ in range(4):
            Message.objects.create(topic=old, author=self.alice, text='x')
        response = self.client.get('/topics/', {'tags': self.tag.pk})
        self.assertEqual(response.context['page_obj'][0].pk, self.topic.pk)

    def test_search_invalid_tag_rejected(self):
        for value in ['garbage', '999999']:
            self.assertEqual(self.client.get('/topics/', {'tags': value}).status_code, 400)

    def test_top_three_and_countdown(self):
        for n in range(4):
            topic = Topic.objects.create(title=f'Top {n}', description='x', author=self.alice)
            topic.tags.add(self.tag)
            for _ in range(n + 2):
                Message.objects.create(topic=topic, author=self.alice, text='x')
        response = self.client.get('/')
        self.assertEqual([t.title for t in response.context['topics']], ['Top 3', 'Top 2', 'Top 1'])
        with override_settings(COUNTDOWN_TARGET='2030-01-01T00:00:00+03:00'):
            self.assertContains(self.client.get('/'), 'id="countdown"')

    def test_invalid_login_and_inactive_account(self):
        for name in ['alice', 'nonexistent']:
            self.assertEqual(self.client.post('/login/', {'username': name, 'password': 'wrong'}).status_code, 400)
        User.objects.filter(pk=self.alice.pk).update(is_active=False)
        self.assertEqual(self.client.post('/login/', {'username': 'alice', 'password': PASSWORD}).status_code, 400)

    def test_session_has_no_credentials_and_absolute_seven_day_expiry(self):
        self.login()
        session = self.client.session
        self.assertNotIn('username', session)
        self.assertNotIn('password', session)
        self.assertLessEqual(session.get_expiry_age(), 604800)
        cookie = self.client.cookies['sessionid']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertNotIn(PASSWORD, cookie.value)
        self.assertEqual(self.client.get('/').headers['Cache-Control'], 'no-store')

    def test_expired_session_cannot_mutate(self):
        self.login()
        Session.objects.filter(session_key=self.client.session.session_key).update(expire_date=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.create_topic().status_code, 302)
        self.assertEqual(Topic.objects.count(), 1)

    def test_credentials_change_revokes_both_sessions_and_preserves_role_author(self):
        first = self.login(self.mod)
        second = self.login(self.mod, Client())
        self.assertEqual(self.change(first, username='newmoderator').status_code, 302)
        for c in [first, second]:
            self.assertEqual(c.post(reverse('message_delete', args=[self.message.pk])).status_code, 302)
            self.assertTrue(Message.objects.filter(pk=self.message.pk).exists())
        self.assertEqual(first.post('/login/', {'username': 'moderator', 'password': PASSWORD}).status_code, 400)
        self.assertEqual(first.post('/login/', {'username': 'newmoderator', 'password': NEW_PASSWORD}).status_code, 302)
        self.mod.refresh_from_db()
        self.assertTrue(self.mod.is_moderator)
        self.assertEqual(self.tag.author_id, self.mod.pk)
        self.assertEqual(first.post(reverse('message_delete', args=[self.message.pk])).status_code, 302)

    def test_username_only_change_revokes_sessions(self):
        self.login()
        second = self.login(client=Client())
        self.assertEqual(self.change(password1='', password2='').status_code, 302)
        self.assertEqual(second.post(reverse('topic_delete', args=[self.topic.pk])).status_code, 302)
        self.assertTrue(Topic.objects.filter(pk=self.topic.pk).exists())
        self.assertEqual(self.client.post('/login/', {'username': 'alice', 'password': PASSWORD}).status_code, 400)
        self.assertEqual(self.client.post('/login/', {'username': 'renamed', 'password': PASSWORD}).status_code, 302)

    def test_password_only_change(self):
        self.login()
        self.assertEqual(self.change(username='alice').status_code, 302)
        self.assertEqual(self.client.post('/login/', {'username': 'alice', 'password': PASSWORD}).status_code, 400)
        self.assertEqual(self.client.post('/login/', {'username': 'alice', 'password': NEW_PASSWORD}).status_code, 302)

    def test_invalid_credential_change_keeps_data_and_session(self):
        self.login()
        version = self.alice.credential_version
        for data in [{'current_password': 'wrong'}, {'username': 'bob'}, {'password1': '123', 'password2': '123'},
                     {'password2': 'mismatch'}, {'username': 'bad name'}, {'user_id': self.bob.pk},
                     {'is_moderator': '1'}, {'username': 'alice', 'password1': '', 'password2': ''}]:
            with self.subTest(data=data):
                self.assertEqual(self.change(**data).status_code, 400)
                self.alice.refresh_from_db()
                self.assertEqual(self.alice.username, 'alice')
                self.assertEqual(self.alice.credential_version, version)
                self.assertTrue(self.alice.check_password(PASSWORD))
                self.assertEqual(self.client.get('/account/credentials/').status_code, 200)

    def test_credential_change_transaction_rolls_back(self):
        self.login()
        original_save = User.save
        def fail_after_save(user, *args, **kwargs):
            original_save(user, *args, **kwargs)
            raise IntegrityError('simulated failure before commit')
        with patch.object(User, 'save', fail_after_save):
            self.assertEqual(self.change().status_code, 400)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.username, 'alice')
        self.assertEqual(self.alice.credential_version, 1)
        self.assertTrue(self.alice.check_password(PASSWORD))
        self.assertEqual(self.client.get('/account/credentials/').status_code, 200)

    def test_role_change_operator_command_revokes_sessions(self):
        self.login()
        call_command('set_moderator', 'alice', stdout=StringIO())
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.is_moderator)
        self.assertEqual(self.create_topic().status_code, 302)
        self.assertEqual(Topic.objects.count(), 1)

    def test_history_restores_deleted_topic_messages_tags_dates_and_authorship(self):
        self.login()
        before = snapshot()
        self.client.post(reverse('topic_delete', args=[self.topic.pk]))
        revision = Revision.objects.latest('pk')
        self.assertEqual(revision.before, before)
        restore_revision(revision.pk)
        self.assertEqual(snapshot(), before)
        self.assertEqual(Revision.objects.count(), 2)
        self.assertEqual(Revision.objects.get(pk=revision.pk).before, before)

    def test_history_keeps_multiple_states_and_undoes_restore(self):
        self.login(self.mod)
        before = snapshot()
        self.client.post('/tags/', {'name': 'New'})
        creation = Revision.objects.latest('pk')
        after_create = snapshot()
        self.client.post(reverse('tag_delete', args=[Tag.objects.get(name='New').pk]))
        restore_revision(creation.pk, 'before')
        self.assertEqual(snapshot(), before)
        restore_revision(creation.pk, 'after')
        self.assertEqual(snapshot(), after_create)
        undo = Revision.objects.latest('pk')
        restore_revision(undo.pk, 'before')
        self.assertEqual(snapshot(), before)
        self.assertEqual(Revision.objects.get(pk=creation.pk).after, after_create)

    def test_history_has_no_passwords_and_does_not_restore_credentials(self):
        self.login()
        self.create_topic()
        revision = Revision.objects.latest('pk')
        self.change()
        restore_revision(revision.pk)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.check_password(NEW_PASSWORD))
        encoded = json.dumps(revision.before)
        self.assertNotIn('password', encoded)
        self.assertNotIn('session', encoded)

    def test_failed_snapshot_rolls_back_content_change(self):
        self.login()
        before = snapshot()
        with patch.object(Revision.objects, 'create', side_effect=IntegrityError('disk full')):
            self.assertEqual(self.create_topic().status_code, 400)
        self.assertEqual(snapshot(), before)
        self.assertEqual(Revision.objects.count(), 0)

    def test_logout_invalidates_session_and_prevents_replay(self):
        self.login()
        old_cookie = self.client.cookies['sessionid'].value
        self.assertEqual(self.client.post('/logout/').status_code, 302)
        self.client.cookies['sessionid'] = old_cookie
        self.assertEqual(self.create_topic().status_code, 302)
        self.assertEqual(Topic.objects.count(), 1)
