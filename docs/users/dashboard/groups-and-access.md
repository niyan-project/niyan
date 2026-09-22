---
title: Groups and access
description: Organize datasets and people, and understand inherited and explicit access.
---

# Groups and access

Groups organize both people and datasets. They can contain nested subgroups, allowing paths such as `biology/genomics/reference-data`.

## Create and browse groups

Open **Groups** in the main navigation. Authorized users can create a root group. Inside an existing group, authorized users can create a subgroup or dataset.

## Manage members

Group managers see a **Members** tab. Search for an existing Niyān user by username, select that user, and assign one of these roles:

- **Reader** for viewing and downloading.
- **Contributor** for publishing dataset changes.
- **Maintainer** for managing ordinary dataset operations and access.
- **Owner** for full control of the group and its resources.

Membership can be changed or removed from the same page. Niyān does not send pending email invitations in v1; the account must already exist.

Group membership is inherited through the namespace hierarchy. A role on a parent group can therefore provide access to its subgroups and datasets.

## Grant access to one dataset

Users allowed to manage a dataset see its **Access** tab. An explicit grant can target either an existing user or a visible group and assign one of the same four roles.

Use explicit grants for exceptions. Prefer group membership when a person should have consistent access across several related datasets.

Removing an explicit grant does not necessarily remove access if the user still inherits a role through group membership.
