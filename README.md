# Release Desk

Maintain local release records and generate Markdown notes grouped as Added, Changed and Fixed. Requires Python 3.10+; only the standard library is used.

`python3 release_desk.py notes 1.1.0` renders the included second release. `python3 release_desk.py versions` lists sample versions in numeric order. `samples/next-changes.json` is a candidate change list; to try registration without changing the sample store, use `python3 release_desk.py --store .state/releases.json add 1.2.0 samples/next-changes.json` and query that same store.

Version identifiers have exactly three nonnegative numeric components, without prefixes, prerelease suffixes or leading zeros. Each release has at least one single-line change with a recognized category. Existing versions cannot be replaced. Category order is fixed and the input order within a category is retained. This version does not inspect Git, publish packages or compare release contents automatically.

The API is `ReleaseDesk.add`, `versions` and `notes`. JSON is used for command results and errors except `notes`, which prints Markdown. Invalid input or file errors exit 2. Run `python3 -B -m unittest -v` for rendering, sorting, validation and CLI tests.
