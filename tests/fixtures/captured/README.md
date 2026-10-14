# Captured pages

Raw bytes, straight from `support.sap.com`. Nothing in this directory is
hand-written, and nothing here is ever edited by hand: if SAP's markup changes,
a fresh capture is added and the older one stays as history.

```bash
scripts/capture-fixture.sh --month 2026-11          # the bulletin page
scripts/capture-fixture.sh --month 2026-11 --index  # the Security Notes & News index
```

The script refuses to store a page that does not parse as the month you asked
for, prints the sha256 prefix, and writes the file to
`captured/<YYYY-MM-DD>/`. It never overwrites a file unless `--overwrite` is
given.

`tests/integration/test_captured_pages.py` runs every `*.html` file in this
directory through the parser, so the first capture becomes a regression test
without touching any test code. Until a capture exists, that test reports
"no captures yet" and the reconstructed fixtures in `../` are the only
evidence — see [addendum 0010](../../../docs/addenda/0010-test-provenance-and-validation-status.md).

**Never present a reconstructed fixture as a capture.** The two kinds are
marked in `../README.md`, and a capture always wins over a reconstruction.
