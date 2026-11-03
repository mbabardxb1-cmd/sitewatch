# Roles, permissions and tenant isolation

_The permission table below is generated from `backend/app/auth/permissions.py`._

| Permission | Description | Super Admin | Developer | Client |
|---|---|:-:|:-:|:-:|
| `user.manage` | Create, edit, deactivate users and assign roles | ✓ |  |  |
| `client.manage` | Create and edit client organizations | ✓ |  |  |
| `client.read` | List client organizations | ✓ | ✓ |  |
| `settings.manage` | Change system settings, schedules and report templates | ✓ |  |  |
| `integration.manage` | Manage hosting/registrar API integrations | ✓ |  |  |
| `notification.manage` | Manage notification rules and recipients | ✓ |  |  |
| `notification.read` | View notification rules and delivery logs | ✓ | ✓ |  |
| `website.all` | Access every website regardless of assignment | ✓ |  |  |
| `website.read` | View assigned websites | ✓ | ✓ | ✓ |
| `website.write` | Create, edit and archive websites | ✓ | ✓ |  |
| `website.assign` | Assign users to websites | ✓ |  |  |
| `website.crawl_config` | Change crawl configuration | ✓ | ✓ |  |
| `uptime.read_detailed` | View raw checks, HTTP codes, error details and logs | ✓ | ✓ |  |
| `uptime.check_now` | Run a manual uptime check | ✓ | ✓ |  |
| `incident.read` | View incidents | ✓ | ✓ | ✓ |
| `incident.manage` | Acknowledge incidents | ✓ | ✓ |  |
| `audit.read_detailed` | View full audit findings | ✓ | ✓ |  |
| `audit.run` | Trigger a manual audit | ✓ | ✓ |  |
| `audit.export` | Export audit results | ✓ | ✓ |  |
| `issue.read_summary` | View summarized issues | ✓ | ✓ | ✓ |
| `issue.read_detailed` | View full issue details | ✓ | ✓ |  |
| `issue.manage` | Change issue status and assignee | ✓ | ✓ |  |
| `issue.note.read` | Read internal notes | ✓ | ✓ |  |
| `issue.note.write` | Write internal notes | ✓ | ✓ |  |
| `expiry.read` | View domain/hosting/SSL status (client users also need the client flag) | ✓ | ✓ | ✓ |
| `expiry.read_detailed` | View registrar/hosting details and internal notes | ✓ | ✓ |  |
| `expiry.write` | Enter hosting renewal data and refresh lookups | ✓ | ✓ |  |
| `report.read` | View and download approved client reports | ✓ | ✓ | ✓ |
| `report.read_all` | View and download all reports including technical ones | ✓ | ✓ |  |
| `report.generate` | Generate reports manually | ✓ | ✓ |  |
| `report.approve` | Approve reports for client access and email them | ✓ |  |  |
| `maintenance.write` | Record maintenance work performed | ✓ | ✓ |  |

## How access is decided (in this order)

1. **Authentication:** session cookie (HttpOnly, Secure), checked on every request. Users of a
   deactivated client organization are refused. → `401`
2. **CSRF:** every state-changing request must carry the session's token in `X-CSRF-Token`.
   → `403`
3. **Permission:** each endpoint declares the permission(s) it needs (`require(...)`). → `403`
4. **Tenant scope:** every query for website-owned data is filtered through
   `app/auth/scope.py`:
   - super administrators see every website;
   - developers see websites they are assigned to;
   - client users see websites they are assigned to **and** that belong to their own
     organization (so a mistaken assignment still grants nothing).
   Out-of-scope resources return `404`, exactly like resources that do not exist, so IDs can't
   be probed.
5. **Shaping:** client users receive separate response models that do not contain technical
   fields at all (incident causes, raw errors, check logs, crawl configuration, recipients,
   assignments, account references, internal notes, raw lookup data).

Extra rules on top of the matrix:
- Client reports are visible to clients only once published; technical reports never are.
- Expiry information is visible to client users only if their organization has it enabled.
- Informational issues and internal maintenance entries are hidden from clients.
- Dashboard notices for clients omit operational detail (incident causes, manual-entry
  markers, worker problems).

## How it is tested

`backend/tests/test_permission_matrix.py`:
- **Completeness:** every API route must be classified in the matrix, or the test fails (so
  new endpoints can't skip review).
- **Matrix:** every route (78) is called by six actors: anonymous, client of tenant A, client of
  tenant B, unassigned developer, assigned developer and super admin, with valid request bodies
  so validation can't mask authorization. The expected result is derived from the role
  catalogue: 401 / 403 / 404 / allowed. 5xx is never accepted.
- **Leak scan:** for actors outside tenant A, no allowed response may contain tenant A's
  website, incident, issue, audit, report or maintenance IDs, names or hostnames.
- **CSRF:** every state-changing route without the token is refused.
- **Client payloads:** responses to client users are scanned for staff-only field names.

The matrix was itself checked by deliberately breaking the code. Removing a scope filter from
an incident lookup, removing it from the incidents list, and loosening the permission on raw
checks were each caught, the second one by the leak scan naming the leaked IDs.

## Design decisions to be aware of
- Developers can list all client organizations (names and contact emails) so they can register
  websites for any client; they only see websites they are assigned to.
- A developer who registers a website is assigned to it automatically. Only super
  administrators can change assignments, publish client reports, manage users, settings,
  notification rules and integrations.

