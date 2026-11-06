# Legacy reference extractor

`backend/legacy/extract_print_path.py` is the older square-test,
reference-only extractor. It is preserved for reproducibility, but new work
should use `application/experiment.py` and the shared
`backend/reconstruction/` pipeline from the `capture/` root.

To reproduce the old extraction:

```bash
python backend/legacy/extract_print_path.py /path/to/model.gcode \
  --output-dir outputs/post_analysis/another_print
```

Keep input reference files in `inputs/references/` and generated results in
`outputs/`. No recordings or source data were deleted during reorganization.
