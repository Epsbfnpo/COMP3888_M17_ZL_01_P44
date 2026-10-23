# Public CSEC results (historical snapshot)

These responses and `../public-summary.json` were produced by an early version
of the target (`system.version` 0.1.0) against the 11 public CSEC cases. They
record execution compatibility only: no private ground truth, decision policy
or false-accept/false-reject rates.

They have not been regenerated for 0.8.1 because the public bundles are not
stored in the repository. To refresh them, obtain the bundles and run, from
`program/`:

```bash
python3 -B check_public.py /path/to/public-bundles
```

The command rewrites this folder and `../public-summary.json`. Keep
`system.version` and `system.source_revision` from the new responses when
citing results.
