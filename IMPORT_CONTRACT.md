# RecipeMonster ingredient catalog import contract

## Artifacts

The importer accepts RecipeMonster release archives named `ingredients_<language>.tar.gz` and `nutrition.tar.gz`. Every archive contains:

- exactly one CSV matching the archive name;
- `ATTRIBUTIONS.md`
- `LICENCE.md`

It rejects ZIP files, extra entries, paths, links, oversized files, wrong headers, duplicate IDs, duplicate taxonomy keys, names referencing unknown ingredient IDs, a missing supported-language name, unsupported nutrient units and invalid numeric values.

Each language CSV row is one equal localized name linked by ingredient ID and technical taxonomy key. The filename determines its language. It has no primary-name or alias semantics. Every supported language contains at least one name for every ingredient.

Each nutrition CSV row is an optional nutrition vector per 100 g for one ingredient. Ingredients without reliable nutrition data remain in every language catalog and have no nutrition row. `nutrition_source_label` is audit evidence. It is never a user-facing name.

## Preview

Only `system_admin` can upload artifacts. Preview stores the original archives in object storage, validates them in a durable job and reports:

- new global ingredients;
- exact existing taxonomy matches;
- names to add;
- nutrition values to add or update;
- ambiguous existing records;
- invalid rows.

Preview does not mutate the catalog. Its token binds actor, artifact checksum and current catalog generation.

## Apply

Apply requires the preview token and an idempotency key. A worker imports bounded batches into staging records and atomically publishes a complete generation.

Resolution order:

1. exact stable ingredient ID;
2. exact taxonomy key;
3. explicit administrator-approved target from preview;
4. new global ingredient.

Rows from every `ingredients_<language>.csv` become equal localized names. Existing moderator changes, activity and group assignments win. Import never changes private ingredients or private names and never relinks recipes by name alone.

The same artifact checksum is a no-op after a successful apply. A newer artifact may add names and replace nutrition evidence while preserving the global ingredient record and recipe references. Missing rows never cause automatic deletion.
