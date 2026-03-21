from dataclasses import dataclass, field

from grcen.models.asset import AssetType


@dataclass
class FieldDef:
    name: str
    label: str
    field_type: str  # "text", "date", "integer", "boolean", "enum"
    required: bool = False
    choices: list[str] | None = None
    help_text: str = ""


CUSTOM_FIELDS: dict[AssetType, list[FieldDef]] = {
    AssetType.PERSON: [
        FieldDef("manager", "Manager", "text"),
        FieldDef("title", "Job Title", "text"),
        FieldDef("department", "Department", "text"),
        FieldDef("email", "Email", "text"),
        FieldDef(
            "employment_type",
            "Employment Type",
            "enum",
            choices=["full_time", "contractor", "vendor", "intern"],
        ),
        FieldDef("start_date", "Start Date", "date"),
        FieldDef("end_date", "End Date", "date"),
        FieldDef("last_reviewed", "Last Reviewed", "date"),
        FieldDef("next_review_due", "Next Review Due", "date"),
    ],
    AssetType.POLICY: [
        FieldDef("version", "Version", "text"),
        FieldDef("effective_date", "Effective Date", "date"),
        FieldDef("review_cadence", "Review Cadence", "text"),
        FieldDef("review_date", "Next Review Date", "date"),
        FieldDef("approver", "Approver", "text"),
        FieldDef("scope", "Scope", "text"),
        FieldDef(
            "classification",
            "Classification",
            "enum",
            choices=["public", "internal", "confidential", "restricted"],
        ),
    ],
    AssetType.PRODUCT: [
        FieldDef("version", "Version", "text"),
        FieldDef(
            "lifecycle_stage",
            "Lifecycle Stage",
            "enum",
            choices=["planning", "development", "ga", "deprecated", "eol"],
        ),
        FieldDef(
            "type",
            "Product Type",
            "enum",
            choices=["internal", "free", "paid", "enterprise"],
        ),
        FieldDef("developer", "Developer", "text"),
        FieldDef("url", "URL", "text"),
        FieldDef("last_reviewed", "Last Reviewed", "date"),
        FieldDef("next_review_due", "Next Review Due", "date"),
    ],
    AssetType.SYSTEM: [
        FieldDef(
            "environment",
            "Environment",
            "enum",
            choices=["production", "staging", "development", "dr"],
        ),
        FieldDef(
            "hosting",
            "Hosting",
            "enum",
            choices=["on_prem", "cloud", "hybrid", "saas"],
        ),
        FieldDef("provider", "Provider", "text"),
        FieldDef("region", "Region", "text"),
        FieldDef(
            "criticality",
            "Criticality",
            "enum",
            choices=["critical", "high", "medium", "low"],
        ),
        FieldDef(
            "data_classification",
            "Data Classification",
            "enum",
            choices=["public", "internal", "confidential", "restricted"],
        ),
        FieldDef("url", "URL", "text"),
        FieldDef("last_reviewed", "Last Reviewed", "date"),
        FieldDef("next_review_due", "Next Review Due", "date"),
    ],
    AssetType.DEVICE: [
        FieldDef(
            "device_type",
            "Device Type",
            "enum",
            choices=[
                "server",
                "laptop",
                "desktop",
                "mobile",
                "networking_device",
                "iot_device",
            ],
        ),
        FieldDef("manufacturer", "Manufacturer", "text"),
        FieldDef("model", "Model", "text"),
        FieldDef("os", "Operating System", "text"),
        FieldDef("location", "Location", "text"),
        FieldDef("serial_number", "Serial Number", "text"),
        FieldDef("quantity", "Quantity", "integer"),
        FieldDef(
            "use",
            "Use",
            "enum",
            choices=[
                "employee_device",
                "production_server",
                "lab_device",
                "field_device",
                "retired",
                "unassigned",
                "testing",
            ],
        ),
        FieldDef("cmdb_link", "CMDB Link", "text"),
        FieldDef("last_reviewed", "Last Reviewed", "date"),
        FieldDef("next_review_due", "Next Review Due", "date"),
    ],
    AssetType.DATA_CATEGORY: [
        FieldDef(
            "classification",
            "Classification",
            "enum",
            choices=["public", "internal", "confidential", "restricted"],
        ),
        FieldDef("pii", "Contains PII", "boolean"),
        FieldDef("phi", "Contains PHI", "boolean"),
        FieldDef("regulated", "Regulated", "boolean"),
        FieldDef("regulations", "Regulations", "text"),
        FieldDef("retention_period", "Retention Period", "text"),
        FieldDef("storage_locations", "Storage Locations", "text"),
        FieldDef("last_reviewed", "Last Reviewed", "date"),
        FieldDef("next_review_due", "Next Review Due", "date"),
    ],
    AssetType.AUDIT: [
        FieldDef(
            "audit_type",
            "Audit Type",
            "enum",
            choices=[
                "external",
                "internal",
                "certification",
                "assessment",
                "policy",
                "security",
            ],
        ),
        FieldDef("framework", "Framework", "text"),
        FieldDef("auditor", "Auditor", "text"),
        FieldDef("start_date", "Start Date", "date"),
        FieldDef("end_date", "End Date", "date"),
        FieldDef("report_date", "Report Date", "date"),
        FieldDef(
            "result",
            "Result",
            "enum",
            choices=["pending", "pass", "pass_with_exceptions", "fail"],
        ),
    ],
    AssetType.REQUIREMENT: [
        FieldDef("framework", "Framework", "text"),
        FieldDef("reference_id", "Reference ID", "text"),
        FieldDef("category", "Category", "text"),
        FieldDef(
            "compliance_status",
            "Compliance Status",
            "enum",
            choices=[
                "compliant",
                "partially_compliant",
                "non_compliant",
                "not_assessed",
            ],
        ),
        FieldDef("due_date", "Due Date", "date"),
        FieldDef("last_assessed", "Last Assessed", "date"),
        FieldDef("evidence_url", "Evidence URL", "text"),
    ],
    AssetType.PROCESS: [
        FieldDef(
            "frequency",
            "Frequency",
            "enum",
            choices=[
                "continuous",
                "daily",
                "weekly",
                "monthly",
                "quarterly",
                "annually",
                "ad_hoc",
            ],
        ),
        FieldDef("last_executed", "Last Executed", "date"),
        FieldDef("next_execution", "Next Execution", "date"),
        FieldDef(
            "automation_level",
            "Automation Level",
            "enum",
            choices=["manual", "semi_automated", "fully_automated"],
        ),
        FieldDef("sla", "SLA", "text"),
        FieldDef(
            "type",
            "Process Type",
            "enum",
            choices=["operational", "management", "strategic", "support"],
        ),
    ],
    AssetType.INTELLECTUAL_PROPERTY: [
        FieldDef(
            "ip_type",
            "IP Type",
            "enum",
            choices=[
                "patent",
                "trade_secret",
                "copyright",
                "trademark",
                "proprietary_tech",
            ],
        ),
        FieldDef("filing_date", "Filing Date", "date"),
        FieldDef("expiry_date", "Expiry Date", "date"),
        FieldDef("registration_id", "Registration ID", "text"),
        FieldDef(
            "confidentiality",
            "Confidentiality",
            "enum",
            choices=["public", "internal", "confidential", "restricted"],
        ),
    ],
    AssetType.RISK: [
        FieldDef(
            "risk_category",
            "Risk Category",
            "enum",
            choices=[
                "security",
                "compliance",
                "operational",
                "financial",
                "reputational",
                "strategic",
            ],
        ),
        FieldDef("threat_source", "Threat Source", "text"),
        FieldDef("risk_framework", "Risk Framework", "text"),
        FieldDef(
            "severity",
            "Severity",
            "enum",
            choices=["critical", "high", "medium", "low"],
        ),
        FieldDef(
            "likelihood",
            "Likelihood",
            "enum",
            choices=["almost_certain", "likely", "possible", "unlikely", "rare"],
        ),
        FieldDef(
            "impact",
            "Impact",
            "enum",
            choices=[
                "catastrophic",
                "major",
                "moderate",
                "minor",
                "insignificant",
            ],
        ),
        FieldDef("inherent_risk_score", "Inherent Risk Score", "integer"),
        FieldDef("residual_risk_score", "Residual Risk Score", "integer"),
        FieldDef(
            "treatment",
            "Treatment",
            "enum",
            choices=["mitigate", "accept", "transfer", "avoid"],
        ),
        FieldDef("treatment_plan", "Treatment Plan", "text"),
        FieldDef(
            "control_effectiveness",
            "Control Effectiveness",
            "enum",
            choices=["effective", "partially_effective", "ineffective", "not_assessed"],
        ),
        FieldDef("risk_owner", "Risk Owner", "text"),
        FieldDef("identified_date", "Identified Date", "date"),
        FieldDef("last_reviewed", "Last Reviewed", "date"),
        FieldDef("review_date", "Next Review Due", "date"),
        FieldDef("accepted_by", "Accepted By", "text"),
        FieldDef("accepted_date", "Accepted Date", "date"),
        FieldDef("exception_approved", "Exception Approved", "date"),
        FieldDef("exception_due", "Exception Due", "date"),
        FieldDef("exception_approver", "Exception Approver", "text"),
    ],
    AssetType.ORGANIZATIONAL_UNIT: [
        FieldDef(
            "unit_type",
            "Unit Type",
            "enum",
            choices=["department", "team", "division", "subsidiary", "business_unit"],
        ),
        FieldDef("parent_unit", "Parent Unit", "text"),
        FieldDef("location", "Location", "text"),
        FieldDef("headcount", "Headcount", "integer"),
        FieldDef("cost_center", "Cost Center", "text"),
        FieldDef("ou_lead", "OU Lead", "text"),
        FieldDef("last_reviewed", "Last Reviewed", "date"),
        FieldDef("next_review_due", "Next Review Due", "date"),
    ],
}


def get_field_names(asset_type: AssetType) -> set[str]:
    """Return the set of custom field names for a given asset type."""
    return {f.name for f in CUSTOM_FIELDS.get(asset_type, [])}


def coerce_value(field_def: FieldDef, raw: str) -> object:
    """Coerce a raw string form value to the appropriate Python type."""
    if field_def.field_type == "boolean":
        return raw.lower() in ("true", "1", "yes", "on")
    if field_def.field_type == "integer":
        return int(raw) if raw else None
    return raw if raw else None
