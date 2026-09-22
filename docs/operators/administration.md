---
title: Administer the system
description: Manage users, groups, datasets, access tokens, and permissions.
---

# Administer the system

Sign in as a staff user and open **System** in the top navigation. The system pages provide the normal administration surface; Django admin remains available for low-level recovery and inspection.

## Bootstrap the first account

A new installation has no accounts and no registration page. The operator must run Django's `createsuperuser` management command once during deployment. The [Docker Compose deployment guide](/operators/deployment#create-the-first-system-administrator) provides the exact command.

After signing in as that superuser, create ordinary user and staff accounts through **System → Users**. Reserve the unrestricted superuser account for bootstrap and recovery where practical.

## Users

Niyān has no public registration. Staff create user accounts, assign staff status where appropriate, and disable accounts that should no longer sign in.

When creating a user, the **Generate random password** action can fill a password that satisfies the configured validators. Deliver initial credentials through an approved secure channel and have the user change the password.

Access tokens are bearer credentials. Users can inspect and revoke both browser-created tokens and CLI-issued tokens under **Settings → Security → Access tokens**. Administrators should revoke tokens belonging to a compromised or deactivated account.

## Groups and memberships

Groups organize datasets and can be nested. Add an existing Niyān user by exact username; v1 has no pending email invitation workflow.

Membership roles flow through the group hierarchy according to Niyān's authorization rules. Grant the least role that permits the user's work and periodically remove obsolete memberships.

## Datasets and access

Dataset visibility follows access rules rather than public discoverability. Users without access should not see that a private dataset exists.

Dataset grants supplement inherited group access. Use them for exceptional access rather than reproducing the entire group membership structure per dataset. Download access includes the dataset's browsable metadata and history.

Dataset deletion is destructive: it removes the dataset record and its bare Git repository through a deliberately explicit confirmation flow. Ensure required retention or backup obligations are satisfied first. LFS objects that become unreferenced are reclaimed later by maintenance after the configured grace period.

## Django permissions and staff access

The **System** navigation is permission-aware. Staff status alone does not imply unrestricted domain access; assign only the Django model permissions an administrator needs. Superusers bypass those checks and should remain rare.
