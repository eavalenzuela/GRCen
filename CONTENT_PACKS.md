# Compliance Content Packs

GRCen ships **content packs**: ready-to-install compliance baselines so a brand-new
organization can go from an empty register to a real, cross-mapped set of
frameworks, requirements, controls, and crosswalks **in one action** — without
standing up the external system-of-record (autocomply).

A pack is just a bundled **catalog** (the same shape `sync-catalog` consumes),
assembled from JSON *fragments* and projected into the asset graph through the
existing idempotent catalog sync. Each pack owns an `assets.source` /
`relationships.source` tag of the form `grcen-pack:<id>`, so a pack:

- **coexists** with anything you author by hand (`source IS NULL`) or sync from a
  system-of-record (`source = 'autocomply'`);
- **re-installs idempotently** — re-running upserts in place on
  `(organization_id, source, source_ref)` instead of duplicating;
- **uninstalls cleanly** — removing exactly the rows tagged with the pack's source.

## Installing a pack

### From the admin UI

`Admin → Content Packs` (`/admin/content-packs`, requires the `manage_users`
permission). Each pack shows its framework / requirement / control / crosswalk
counts and whether it's already installed. Buttons:

- **Install** — seed the pack (or **Re-sync** if already installed).
- **Preview (dry run)** — report exactly what *would* change, writing nothing.
- **Uninstall** — remove the pack's assets and relationships from this org.

### From the CLI

```console
$ grcen list-packs                       # what's available + install status
$ grcen install-pack common-baseline     # seed the flagship cross-mapped baseline
$ grcen install-pack iso27001-2022 --dry-run   # preview only
$ grcen install-pack iso27001-2022 --org acme  # target a specific org by slug
$ grcen install-pack common-baseline --uninstall  # remove it again
```

## Bundled packs

| id | what it seeds |
|---|---|
| `common-baseline` | **Flagship.** NIST CSF 2.0 + CIS Controls v8.1 + SOC 2 + ISO 27001:2022, a shared control library, and cross-framework crosswalks — a complete, cross-mapped starting point in one install. |
| `nist-csf-2.0` | NIST Cybersecurity Framework 2.0 (Functions → Categories → Subcategories). |
| `cis-controls-v8.1` | CIS Critical Security Controls v8.1 (18 Controls → Safeguards, with IG tags). |
| `soc2-tsc` | SOC 2 Trust Services Criteria (Common Criteria + optional categories). |
| `iso27001-2022` | ISO/IEC 27001:2022 Annex A (93 controls across four themes). |

> The `common-baseline` already contains all four frameworks cross-mapped
> together. Install it **or** the single-framework packs — not both — to avoid
> seeding a framework twice (each under a distinct source).

## How a pack maps into the graph

The same projection `catalog_sync` uses (see its module docstring):

```
framework            → asset(type=framework)
requirement          → asset(type=requirement)   parent_of  ← framework
control              → asset(type=control)        satisfies  → requirement
crosswalk            → requirement --cross_maps--> requirement   (cross-framework)
```

A requirement counts as **satisfied** (lights up coverage on `/frameworks`) when a
control `satisfies` it, or it has an outgoing `satisfied_by` / `implemented_by`
edge. The shared control library deliberately `satisfies` requirements across all
four frameworks, so one control closes the same gap everywhere at once.

A **crosswalk** records that a requirement in one framework is equivalent to a
requirement in another. It surfaces on the framework detail page in the
*Cross-framework* column, and gives the contract's `metadata.crosswalk`
relationship/confidence a first-class home in GRCen.

## Authoring a new pack

Packs are assembled from fragments under `src/grcen/content_packs/`:

```
content_packs/frameworks/<slug>.json    {"framework": {ref, name, requirements: [...]}}
content_packs/controls/<name>.json      {"controls": [{ref, name, satisfies: [...]}]}
content_packs/crosswalks/<name>.json    {"crosswalks": [{from, to, relationship}]}
```

**Framework fragment** — one framework and its requirements:

```json
{
  "framework": {
    "ref": "iso27001",
    "name": "ISO/IEC 27001:2022 (Annex A)",
    "description": "Information security controls (Annex A).",
    "metadata": { "version": "2022", "governing_body": "ISO/IEC" },
    "requirements": [
      {
        "ref": "iso27001:A.5.15",
        "name": "A.5.15 — Access control",
        "reference_id": "A.5.15",
        "category": "Organizational",
        "description": "Rules to control physical and logical access to information."
      }
    ]
  }
}
```

**Controls fragment** — reusable controls whose `satisfies` spans frameworks. The
optional `metadata.crosswalk` carries per-requirement relationship/confidence:

```json
{
  "controls": [
    {
      "ref": "CCF-06",
      "name": "Multi-factor authentication on remote access",
      "metadata": {
        "control_type": "preventive",
        "implementation": "technical",
        "crosswalk": {
          "soc2:CC6.1": { "relationship": "partial", "confidence": "high" },
          "iso27001:A.8.5": { "relationship": "equivalent", "confidence": "high" }
        }
      },
      "satisfies": ["soc2:CC6.1", "iso27001:A.8.5", "nist-csf-2.0:PR.AA-03", "cis-v8.1:6.3"]
    }
  ]
}
```

**Crosswalks fragment** — direct requirement-to-requirement equivalences across
frameworks (symmetric; list each pair once, never within one framework):

```json
{
  "crosswalks": [
    { "from": "iso27001:A.8.5", "to": "soc2:CC6.1",
      "relationship": "equivalent", "confidence": "high",
      "note": "MFA / strong authentication" }
  ]
}
```

`relationship` ∈ `equivalent | superset | subset | partial | related` (default
`related`). `confidence` ∈ `high | medium | low`.

Then register the pack in `src/grcen/services/content_packs.py` (`PACKS`) by naming
the fragments it composes. Validate with:

```console
$ grcen install-pack <id> --dry-run
```

Rules enforced by `catalog_sync.validate_catalog` (the install fails closed if any
break):

- every requirement `ref` is globally unique and prefixed with its framework slug;
- every `control.satisfies` ref and every crosswalk `from`/`to` ref resolves to a
  requirement **in the same catalog** (cross-pack references are not resolved);
- a crosswalk never maps a requirement to itself, and each pair appears once.

## Notes on provenance & licensing

Framework **structures** (identifier codes and short titles) are reproduced as
references for interoperability. NIST CSF 2.0 is U.S. public domain. SOC 2
(AICPA), ISO/IEC 27001:2022, and CIS Controls v8.1 identifiers and titles are the
property of their respective bodies; the packs carry short paraphrased intent, not
the standards' normative text. Each pack records its attribution, shown in the
admin UI.
