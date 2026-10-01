# Release Desk

Maintain local release records and generate Markdown notes grouped as Added, Changed and Fixed. Requires Python 3.10+; only the standard library is used.

`python3 release_desk.py notes 1.1.0` renders the included second release. `python3 release_desk.py versions` lists sample versions in numeric order. `samples/next-changes.json` is a candidate change list; to try registration without changing the sample store, use `python3 release_desk.py --store .state/releases.json add 1.2.0 samples/next-changes.json` and query that same store.

Version identifiers have exactly three nonnegative numeric components, without prefixes, prerelease suffixes or leading zeros. Each release has at least one single-line change with a recognized category. Existing versions cannot be replaced. Category order is fixed and the input order within a category is retained. This version does not inspect Git or publish packages.

`python3 release_desk.py diff 1.0.0 1.1.0` compares two registered versions in the same store and prints a single-line JSON object with `baseVersion`, `targetVersion` and the `added`, `removed` and `unchanged` entries of the target relative to the base. Entries match on category plus trimmed, case-sensitive text; duplicates pair up by occurrence. Comparing a version with itself or a higher with a lower version is allowed. The comparison is read-only and never infers actual product changes.

The API is `ReleaseDesk.add`, `versions`, `notes`, `diff` and `import_releases`. JSON is used for command results and errors except `notes`, which prints Markdown. Invalid input or file errors exit 2. Run `python3 -B -m unittest -v` for rendering, sorting, validation, diff, import and CLI tests.

`python3 release_desk.py import batches.json` merges multiple releases from a local JSON file that maps versions to change arrays, using the same validation as `add` and the `--store` target. It prints a single-line JSON object with numerically sorted `imported` and `skipped` arrays: an existing version is skipped only when its normalized categories, texts and order match exactly, and any count, order, category or text difference is a conflict that aborts the whole import. An empty object is valid and changes nothing; a missing target store is treated as empty and is not created when every release is skipped. Validation, conflicts and file errors leave any existing store byte-for-byte untouched. The import never reads Git or publishes packages.
