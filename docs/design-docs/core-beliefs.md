# Core Beliefs

## Repository as System of Record
- Product intent, architecture, execution plans, and operational guidance live in-repo.
- Documentation updates ship with code changes; drift is treated as a defect.

## Single Canonical Location per Knowledge Type
- Product requirements: `docs/product-specs`
- Architecture: `docs/design-docs/`
- Plans and tasks: `docs/exec-plans/`
- Runbooks/reference: `docs/references/`

## Plans Are Living Artifacts
- Complex or risky changes start with an active ExecPlan in `docs/exec-plans/active/`.
- Work progress, decisions, surprises, and outcomes are updated as implementation proceeds.
- Completed plans move to `docs/exec-plans/completed/` and remain searchable.

## Generated Knowledge Must Be Regenerable
- Generated docs under `docs/generated/` must declare source-of-truth files and regeneration commands.

## Documentation Quality Is Operational Quality
- Changes are not done until docs, quality score, and reliability/security notes are updated where relevant.
