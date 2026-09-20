# Niyān Web Application

- **Status:** Accepted
- **Audience:** web, server, deployment, and viewer-plugin maintainers
- **Last reviewed:** 2026-09-20

## Purpose

The Niyān web application is the authenticated, Git-forge-style interface for discovering groups and datasets, browsing repository history and files, approving CLI login, and administering access. It is a control-plane client; Django remains authoritative for identity, authorization, Git state access, and transfer authorization.

The web interface has no public repositories, anonymous discovery, stars, followers, or other social-forge behavior in v1.

This specification follows [ADR 0010](../architecture/decisions/0010-static-nuxt-spa.md).

## Application and Deployment Model

The application must use the latest compatible stable Nuxt 4.x and Nuxt UI releases selected when its dependency lockfile is updated. It uses TypeScript and Vue Composition API conventions.

The production build is a client-rendered SPA with Nuxt server-side rendering disabled. It must not require a Node.js runtime after generation. Generated build assets live under `/static/niyan/`; API, Git, Django admin, and static requests retain their own Django-owned routes, while other browser routes fall back to the SPA entry document.

Development uses the Nuxt development server with a Nitro development proxy for Django-owned paths. Browser API code must use relative URLs so development and production share cookie, CSRF, and routing behavior without establishing a public cross-origin API contract.

The build may receive the canonical installation origin through `NIYAN_PUBLIC_URL`. The web application uses that origin in CLI authentication instructions and standard Git URLs, falling back to the browser's current origin when it is omitted. V1 supports installation at an origin root and does not claim URL-path-prefix deployment support.

WhiteNoise is the initial production static-file server. Deployments may place a reverse proxy in front of the same generated files without changing public paths.

## Visual System

The application should use Nuxt UI components wherever an appropriate accessible component exists. Tailwind CSS utilities should own layout, spacing, responsive behavior, and ordinary visual styling. Custom CSS is reserved for semantic theme tokens, rendered untrusted-content containment, viewer isolation, or a layout that cannot be expressed clearly with the established utilities.

The interface uses:

- Indigo as the semantic primary color for actions, links, focus, and selected state;
- neutral Zinc-like surfaces, borders, and typography, including black and gray dark-mode backgrounds that are not broadly tinted by the primary color;
- light, dark, and operating-system-following appearance modes;
- compact, information-dense Git-forge patterns such as breadcrumbs, repository tabs, branch selectors, file tables, commit history, and README panels; and
- familiar interaction patterns without copying unrelated software-development or social features.

Color must never be the only indicator of meaning. Keyboard focus, contrast, touch targets, reduced-motion preferences, and semantic labels are baseline requirements rather than later polish.

## Browser Authentication

There is no public registration. An installation administrator creates the initial Django superuser, signs in, and provisions additional users through the staff-only System area. Django admin remains available for assigning staff status and Django auth-group permissions.

The web application must provide username-and-password sign-in, sign-out, current-account state, and CSRF initialization through same-origin Django endpoints. It must not store passwords, session identifiers, access-token secrets, or signed object-storage URLs in browser-persistent storage.

The primary header exposes datasets and groups beside the Niyān logo. Staff users with at least one supported installation-wide permission also see System immediately after Groups. Account-specific destinations belong in a compact user menu beside the independent color-mode control. That menu contains only Settings and Log out rather than placing access tokens or individual settings destinations in primary navigation.

Settings use a persistent left sidebar organized into labeled sections. V1 exposes one section, Security, with separate email, password-and-authentication, and access-token pages; it does not add a redundant account-review landing page. Email and password changes are available only through an authenticated browser session, require the current password, and remain CSRF-protected. A successful password change retains the explicitly confirmed current session; Django's session-auth hash invalidates other sessions that still carry the old password hash.

System administration uses the same persistent-sidebar pattern but is not part of personal Settings. Its initial Administration section contains Users, Groups, and Datasets, hiding destinations for which the current staff user has no supported Django model permission. The Users page lists accounts with `accounts.view_user` and provisions active, non-staff accounts with `accounts.add_user`; every new account receives its personal namespace in the same operation. Staff status, superuser status, Django auth-group membership, and user deletion remain Django-admin operations until their product workflows and destructive effects are specified. The Groups and Datasets pages provide installation-wide discovery and link into the ordinary resource pages, whose existing mutations remain authoritative.

Anonymous navigation to an authenticated page redirects to sign-in while preserving a safe local return path. Authenticated navigation to sign-in redirects to the dashboard.

The CLI device-authorization page must show the requesting device label, requested scopes, resource boundary, dataset path when present, and expiry before the user approves or denies it. Authentication does not imply approval.

## Dashboard and Groups

The signed-in dashboard lists datasets and groups visible to the user. The product UI calls group namespaces **groups**; `namespace` remains an API and internal domain term where precision is required.

Users may create root or nested groups. The creator becomes an owner. A user with inherited owner access to a group may update the group's name or slug, create child groups, manage its direct memberships, and permanently delete an empty group. A group containing child groups or datasets must not be deleted until those resources are moved or removed.

Group membership management lists, adds, changes, and removes direct members. Inherited membership is shown as effective access where useful but must not be represented as an editable direct record. The interface must not expose Django auth groups as Niyān groups.

## Dataset Management

The dashboard and group pages allow authorized users to create dataset records with optional descriptions, rename datasets, change dataset slugs and descriptions, and permanently delete datasets using exact-path confirmation. Dataset content creation and browser upload are outside Phase 3; users populate and mutate repository content through the recommended `niyan` CLI or supported standard Git and Git LFS clients.

Changing a human-facing group or dataset path must not imply an identity change. The frontend must use immutable UUIDs for API mutation and refresh path-based routes after a successful rename.

Inaccessible datasets and groups are rendered as not found rather than revealing their existence.

## Repository Browser

The dataset page provides separate branch and tag views, commits, tree breadcrumbs, file metadata, root README rendering, and authorized downloads. Every tree, commit, README, and file request must expose or retain the exact resolved commit so one page load does not silently combine moving revisions.

The clone action uses a compact popover that presents the recommended `niyan dataset clone` command and a supported standard `git clone` command with separate copy controls. It explains that Niyān configures authentication, shallow history, automatic LFS attributes, selective materialization, and multipart uploads, while standard Git users choose those behaviors themselves. The standard Git URL is a supported product interface rather than a recovery-only interoperability detail.

An empty dataset replaces the ordinary file table with a first-push guide. It leads with the recommended Niyān workflow and may also show the equivalent standard Git and Git LFS workflow, including the user's responsibility to configure `.gitattributes` and suitable clone depth.

README Markdown is untrusted. Raw HTML is disabled and generated links must use safe protocols. Unsupported file formats receive metadata and download actions rather than ad hoc previews. Format-specific inline experiences belong to the viewer-plugin contract.

Git-resident file downloads may stream from the repository endpoint. Git LFS downloads must use a short-lived action returned after Django authorization and transfer bytes directly from object storage.

## Access Management

Dataset owners can list, create, edit, and revoke direct user and group grants. Group owners can similarly manage direct group memberships. Principal-selection interfaces search eligible users and groups by their human-facing labels; they must not require a person to paste database identifiers.

Every user can list manually and CLI-issued access tokens, inspect their non-secret origin, scopes, resource boundary, expiry, last use, and active status, create a manual token, and revoke a token. A newly created secret is displayed exactly once and is not placed in URLs, logs, or browser-persistent storage.

## Failure and Security Behavior

- Browser mutations must include a valid Django CSRF token.
- Authorization failures are determined by Django and must not be approximated by hidden buttons alone.
- API errors should preserve a stable public code while the UI gives a concise, actionable explanation.
- Destructive operations require explicit confirmation and must not be triggered by an ordinary navigation link.
- Signed transfer URLs are ephemeral values and must not be cached in persistent client state.
- Rendered README and future viewer output must not execute untrusted scripts or HTML.

## Phase 3 Acceptance Boundary

Phase 3 is complete when authenticated users can sign in, navigate visible groups and datasets, manage permitted metadata and access, review CLI device authorization, browse a populated repository, download ordinary and LFS-backed files through their correct transfer paths, manage tokens, and complete these workflows with keyboard navigation and baseline automated accessibility coverage.

Browser content upload, public visibility, social features, server-side rendering, full-text search, semantic file previews, and viewer plugins are not part of this phase.
