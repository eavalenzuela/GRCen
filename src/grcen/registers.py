"""Register framework configuration (Slice 1).

A *register* is a named, navigable, register-grade view of one asset type
(Vendor Register, Incident Register, …). Rather than hand-building a page per
type, this module declares each register as data: label, slug, curated columns,
default sort, the "status at a glance" signal, the bulk-editable fields, and the
metric cards shown above the table.

The canonical surface is the existing ``/assets?type=X`` list. A register adds:
- a pretty URL ``/registers/{slug}`` (a 302 alias to the canonical list, or to a
  richer bespoke page via ``canonical_path``),
- a ``/registers`` index landing page,
- curated columns (``?columns=curated``) + default sort applied via the alias,
- a metrics header + overdue-review badge.

Pure config + pure helpers only (no DB) so it imports cheaply and asserts at
import time. Metric *computation* lives in ``services/register_service.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from grcen.custom_fields import CUSTOM_FIELDS, FieldDef
from grcen.models.asset import ORGANIZATIONAL_TYPES, AssetType
from grcen.services.review_service import REVIEW_DATE_FIELDS

# ── column / metric / register descriptors ─────────────────────────────────


@dataclass(frozen=True)
class ColumnDef:
    """One curated column. ``key`` is a core field (``name``/``status``/``owner``/
    ``created_at``/``updated_at``/``type``), a ``meta.<field>`` custom field, or a
    ``computed.*`` column (``next_review``/``lifecycle``/``incident_state``)."""

    key: str
    label: str
    sortable: bool = True
    numeric: bool = False  # meta column that should sort numerically (Slice 3)


@dataclass(frozen=True)
class MetricDef:
    """One stat card. ``kind`` selects the aggregate computed by register_service."""

    label: str
    kind: str  # total|overdue_reviews|meta_eq|meta_in|status_eq|incident_open|meta_sum
    field: str | None = None
    value: str | None = None
    values: tuple[str, ...] | None = None
    warn: bool = False  # render in the warning color when the value is non-zero


@dataclass(frozen=True)
class RegisterDef:
    type: AssetType
    slug: str
    label: str
    plural: str
    columns: tuple[ColumnDef, ...] = ()
    default_sort: str = "name"
    default_order: str = "asc"
    lifecycle_column: str | None = None  # "status at a glance" (core status or meta.<enum>)
    owner_field: str | None = None       # accountable-party meta field (reachable via columns=all)
    bulk_fields: tuple[str, ...] = ()     # generalized bulk-edit set (Slice 2)
    metrics: tuple[MetricDef, ...] = ()
    nav_primary: bool = False
    canonical_path: str | None = None    # richer bespoke page; alias redirects there


def _m(key: str, label: str, **kw) -> ColumnDef:
    return ColumnDef(key=key, label=label, **kw)


# ── the registry ───────────────────────────────────────────────────────────

REGISTERS: dict[AssetType, RegisterDef] = {
    AssetType.VENDOR: RegisterDef(
        type=AssetType.VENDOR, slug="vendors", label="Vendor", plural="Vendors",
        columns=(
            _m("meta.tier", "Tier"),
            _m("meta.assessment_result", "Assessment"),
            _m("computed.next_review", "Next Assessment"),
            _m("meta.contract_end", "Contract End"),
        ),
        default_sort="meta.next_assessment_due", default_order="asc",
        lifecycle_column="meta.assessment_result", owner_field="security_contact",
        bulk_fields=("status", "owner", "tags", "meta.tier", "meta.next_assessment_due"),
        metrics=(
            MetricDef("Total", "total"),
            MetricDef("Overdue Assessments", "overdue_reviews", warn=True),
            MetricDef("Critical Vendors", "meta_eq", field="tier", value="critical"),
            MetricDef("Approval Gaps", "meta_in", field="assessment_result",
                      values=("not_approved", "conditionally_approved"), warn=True),
        ),
        nav_primary=True,
    ),
    AssetType.INCIDENT: RegisterDef(
        type=AssetType.INCIDENT, slug="incidents", label="Incident", plural="Incidents",
        columns=(
            _m("computed.incident_state", "State", sortable=False),
            _m("meta.severity", "Severity"),
            _m("meta.incident_type", "Type"),
            _m("meta.detected_at", "Detected"),
        ),
        default_sort="meta.detected_at", default_order="desc",
        lifecycle_column="computed.incident_state",
        bulk_fields=("status", "owner", "tags", "meta.severity"),
        metrics=(
            MetricDef("Total", "total"),
            MetricDef("Open", "incident_open", warn=True),
            MetricDef("Critical", "meta_eq", field="severity", value="critical", warn=True),
        ),
        nav_primary=True,
    ),
    AssetType.POLICY: RegisterDef(
        type=AssetType.POLICY, slug="policies", label="Policy", plural="Policies",
        columns=(
            _m("status", "Status"),
            _m("meta.policy_type", "Type"),
            _m("computed.next_review", "Next Review"),
            _m("meta.approver", "Approver"),
        ),
        default_sort="meta.review_date", default_order="asc",
        lifecycle_column="status", owner_field="approver",
        bulk_fields=("status", "owner", "tags", "meta.review_date"),
        metrics=(
            MetricDef("Total", "total"),
            MetricDef("Overdue Reviews", "overdue_reviews", warn=True),
            MetricDef("Draft", "status_eq", value="draft"),
        ),
        nav_primary=True,
    ),
    AssetType.AUDIT: RegisterDef(
        type=AssetType.AUDIT, slug="audits", label="Audit", plural="Audits",
        columns=(
            _m("status", "Status"),
            _m("meta.audit_type", "Type"),
            _m("meta.result", "Result"),
            _m("meta.end_date", "End"),
            _m("meta.open_findings", "Open Findings", numeric=True),
        ),
        default_sort="meta.end_date", default_order="desc",
        lifecycle_column="status", owner_field="auditor",
        bulk_fields=("status", "owner", "tags"),
        metrics=(
            MetricDef("Total", "total"),
            MetricDef("Open Findings", "meta_sum", field="open_findings", warn=True),
            MetricDef("Adverse Results", "meta_in", field="result",
                      values=("fail", "pass_with_exceptions"), warn=True),
        ),
        nav_primary=True,
    ),
    AssetType.SYSTEM: RegisterDef(
        type=AssetType.SYSTEM, slug="systems", label="System", plural="Systems",
        columns=(
            _m("meta.environment", "Environment"),
            _m("meta.criticality", "Criticality"),
            _m("meta.hosting", "Hosting"),
            _m("computed.next_review", "Next Review"),
        ),
        default_sort="name", lifecycle_column="status",
        bulk_fields=("status", "owner", "tags", "meta.criticality"),
        metrics=(MetricDef("Total", "total"), MetricDef("Overdue Reviews", "overdue_reviews", warn=True)),
    ),
    AssetType.DEVICE: RegisterDef(
        type=AssetType.DEVICE, slug="devices", label="Device", plural="Devices",
        columns=(
            _m("meta.device_type", "Type"),
            _m("meta.location", "Location"),
            _m("computed.next_review", "Next Review"),
        ),
        lifecycle_column="status", bulk_fields=("status", "owner", "tags"),
        metrics=(MetricDef("Total", "total"), MetricDef("Overdue Reviews", "overdue_reviews", warn=True)),
    ),
    AssetType.PROCESS: RegisterDef(
        type=AssetType.PROCESS, slug="processes", label="Process", plural="Processes",
        columns=(
            _m("meta.frequency", "Frequency"),
            _m("meta.next_execution", "Next Execution"),
            _m("meta.automation_level", "Automation"),
        ),
        lifecycle_column="status", owner_field="responsible_role",
        bulk_fields=("status", "owner", "tags", "meta.next_execution"),
        metrics=(MetricDef("Total", "total"),),
    ),
    AssetType.PERSON: RegisterDef(
        type=AssetType.PERSON, slug="people", label="Person", plural="People",
        columns=(
            _m("meta.title", "Title"),
            _m("meta.department", "Department"),
            _m("computed.next_review", "Next Review"),
        ),
        lifecycle_column="status", bulk_fields=("status", "owner", "tags"),
        metrics=(MetricDef("Total", "total"),),
    ),
    AssetType.DATA_CATEGORY: RegisterDef(
        type=AssetType.DATA_CATEGORY, slug="data-categories",
        label="Data Category", plural="Data Categories",
        columns=(
            _m("meta.classification", "Classification"),
            _m("meta.pii", "PII"),
            _m("computed.next_review", "Next Review"),
        ),
        lifecycle_column="meta.classification", bulk_fields=("status", "owner", "tags"),
        metrics=(MetricDef("Total", "total"),),
    ),
    AssetType.REQUIREMENT: RegisterDef(
        type=AssetType.REQUIREMENT, slug="requirements", label="Requirement", plural="Requirements",
        columns=(
            _m("meta.compliance_status", "Compliance"),
            _m("meta.framework", "Framework"),
            _m("meta.due_date", "Due"),
            _m("meta.priority", "Priority"),
        ),
        default_sort="meta.due_date", lifecycle_column="meta.compliance_status",
        bulk_fields=("status", "owner", "tags", "meta.due_date"),
        metrics=(MetricDef("Total", "total"),),
    ),
    AssetType.PRODUCT: RegisterDef(
        type=AssetType.PRODUCT, slug="products", label="Product", plural="Products",
        columns=(
            _m("meta.lifecycle_stage", "Lifecycle"),
            _m("meta.type", "Type"),
            _m("computed.next_review", "Next Review"),
        ),
        lifecycle_column="meta.lifecycle_stage", owner_field="developer",
        bulk_fields=("status", "owner", "tags"),
        metrics=(MetricDef("Total", "total"),),
    ),
    AssetType.INTELLECTUAL_PROPERTY: RegisterDef(
        type=AssetType.INTELLECTUAL_PROPERTY, slug="intellectual-property",
        label="Intellectual Property", plural="Intellectual Property",
        columns=(
            _m("meta.ip_type", "Type"),
            _m("meta.expiry_date", "Expiry"),
            _m("meta.confidentiality", "Confidentiality"),
        ),
        lifecycle_column="status", bulk_fields=("status", "owner", "tags", "meta.expiry_date"),
        metrics=(MetricDef("Total", "total"),),
    ),
    AssetType.ORGANIZATIONAL_UNIT: RegisterDef(
        type=AssetType.ORGANIZATIONAL_UNIT, slug="organizational-units",
        label="Organizational Unit", plural="Organizational Units",
        columns=(
            _m("meta.unit_type", "Unit Type"),
            _m("meta.headcount", "Headcount", numeric=True),
            _m("meta.ou_lead", "Lead"),
        ),
        lifecycle_column="status", owner_field="ou_lead",
        bulk_fields=("status", "owner", "tags"),
        metrics=(MetricDef("Total", "total"),),
    ),
    # Types with a richer bespoke surface — the alias redirects there.
    AssetType.RISK: RegisterDef(
        type=AssetType.RISK, slug="risks", label="Risk", plural="Risks",
        canonical_path="/risk-management", nav_primary=True,
    ),
    AssetType.CONTROL: RegisterDef(
        type=AssetType.CONTROL, slug="controls", label="Control", plural="Controls",
        canonical_path="/controls",
    ),
    AssetType.FRAMEWORK: RegisterDef(
        type=AssetType.FRAMEWORK, slug="frameworks", label="Framework", plural="Frameworks",
        canonical_path="/frameworks",
    ),
}

# Index page grouping (review C4: co-locate the compliance triad).
GROUPS: tuple[tuple[str, tuple[AssetType, ...]], ...] = (
    ("Third parties", (AssetType.VENDOR,)),
    ("Operations", (AssetType.INCIDENT, AssetType.PROCESS, AssetType.SYSTEM, AssetType.DEVICE)),
    ("Compliance", (AssetType.REQUIREMENT, AssetType.FRAMEWORK, AssetType.CONTROL)),
    ("Governance", (AssetType.POLICY, AssetType.AUDIT)),
    ("Risk", (AssetType.RISK,)),
    ("Inventory", (AssetType.PERSON, AssetType.DATA_CATEGORY, AssetType.PRODUCT,
                   AssetType.INTELLECTUAL_PROPERTY, AssetType.ORGANIZATIONAL_UNIT)),
)

_BY_SLUG: dict[str, RegisterDef] = {r.slug: r for r in REGISTERS.values()}


# ── helpers ─────────────────────────────────────────────────────────────────


def by_type(asset_type: AssetType | None) -> RegisterDef | None:
    if asset_type is None:
        return None
    return REGISTERS.get(asset_type)


def by_slug(slug: str) -> RegisterDef | None:
    return _BY_SLUG.get(slug)


def _field_def(asset_type: AssetType, name: str) -> FieldDef | None:
    return next((f for f in CUSTOM_FIELDS.get(asset_type, []) if f.name == name), None)


def _core_col(key: str, label: str | None, sortable: bool) -> dict:
    labels = {"name": "Name", "type": "Type", "status": "Status",
              "owner": "Owner", "created_at": "Created", "updated_at": "Updated"}
    return {"kind": key, "label": label or labels.get(key, key.title()),
            "sortable": sortable, "sort_key": key if sortable else None}


def _resolve_one(c: ColumnDef, register: RegisterDef, asset_type: AssetType,
                 effective_sensitive: set[str]) -> dict | None:
    key = c.key
    if key in ("name", "type", "status", "owner", "created_at", "updated_at"):
        return _core_col(key, c.label, c.sortable)
    if key.startswith("meta."):
        fname = key[len("meta."):]
        if fname in effective_sensitive:
            return None
        fd = _field_def(asset_type, fname)
        return {"kind": "meta", "label": c.label, "sortable": c.sortable,
                "sort_key": key if c.sortable else None, "meta_name": fname,
                "field_type": fd.field_type if fd else "text"}
    if key == "computed.next_review":
        fname = REVIEW_DATE_FIELDS.get(asset_type.value)
        if not fname:
            return None
        return {"kind": "next_review", "label": c.label, "sortable": True,
                "sort_key": f"meta.{fname}", "meta_name": fname}
    if key == "computed.incident_state":
        return {"kind": "incident_state", "label": c.label, "sortable": False, "sort_key": None}
    if key == "computed.lifecycle":
        lc = register.lifecycle_column
        if lc and lc.startswith("meta."):
            return _resolve_one(_m(lc, c.label), register, asset_type, effective_sensitive)
        return _core_col("status", c.label, True)
    return None


def resolve_columns(register: RegisterDef | None, mode: str, asset_type: AssetType | None,
                    effective_sensitive: set[str]) -> list[dict]:
    """Resolve the displayed columns.

    ``curated`` (register present) → ``Name`` + the register's curated columns +
    ``Owner``. Otherwise ``all`` → the core columns plus, when a single type is
    selected, every effective-non-sensitive custom field (today's behavior).
    """
    name_col = _core_col("name", "Name", True)
    owner_label = "Manager" if asset_type == AssetType.PERSON else "Owner"
    owner_col = {"kind": "owner", "label": owner_label, "sortable": True, "sort_key": "owner"}

    if register is not None and mode == "curated":
        cols = [name_col]
        for c in register.columns:
            rc = _resolve_one(c, register, asset_type, effective_sensitive)
            if rc is not None:
                cols.append(rc)
        cols.append(owner_col)
        return cols

    cols = [name_col, _core_col("type", "Type", True), _core_col("status", "Status", True),
            owner_col, _core_col("created_at", "Created", True), _core_col("updated_at", "Updated", True)]
    if asset_type is not None:
        for f in CUSTOM_FIELDS.get(asset_type, []):
            if f.sensitive or f.name in effective_sensitive:
                continue
            cols.append({"kind": "meta", "label": f.label, "sortable": True,
                         "sort_key": f"meta.{f.name}", "meta_name": f.name,
                         "field_type": f.field_type})
    return cols


def _assert_registry() -> None:
    """Fail fast on a malformed registry (non-sensitive-by-code-default, valid keys)."""
    for at, reg in REGISTERS.items():
        assert reg.type == at, f"{reg.slug}: type key mismatch"
        assert at in ORGANIZATIONAL_TYPES, f"{reg.slug}: not an organizational type"
        code_sensitive = {f.name for f in CUSTOM_FIELDS.get(at, []) if f.sensitive}
        for c in reg.columns:
            if c.key.startswith("meta."):
                fname = c.key[len("meta."):]
                # necessary-not-sufficient: org/per-asset overrides still apply at runtime
                assert fname not in code_sensitive, (
                    f"{reg.slug}: column '{fname}' is code-sensitive; not allowed as a register column"
                )
    slugs = [r.slug for r in REGISTERS.values()]
    assert len(slugs) == len(set(slugs)), "duplicate register slug"


_assert_registry()
