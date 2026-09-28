---
name: keel-check
description: Read-only check of an installed Keel workflow harness against its map. Triggers on check, inspect, diagnose, or verify the Keel harness.
---
<!-- keel -->

# Keel check

Run `{{KEEL_WF}} check` and relay the table of installed, missing, and different items. This check reads the installation and must not edit files. To fix a missing or different item, reinstall from Keel's Workflow step. The work documents remain under `{{KB_PATH}}/raw/work/`.
