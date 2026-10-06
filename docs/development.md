# Development

The source is at
[github.com/lsnewman/ha-tagsense](https://github.com/lsnewman/ha-tagsense).

```sh
cd tagsense
pip install -r requirements.txt pytest pyyaml jinja2
python -m pytest tests
python -m app.check ../test-frames --save-annotated /tmp/annotated
python -m app.sweep ../test-frames --ablation   # per-family margins and phantoms
```

- `app/check.py` runs the detector and sanity check over a folder of frames. It
  expects subfolders named `present/`, `absent/` and `smear/` (or `corrupt/`),
  and flags misses, phantoms and corrupt frames that were not rejected.
- `app/sweep.py` compares the tag families and the detector parameters:
  synthetic tags, real-geometry transplants, phantom counts and an ablation of
  each tuned parameter. The results are in
  [SPEC.md](https://github.com/lsnewman/ha-tagsense/blob/main/SPEC.md).
- Tests that use real frames read `../test-frames` or `$TAGSENSE_TESTDATA`, and
  are skipped if the folder is missing. Real frames are not committed.

**Releasing:** bump `version` in `tagsense/config.yaml`, or Home Assistant will
not offer the update.

## This documentation

The pages are Markdown in the repository's `docs/` folder, built with
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/) and published to
GitHub Pages by a workflow when `main` changes. To preview locally:

```sh
pip install mkdocs-material
mkdocs serve
```

The app's **Documentation** tab in Home Assistant shows `tagsense/DOCS.md`, a
short guide that links here.
