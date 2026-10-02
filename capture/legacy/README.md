# Legacy reference extractor

`extract_print_path.py` is the older square-test, reference-only extractor.
It is preserved for reproducibility, but new work should use `experiment.py`
and the shared `reconstruction/` pipeline from the capture root.

To reproduce the old extraction:

```bash
python legacy/extract_print_path.py /path/to/model.gcode --output-dir references/another_print
```

Existing reference artifacts remain in `../references/`. No recordings were
moved or deleted during workspace cleanup.
