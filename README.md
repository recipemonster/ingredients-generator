# RecipeMonster data

A generator for RecipeMonster's multilingual catalog of simple ingredients and nutrition data.

```shell
make release
```

This command downloads pinned sources, runs tests and validation, and builds a separate ingredient archive for each language and one nutrition archive.

## Sources and licenses

| Source | Data | License |
| --- | --- | --- |
| Open Food Facts | Ingredient identities and multilingual names | ODbL 1.0 |
| Anses-Ciqual | Nutrition data | Etalab Open License 2.0 |
| USDA FoodData Central SR Legacy | Nutrition data | CC0 1.0 / public domain |
| Fineli Open Data | Optional nutrition data | CC BY 4.0 |

The generator code is licensed under Apache-2.0. The generated catalog is not licensed solely under Apache-2.0. Source data licenses and attribution requirements apply, especially ODbL 1.0 for the database derived from Open Food Facts.

See [SOURCES.md](SOURCES.md) for source details. Exact versions, URLs, and checksums are defined in `sources.json`.
