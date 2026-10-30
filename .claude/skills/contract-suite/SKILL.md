---
name: contract-suite
description: Add a port, a fake, or a real adapter to the chassis so that both pass the same contract suite. Use whenever a new external dependency or adapter appears.
---
# Contract suite: port, fake, adapter

Rule: every external dependency sits behind a port with a fake, and one suite checks both.

The steps are in `docs/guides/adding-a-port.md`, for a new port and for a new real adapter. Follow the guide. This skill does not repeat them.
