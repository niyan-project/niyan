from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AccessToken, User
from accounts.tokens import InvalidAccessToken, authenticate_access_token, create_access_token, revoke_access_token
from datasets.audit import record_audit_event, record_expired_access_tokens
from datasets.models import AuditEvent, Dataset
from datasets.services import create_dataset_grant, create_protected_ref_rule, delete_dataset_grant, delete_protected_ref_rule, update_dataset_grant, update_protected_ref_rule
from namespaces.models import NamespaceMembership
from namespaces.services import create_group, create_group_membership, delete_group_membership, update_group_membership


class AuditEventTests(TestCase):
    """Verify immutable, bounded event production and owner visibility."""

    def setUp(self):
        """Create an owner, collaborator, and database-only dataset."""

        self.owner = User.objects.create_user(username='owner')
        self.collaborator = User.objects.create_user(username='collaborator')
        self.dataset = Dataset.objects.create(namespace=self.owner.personal_namespace, slug='images', name='Images', created_by=self.owner)
        self.client = Client()
        self.client.force_login(self.owner)

    def test_access_token_lifecycle_and_identifiable_rejection_are_retained(self):
        """Record issue, revoke, and rejected use without retaining the secret."""

        access_token, raw_token = create_access_token(user=self.owner, name='Workstation', scopes=['read_api'], origin=AccessToken.Origin.MANUAL)
        revoke_access_token(access_token=access_token, revoked_by=self.owner)

        with self.assertRaises(InvalidAccessToken):
            authenticate_access_token(raw_token)

        events = AuditEvent.objects.filter(token_id=access_token.id).order_by('occurred_at', 'id')
        self.assertEqual([event.action for event in events], ['access_token.issued', 'access_token.revoked', 'access_token.use_rejected'])
        self.assertEqual(events.last().reason_code, 'revoked')
        self.assertNotIn(raw_token, str([event.payload for event in events]))

    def test_collaboration_services_append_safe_resource_snapshots(self):
        """Retain group, grant, and protected-ref mutations at service boundaries."""

        group = create_group(name='Lab', slug='lab', created_by=self.owner)
        membership = create_group_membership(group=group, user=self.collaborator, role=NamespaceMembership.Role.READER, created_by=self.owner)
        update_group_membership(membership=membership, role=NamespaceMembership.Role.CONTRIBUTOR, updated_by=self.owner)
        delete_group_membership(membership=membership, deleted_by=self.owner)

        grant = create_dataset_grant(dataset=self.dataset, user=self.collaborator, role=NamespaceMembership.Role.READER, granted_by=self.owner)
        update_dataset_grant(grant=grant, role=NamespaceMembership.Role.CONTRIBUTOR, updated_by=self.owner)
        delete_dataset_grant(grant=grant, deleted_by=self.owner)
        rule = create_protected_ref_rule(
            dataset=self.dataset,
            kind='branch',
            pattern='release/*',
            minimum_role=NamespaceMembership.Role.MAINTAINER,
            deletion_minimum_role=NamespaceMembership.Role.OWNER,
            created_by=self.owner,
        )
        update_protected_ref_rule(rule=rule, updated_by=self.owner, pattern='stable/*')
        delete_protected_ref_rule(rule=rule, deleted_by=self.owner)

        self.assertEqual(
            list(AuditEvent.objects.filter(group_id=group.id, action__startswith='group.membership').values_list('action', flat=True)),
            ['group.membership_removed', 'group.membership_role_changed', 'group.membership_created', 'group.membership_created'],
        )
        dataset_actions = set(AuditEvent.objects.filter(dataset_id=self.dataset.id).values_list('action', flat=True))
        self.assertTrue(
            {
                'dataset.grant_created',
                'dataset.grant_role_changed',
                'dataset.grant_revoked',
                'dataset.protected_ref_created',
                'dataset.protected_ref_updated',
                'dataset.protected_ref_deleted',
            }.issubset(dataset_actions)
        )

    def test_events_reject_mutation_deletion_and_sensitive_payload_keys(self):
        """Keep retained rows append-only and reject likely secret metadata."""

        event = record_audit_event(action='test.accepted', actor=self.owner, scope=AuditEvent.Scope.INSTALLATION)
        event.reason_code = 'changed'

        with self.assertRaises(TypeError):
            event.save()
        with self.assertRaises(TypeError):
            AuditEvent.objects.filter(pk=event.pk).update(reason_code='changed')
        with self.assertRaises(TypeError):
            event.delete()
        with self.assertRaises(ValidationError):
            record_audit_event(action='test.rejected', actor=self.owner, scope=AuditEvent.Scope.INSTALLATION, payload={'authorization_header': 'Bearer secret'})

    def test_expiry_reconciliation_is_bounded_and_idempotent(self):
        """Let the existing worker materialize time-based expiry exactly once."""

        access_token, _ = create_access_token(user=self.owner, name='Short lived', scopes=['read_api'], origin=AccessToken.Origin.MANUAL)
        now = timezone.now()
        AccessToken.objects.filter(pk=access_token.pk).update(expires_at=now - timedelta(seconds=1))

        self.assertEqual(record_expired_access_tokens(now=now), 1)
        self.assertEqual(record_expired_access_tokens(now=now), 0)
        self.assertEqual(AuditEvent.objects.filter(action='access_token.expired', token_id=access_token.id).count(), 1)

    def test_audit_failure_rolls_back_database_backed_mutation(self):
        """Fail a security mutation before visibility when its event cannot persist."""

        with patch('datasets.services.record_audit_event', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                create_dataset_grant(dataset=self.dataset, user=self.collaborator, role=NamespaceMembership.Role.READER, granted_by=self.owner)

        self.assertFalse(self.dataset.grants.exists())

    def test_dataset_group_and_token_queries_enforce_current_scope(self):
        """Expose only owner-scoped collaboration history and one's token lifecycle."""

        record_audit_event(action='dataset.updated', actor=self.owner, scope=AuditEvent.Scope.DATASET, dataset=self.dataset)
        group = create_group(name='Lab', slug='lab', created_by=self.owner)
        token, raw_token = create_access_token(user=self.owner, name='Workstation', scopes=['read_api'], origin=AccessToken.Origin.MANUAL)
        replacement = 'A' if raw_token[-1] != 'A' else 'B'
        wrong_token = raw_token[:-1] + replacement
        with self.assertRaises(InvalidAccessToken):
            authenticate_access_token(wrong_token)

        dataset_response = self.client.get(f'/api/v1/datasets/{self.dataset.id}/audit-events')
        group_response = self.client.get(f'/api/v1/namespaces/{group.id}/audit-events')
        token_response = self.client.get('/api/v1/auth/audit-events')

        self.assertEqual(dataset_response.status_code, 200)
        self.assertEqual(dataset_response.json()['items'][0]['action'], 'dataset.updated')
        self.assertEqual(group_response.status_code, 200)
        self.assertTrue(all(item['group_id'] == str(group.id) for item in group_response.json()['items']))
        self.assertEqual(token_response.status_code, 200)
        self.assertEqual({item['token_id'] for item in token_response.json()['items']}, {str(token.id)})
        self.assertIn('invalid_secret', {item['reason_code'] for item in token_response.json()['items']})

        create_dataset_grant(dataset=self.dataset, user=self.collaborator, role=NamespaceMembership.Role.READER, granted_by=self.owner)
        self.client.force_login(self.collaborator)
        forbidden = self.client.get(f'/api/v1/datasets/{self.dataset.id}/audit-events')
        hidden_group = self.client.get(f'/api/v1/namespaces/{group.id}/audit-events')
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(hidden_group.status_code, 404)
